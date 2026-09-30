"""Étape 3 de la détection des panneaux : les verdicts dans la base.

Enregistre chaque toit dans pv_detections : toutes ses confiances, pas un
oui/non figé. Le tableau de bord en déduit l'affichage avec le seuil PV_SEUIL.
Rejouable : un toit déjà enregistré est mis à jour, jamais dupliqué.

Usage :
  python importer_panneaux.py --flux            verdicts JSON lus sur l'entrée standard
                                                (sortie de classer_panneaux_yolo.py --flux),
                                                écrits par lots de 10 au fil de l'eau
  python importer_panneaux.py <resultats.jsonl> depuis un fichier
Affiche ensuite le bilan de la base.
"""
import json
import os
import sys

sys.path.append("/app")  # code de l'application dans son conteneur ; en fin de liste pour ne rien masquer
from psycopg2.extras import execute_values  # noqa: E402

from services import db  # noqa: E402
from services import schema  # noqa: E402

MODELE = "Cyrille37/solar-panels-IGN-bdortho (yolo26l_solar_panel-s4)"
LOT = 10
PV_SEUIL = float(os.environ.get("PV_SEUIL", "0.25"))


def ligne_toit(texte):
    """(roof_key, has_image, scores, confiance_sombre) ou None pour une ligne à ignorer."""
    try:
        r = json.loads(texte)
        cle = r["cle"]
    except (ValueError, KeyError, TypeError):
        return None  # ligne coupée par un arrêt brutal
    if ":" not in cle:  # une image d'essai, pas un toit de la base
        return None
    scores = sorted((float(s) for s in r.get("scores", [])), reverse=True)
    return cle, r.get("panneaux") != "pas d'image", scores, float(r.get("confiance_sombre", 0.0))


def lire(chemin):
    """{roof_key: (has_image, scores)} d'un fichier de verdicts."""
    toits = {}
    with open(chemin, encoding="utf-8") as f:
        for texte in f:
            t = ligne_toit(texte)
            if t:
                toits[t[0]] = t[1:]
    return toits


def enregistrer(cur, toits):
    execute_values(
        cur,
        """
        INSERT INTO pv_detections (roof_key, has_image, scores, max_score, dark_score, model)
        VALUES %s
        ON CONFLICT (roof_key) DO UPDATE SET
            has_image = EXCLUDED.has_image, scores = EXCLUDED.scores, max_score = EXCLUDED.max_score,
            dark_score = EXCLUDED.dark_score, model = EXCLUDED.model, detected_at = now()
        """,
        [(cle, has_image, scores, max(scores, default=0.0), sombre, MODELE) for cle, has_image, scores, sombre in toits],
        template="(%s, %s, %s::real[], %s, %s, %s)",
    )


def bilan(cur):
    cur.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE has_image AND dark_score >= %(s)s),
               count(*) FILTER (WHERE has_image AND dark_score < %(s)s),
               count(*) FILTER (WHERE NOT has_image)
        FROM pv_detections
        """,
        {"s": PV_SEUIL},
    )
    total, oui, non, sans_image = cur.fetchone()
    cur.execute("SELECT count(DISTINCT roof_key) FROM companies WHERE roof_key IS NOT NULL")
    toits = cur.fetchone()[0]
    cur.execute(
        """
        SELECT c.name, c.city, round(d.dark_score::numeric, 2)
        FROM pv_detections d JOIN companies c ON c.roof_key = d.roof_key
        WHERE d.has_image AND d.dark_score >= %s
        ORDER BY d.dark_score DESC, c.solar_kwc DESC NULLS LAST LIMIT 15
        """,
        (PV_SEUIL,),
    )
    exemples = cur.fetchall()
    print("\n================ BILAN (base) ================")
    print(f"Toits analysés          : {total} / {toits}")
    print(f"  avec panneaux (≥ {PV_SEUIL}) : {oui}")
    print(f"  sans panneaux         : {non}")
    print(f"  sans image Esri       : {sans_image}")
    if exemples:
        print("\nEntreprises sur un toit détecté équipé (les plus sûres) :")
        for nom, ville, conf in exemples:
            print(f"  {conf}  {nom}  ({ville or 'ville inconnue'})")
    print("\nTableau de bord : colonne « Déjà équipé ? » et filtre « Panneaux ».")


def main(source):
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            schema.create_pv_detections(cur)
            conn.commit()
            if source == "--flux":
                lot, total = [], 0
                for texte in sys.stdin:
                    t = ligne_toit(texte)
                    if t:
                        lot.append(t)
                    if len(lot) >= LOT:
                        enregistrer(cur, lot)
                        conn.commit()  # un arrêt en cours de route ne perd qu'un lot
                        total += len(lot)
                        lot = []
                if lot:
                    enregistrer(cur, lot)
                    conn.commit()
                    total += len(lot)
                print(f"{total} toit(s) enregistré(s) dans la base.")
            else:
                toits = lire(source)
                if not toits:
                    sys.exit(f"Aucun verdict dans {source}")
                enregistrer(cur, [(k, *v) for k, v in toits.items()])
                conn.commit()
                print(f"{len(toits)} toit(s) enregistré(s) dans la base.")
            bilan(cur)
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1] if sys.argv[1] == "--flux" else os.path.abspath(sys.argv[1]))
