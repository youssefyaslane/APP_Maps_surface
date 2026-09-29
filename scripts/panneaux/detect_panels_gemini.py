"""Demande à Gemini (Google AI Studio, offre gratuite) si chaque toit porte déjà
des panneaux photovoltaïques : oui, non ou incertain.

Usage (dans le conteneur) :
    python -m scripts.panneaux.detect_panels_gemini [--limit N] [--dry-run] [--pause S]
    python -m scripts.panneaux.detect_panels_gemini --entreprises 5282,646   (essai sur des toits choisis)
    python -m scripts.panneaux.detect_panels_gemini --export chemin.csv

Chaque toit est traité une fois, du plus puissant au plus petit. Le verdict est
écrit aussitôt dans CACHE_DIR/pv_gemini.jsonl : on peut arrêter le script quand
on veut, il reprend là où il s'était arrêté. Quand le quota gratuit du jour est
épuisé, il attend et recommence tout seul, jusqu'à avoir vu tous les toits.

Rien n'est écrit dans la base : les verdicts restent dans ce fichier tant qu'on
n'a pas décidé comment les utiliser (bouton « déjà équipée »).

La clé vient de GEMINI_API_KEY et part dans un en-tête, jamais dans l'URL ni
dans les journaux.
"""
import argparse
import base64
import csv
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

from scripts.panneaux.roof_images import draw_outline, fetch_roof, is_placeholder, polygon_of  # noqa: F401

CACHE_DIR = os.environ.get("CACHE_DIR") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS = os.path.join(CACHE_DIR, "pv_gemini.jsonl")
PREVIEW_DIR = os.path.join(CACHE_DIR, "pv_gemini_apercu")
API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3-flash-preview"
MAX_SIDE = 768        # au-delà, l'image coûte plus de quota sans montrer mieux un panneau
MAX_WAIT_S = 3600     # attente maximale entre deux essais quand le quota est épuisé

PROMPT = (
    "Image satellite vue du dessus. Le toit à examiner est entouré en rouge. "
    "Ce toit porte-t-il des panneaux solaires PHOTOVOLTAÏQUES (rectangles sombres, "
    "bleu-noir, alignés en rangées) ?\n"
    "Ne compte pas comme panneaux : les lanterneaux ou puits de lumière (rectangles "
    "clairs), la tôle peinte en bleu, les chauffe-eau solaires isolés, les ombres. "
    "Ignore les toits voisins, hors du contour rouge.\n"
    "Réponds « incertain » si l'image est floue, absente ou si tu ne peux pas trancher.\n"
    "Donne ta confiance en pourcentage : un entier de 0 à 100 (100 = certain)."
)

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "panneaux": {"type": "STRING", "enum": ["oui", "non", "incertain"]},
        "confiance": {"type": "INTEGER"},
        "raison": {"type": "STRING"},
    },
    "required": ["panneaux", "confiance"],
}


class QuotaExhausted(Exception):
    pass


# ---------- image du toit ----------

def roof_image(polygon):
    """Image Esri autour du toit, contour en rouge, réduite pour le quota.
    None si Esri n'a pas d'image à ce zoom."""
    img, outline = fetch_roof(polygon)
    if img is None:
        return None
    img = draw_outline(img, outline)
    scale = min(1.0, MAX_SIDE / max(img.size))
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)))
    return img


# ---------- Gemini ----------

def _headers():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("GEMINI_API_KEY est absente : créez une clé sur https://aistudio.google.com, "
                 "ajoutez GEMINI_API_KEY=... dans .env, puis recréez le conteneur.")
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


