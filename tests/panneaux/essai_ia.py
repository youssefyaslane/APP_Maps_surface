"""Essai d'une IA en ligne sur les images de tests/panneaux/img : panneaux solaires, oui ou non ?

Usage, depuis le dossier du projet :
    venv/bin/python tests/panneaux/essai_ia.py              (OpenAI, par défaut)
    venv/bin/python tests/panneaux/essai_ia.py --ia gemini

Chaque image part telle quelle avec la même question que le script complet
(scripts/panneaux/detect_panels_gemini.py). Si le nom du fichier contient
« avec_panneaux » ou « sans_panneaux », la réponse est comparée à l'attendu.

Les clés sont lues dans l'environnement, sinon dans le fichier .env du projet :
OPENAI_API_KEY pour OpenAI, GEMINI_API_KEY pour Gemini. Elles partent dans un
en-tête, jamais dans l'URL, et ne sont jamais affichées.
OPENAI_MODEL / GEMINI_MODEL choisissent un autre modèle.
"""
import argparse
import base64
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from scripts.panneaux.detect_panels_gemini import (API as GEMINI_API, DEFAULT_MODEL as GEMINI_DEFAULT,
                                          PROMPT, SCHEMA as GEMINI_SCHEMA, parse_verdict, verdict_from_text)

IMG_DIR = os.path.join(ROOT, "tests", "panneaux", "img")
ATTEMPTS = 3
RETRY_WAIT_S = 20

OPENAI_API = "https://api.openai.com/v1"
# Premier modèle de cette liste proposé par le compte, si OPENAI_MODEL n'est pas fixé.
OPENAI_PREFERENCES = ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini", "gpt-5", "gpt-4.1", "gpt-4o")
OPENAI_SCHEMA = {
    "type": "object",
    "properties": {
        "panneaux": {"type": "string", "enum": ["oui", "non", "incertain"]},
        "confiance": {"type": "integer"},
        "raison": {"type": "string"},
    },
    "required": ["panneaux", "confiance", "raison"],
    "additionalProperties": False,
}


class Retry(Exception):
    """Panne passagère : quota par minute, serveur surchargé, pas de réponse."""


def read_key(name):
    key = os.environ.get(name, "").strip()
    if key:
        return key
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                var, sep, value = line.strip().partition("=")
                if sep and var.strip() == name:
                    key = value.strip().strip('"').strip("'")
                    if key:
                        return key
    sys.exit(f"Clé introuvable : ajoutez {name}=... dans le fichier .env du projet.")


def image_b64(path):
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        return mime, base64.b64encode(f.read()).decode()


def _error_message(resp):
    try:
        return resp.json().get("error", {}).get("message", "")[:250]
    except ValueError:
        return resp.text[:250]


# ---------- OpenAI ----------

def openai_model(key):
    wanted = os.environ.get("OPENAI_MODEL", "").strip()
    resp = requests.get(f"{OPENAI_API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if resp.status_code == 401:
        sys.exit("Clé OpenAI refusée (401) : vérifiez OPENAI_API_KEY.")
    resp.raise_for_status()
    ids = {m["id"] for m in resp.json().get("data", [])}
    if wanted:
        if wanted not in ids:
            sys.exit(f"Le modèle {wanted} n'est pas proposé à ce compte OpenAI.")
        return wanted
    for m in OPENAI_PREFERENCES:
        if m in ids:
            return m
    gpt = sorted(i for i in ids if i.startswith("gpt"))[:20]
    sys.exit(f"Aucun modèle connu trouvé. Modèles « gpt » du compte : {', '.join(gpt)}. "
             f"Choisissez-en un avec OPENAI_MODEL=...")


def ask_openai(model, key, path):
    mime, data = image_b64(path)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}", "detail": "high"}},
        ]}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "verdict_toit", "strict": True, "schema": OPENAI_SCHEMA}},
    }
    resp = requests.post(f"{OPENAI_API}/chat/completions", json=body, timeout=180,
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    if resp.status_code == 429:
        try:
            code = resp.json().get("error", {}).get("code")
        except ValueError:
            code = None
        if code == "insufficient_quota":
            sys.exit("Compte OpenAI sans crédit : ajoutez du crédit sur platform.openai.com (Billing).")
        raise Retry("limite de débit OpenAI")
    if resp.status_code >= 500:
        raise Retry(f"OpenAI surchargé ({resp.status_code})")
    if not resp.ok:
        sys.exit(f"OpenAI refuse la requête ({resp.status_code}) : {_error_message(resp)}")
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        text = None
    return verdict_from_text(text)


# ---------- Gemini ----------

def ask_gemini(model, key, path):
    mime, data = image_b64(path)
    body = {
        "contents": [{"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": mime, "data": data}}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": GEMINI_SCHEMA},
    }
    resp = requests.post(f"{GEMINI_API}/models/{model}:generateContent", json=body, timeout=180,
                         headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    if resp.status_code == 429 or resp.status_code >= 500:
        raise Retry("quota Gemini atteint" if resp.status_code == 429 else f"Google surchargé ({resp.status_code})")
    if not resp.ok:
        sys.exit(f"Gemini refuse la requête ({resp.status_code}) : {_error_message(resp)}")
    return parse_verdict(resp.json())


# ---------- essai ----------

def ask_with_retries(ask, model, key, path):
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return ask(model, key, path)
        except requests.RequestException as exc:
            problem = f"pas de réponse ({exc.__class__.__name__})"
        except Retry as exc:
            problem = str(exc)
        if attempt < ATTEMPTS:
            print(f"   {problem}, nouvel essai dans {RETRY_WAIT_S} s ({attempt}/{ATTEMPTS})", flush=True)
            time.sleep(RETRY_WAIT_S)
    print(f"   {problem} après {ATTEMPTS} essais : relancez le script un peu plus tard.")
    return None


def main():
    p = argparse.ArgumentParser(description="Panneaux solaires : oui ou non, sur les images de tests/panneaux/img.")
    p.add_argument("--ia", choices=("openai", "gemini"), default="openai")
    a = p.parse_args()
    if a.ia == "openai":
        key = read_key("OPENAI_API_KEY")
        model, ask = openai_model(key), ask_openai
    else:
        key = read_key("GEMINI_API_KEY")
        model, ask = os.environ.get("GEMINI_MODEL", GEMINI_DEFAULT), ask_gemini

    images = sorted(f for f in os.listdir(IMG_DIR) if f.lower().endswith((".jpg", ".jpeg", ".png")))
    if not images:
        sys.exit(f"Aucune image dans {IMG_DIR}")
    print(f"IA : {a.ia} — modèle : {model} — {len(images)} image(s)\n")
    justes = compares = 0
    for name in images:
        print(f"• {name}", flush=True)
        start = time.time()
        v = ask_with_retries(ask, model, key, os.path.join(IMG_DIR, name))
        if v is None:
            continue
        attendu = "oui" if "avec_panneaux" in name else "non" if "sans_panneaux" in name else None
        line = f"   Réponse : {v['panneaux'].upper()} (confiance {v['confiance']} %, {time.time() - start:.0f} s)"
        if attendu:
            compares += 1
            justes += v["panneaux"] == attendu
            line += "  ✓ juste" if v["panneaux"] == attendu else f"  ✗ attendu : {attendu}"
        print(line)
        print(f"   Raison : {v['raison']}\n")
    if compares:
        print(f"Résultat : {justes}/{compares} juste(s).")


if __name__ == "__main__":
    main()
