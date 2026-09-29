"""Étape 1 de la détection des panneaux : l'image satellite de chaque toit, en flux.

Lancé par scripts/panneaux/classer_panneaux.sh, dans un conteneur de l'application (qui
lit la base). Aucune image n'est écrite sur le disque : chaque toit part sur la
sortie standard, vers le classificateur YOLO, dans ce format :
    une ligne JSON {"cle", "qui", "contour", "image"}
    puis, si "image" est vrai, 4 octets (taille, gros-boutiste) et le JPEG.
Les messages d'avancement vont sur la sortie d'erreur.

Par défaut, seuls les toits pas encore dans pv_detections sont envoyés (reprise
après interruption). --tout les renvoie tous.
Usage : python extraire_toits_panneaux.py [--tout] [--workers 4]
"""
import argparse
import io
import json
import os
import struct
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append("/app")  # code de l'application dans son conteneur ; en fin de liste pour ne rien masquer
from services import db  # noqa: E402
from services import schema  # noqa: E402
from roof_images import fetch_roof, polygon_of  # noqa: E402


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def load_roofs(tout):
    """[(roof_key, qui, polygone)] du plus puissant au plus petit."""
    conn = db.connect()
    try:
        with conn, conn.cursor() as cur:
            schema.create_pv_detections(cur)
            deja = "" if tout else "AND roof_key NOT IN (SELECT roof_key FROM pv_detections)"
            cur.execute(f"""
                SELECT roof_key, max(solar_kwc) AS kwc,
                       array_agg(name ORDER BY solar_kwc DESC NULLS LAST, id) AS noms
                FROM companies WHERE roof_key IS NOT NULL {deja}
                GROUP BY roof_key ORDER BY kwc DESC NULLS LAST, roof_key
            """)
            rows = cur.fetchall()
            roofs = []
            for key, _kwc, noms in rows:
                qui = ", ".join(noms[:2]) + (f" (+{len(noms) - 2})" if len(noms) > 2 else "")
                roofs.append((key, qui, polygon_of(cur, key)))
            return roofs
    finally:
        conn.close()


def main(tout, workers):
    roofs = [r for r in load_roofs(tout) if r[2]]
    log(f"{len(roofs)} toit(s) à analyser.")
    out = sys.stdout.buffer
    session = requests.Session()

    def work(item):
        key, qui, polygon = item
        img, outline = fetch_roof(polygon, session)
        data = None
        if img is not None:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            data = buf.getvalue()
        return key, qui, outline, data

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, r) for r in roofs]
        for fut in as_completed(futures):
            try:
                key, qui, outline, data = fut.result()
            except requests.RequestException as exc:
                log(f"  image Esri indisponible ({exc.__class__.__name__}) : toit repris au prochain lancement")
                continue
            header = {"cle": key, "qui": qui, "contour": outline, "image": data is not None}
            out.write(json.dumps(header, ensure_ascii=False).encode() + b"\n")
            if data is not None:
                out.write(struct.pack(">I", len(data)))
                out.write(data)
            out.flush()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--tout", action="store_true", help="réanalyser aussi les toits déjà en base")
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    try:
        main(a.tout, a.workers)
    except BrokenPipeError:
        sys.exit(1)  # le classificateur s'est arrêté : pas la peine de continuer