def check_model(model):
    resp = requests.get(f"{API}/models", headers=_headers(), params={"pageSize": 1000}, timeout=30)
    if resp.status_code in (400, 401, 403):
        sys.exit(f"Clé Gemini refusée ({resp.status_code}) : vérifiez GEMINI_API_KEY.")
    resp.raise_for_status()
    names = {m["name"].split("/", 1)[-1] for m in resp.json().get("models", [])}
    if model not in names:
        flash = sorted(n for n in names if "flash" in n)
        sys.exit(f"Le modèle {model} n'est pas proposé à cette clé. Modèles « flash » disponibles : "
                 f"{', '.join(flash) or 'aucun'}. Choisissez-en un avec GEMINI_MODEL=...")
    # Figurer dans la liste ne suffit pas : Google y laisse des modèles fermés
    # aux nouveaux comptes (gemini-2.5-flash répond 404). Un appel minimal le dit.
    resp = requests.post(f"{API}/models/{model}:generateContent", headers=_headers(),
                         json={"contents": [{"parts": [{"text": "Réponds : ok"}]}]}, timeout=120)
    if not resp.ok and resp.status_code != 429 and resp.status_code < 500:
        reason = resp.json().get("error", {}).get("message", "")[:300] if resp.content else ""
        sys.exit(f"Le modèle {model} refuse les appels ({resp.status_code}) : {reason} "
                 f"Choisissez-en un autre avec GEMINI_MODEL=...")


def parse_verdict(payload):
    """Extrait {panneaux, confiance, raison} d'une réponse Gemini ; « incertain » si illisible."""
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        text = None
    return verdict_from_text(text)


def verdict_from_text(text):
    """Le JSON renvoyé par le modèle, quel qu'il soit, ramené à un verdict sûr :
    tout ce qui n'est pas lisible devient « incertain », jamais « oui »."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError
    except (TypeError, ValueError):
        return {"panneaux": "incertain", "confiance": 0, "raison": "réponse illisible"}
    verdict = data.get("panneaux")
    if verdict not in ("oui", "non", "incertain"):
        verdict = "incertain"
    try:
        confiance = max(0, min(100, int(data.get("confiance", 0))))
    except (TypeError, ValueError):
        confiance = 0
    return {"panneaux": verdict, "confiance": confiance, "raison": str(data.get("raison", ""))[:300]}


def ask_gemini(model, img):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    body = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
        ]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    }
    resp = requests.post(f"{API}/models/{model}:generateContent", headers=_headers(), json=body, timeout=120)
    if resp.status_code == 429:
        raise QuotaExhausted()
    if resp.status_code >= 500:
        resp.raise_for_status()  # panne passagère côté Google : l'appelant réessaie
    if not resp.ok:
        # Modèle retiré, clé refusée, requête rejetée : réessayer ne changerait
        # rien, et boucler en silence ferait croire que le script travaille.
        try:
            reason = resp.json().get("error", {}).get("message", "")[:300]
        except ValueError:
            reason = resp.text[:300]
        sys.exit(f"Gemini refuse la requête ({resp.status_code}) avec le modèle {model} : {reason}")
    return parse_verdict(resp.json())


# ---------- reprise ----------

def load_done():
    done = {}
    if os.path.exists(RESULTS):
        with open(RESULTS, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    done[row["roof_key"]] = row
                except (ValueError, KeyError):
                    continue  # ligne coupée par un arrêt brutal : le toit sera refait
    return done


def append_result(row):
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def roofs_to_check(cur):
    """Un toit par roof_key (un toit partagé n'est examiné qu'une fois), du plus
    puissant au plus petit : les plus gros prospects ont leur verdict d'abord."""
    cur.execute("""
        SELECT roof_key, max(solar_kwc) AS kwc
        FROM companies WHERE roof_key IS NOT NULL
        GROUP BY roof_key ORDER BY kwc DESC NULLS LAST, roof_key
    """)
    return cur.fetchall()


# ---------- commandes ----------

def run(limit, dry_run, pause, model, company_ids=None):
    from base import db

    if not dry_run:
        check_model(model)
    os.makedirs(PREVIEW_DIR if dry_run else CACHE_DIR, exist_ok=True)
    done = load_done()
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            todo = [(k, kwc) for k, kwc in roofs_to_check(cur) if k not in done]
            if company_ids:
                cur.execute("SELECT roof_key FROM companies WHERE id = ANY(%s)", (company_ids,))
                wanted = {r[0] for r in cur.fetchall()}
                todo = [(k, kwc) for k, kwc in todo if k in wanted]
            if limit:
                todo = todo[:limit]
            print(f"{len(done)} toit(s) déjà vus, {len(todo)} à traiter.", flush=True)
            wait = 60
            for i, (key, kwc) in enumerate(todo, 1):
                poly = polygon_of(cur, key)
                if not poly:
                    continue
                try:
                    img = roof_image(poly)
                except requests.RequestException as exc:
                    print(f"[{i}/{len(todo)}] {key} : image Esri indisponible ({exc.__class__.__name__}), repris plus tard", flush=True)
                    continue
                if dry_run:
                    if img is not None:
                        img.save(os.path.join(PREVIEW_DIR, key.replace(":", "_") + ".jpg"))
                    print(f"[{i}/{len(todo)}] {key} : {'image prête' if img else 'pas d’image Esri'} {img.size if img else ''}", flush=True)
                    continue
                if img is None:
                    verdict = {"panneaux": "incertain", "confiance": 0, "raison": "pas d'image Esri à ce zoom"}
                else:
                    while True:
                        try:
                            verdict = ask_gemini(model, img)
                            wait = 60
                            break
                        except QuotaExhausted:
                            print(f"Quota Gemini atteint : nouvel essai dans {wait // 60} min.", flush=True)
                            time.sleep(wait)
                            wait = min(wait * 2, MAX_WAIT_S)
                        except requests.RequestException as exc:
                            # Serveur Google surchargé (503) ou délai dépassé : passager.
                            print(f"{key} : Gemini indisponible ({exc.__class__.__name__}), "
                                  f"nouvel essai dans {wait // 60} min", flush=True)
                            time.sleep(wait)
                            wait = min(wait * 2, MAX_WAIT_S)
                row = {"roof_key": key, "kwc": kwc, **verdict, "modele": model,
                       "date": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                append_result(row)
                print(f"[{i}/{len(todo)}] {key} : {verdict['panneaux']} ({verdict['confiance']} %)", flush=True)
                time.sleep(pause)
    finally:
        conn.close()


def export(path):
    from base import db

    done = load_done()
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, city, roof_key, roof_area_m2, solar_kwc
                FROM companies WHERE roof_key IS NOT NULL ORDER BY solar_kwc DESC NULLS LAST
            """)
            rows = cur.fetchall()
    finally:
        conn.close()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "entreprise", "ville", "toit", "surface_m2", "kwc", "panneaux", "confiance", "raison"])
        for cid, name, city, key, area, kwc in rows:
            v = done.get(key, {})
            w.writerow([cid, name, city or "", key, round(area or 0), kwc,
                        v.get("panneaux", "pas encore vu"), v.get("confiance", ""), v.get("raison", "")])
    seen = sum(1 for r in rows if r[3] in done)
    print(f"{len(rows)} entreprise(s) exportée(s) vers {path}, dont {seen} avec un verdict.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--limit", type=int, default=0, help="ne traiter que N toits (essai)")
    p.add_argument("--dry-run", action="store_true", help="préparer les images sans appeler Gemini")
    p.add_argument("--pause", type=float, default=7.0, help="secondes entre deux appels (quota gratuit)")
    p.add_argument("--export", metavar="CSV", help="écrire les verdicts par entreprise dans un CSV")
    p.add_argument("--entreprises", help="identifiants d'entreprises séparés par des virgules (essai ciblé)")
    a = p.parse_args(argv)
    if a.export:
        export(a.export)
    else:
        ids = [int(x) for x in a.entreprises.split(",")] if a.entreprises else None
        run(a.limit, a.dry_run, a.pause, os.environ.get("GEMINI_MODEL", DEFAULT_MODEL), ids)


if __name__ == "__main__":
    main()
