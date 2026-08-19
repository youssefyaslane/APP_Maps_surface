"""Calcule le potentiel solaire de chaque entreprise en base : cherche le toit
sous ses coordonnées (OSM, toits détectés/tracés, puis Microsoft), estime le
nombre de panneaux installables et la puissance correspondante, et stocke le
résultat dans la table `companies`.

Usage: python compute_solar_potential.py [--all|--retry-empty]

Par défaut, ne traite que les entreprises pas encore calculées (reprise
possible après interruption). Avec --all, recalcule tout. Avec --retry-empty,
retraite aussi les entreprises déjà calculées mais sans toit trouvé (rattrape
les échecs réseau Overpass ponctuels, sans le coût d'un --all complet).
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg2

# Évite le préchargement des villes et du modèle IA au moment de l'import
# d'app.py (inutile ici, on ne se sert que de la recherche de toit).
os.environ.setdefault("WERKZEUG_RUN_MAIN", "true")

import app as flask_app

# Mêmes hypothèses que l'estimation affichée côté carte (static/app.js).
SOLAR_PANEL_AREA_M2 = 1.7
SOLAR_PANEL_POWER_W = 400
SOLAR_USABLE_ROOF_FRACTION = 0.7

# Recherches de toit menées en parallèle (l'appel Overpass domine le temps de
# calcul). Volontairement modéré pour ne pas se faire limiter par les miroirs.
MAX_WORKERS = 6
BATCH_SIZE = 50

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://maps:maps@localhost:5432/maps")


def estimate_solar(area_m2):
    if not area_m2 or area_m2 <= 0:
        return 0, 0.0
    n_panels = int(area_m2 * SOLAR_USABLE_ROOF_FRACTION / SOLAR_PANEL_AREA_M2)
    return n_panels, round(n_panels * SOLAR_PANEL_POWER_W / 1000, 2)


def reset_orphans(conn):
    """Réinitialise les entreprises dont le toit IA/tracé a été supprimé depuis
    le dernier calcul (elles repassent alors dans la file de traitement)."""
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, lon, lat FROM companies
            WHERE roof_source IN ('ia-segmentation', 'manual-trace')
            """
        )
        rows = cur.fetchall()

    orphans = []
    for company_id, lon, lat in rows:
        radius = flask_app.ROOF_LOOKUP_RADIUS_DEG
        bbox = (lat - radius, lon - radius, lat + radius, lon + radius)
        if not any(
            flask_app._point_in_polygon(lon, lat, c["polygon"])
            for c in flask_app._query_ia_segments(bbox)
        ):
            orphans.append(company_id)

    if orphans:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE companies SET
                    roof_area_m2 = NULL, roof_source = NULL,
                    solar_panels = NULL, solar_kwc = NULL, solar_computed_at = NULL
                WHERE id = ANY(%s)
                """,
                (orphans,),
            )
        print(f"{len(orphans)} entreprise(s) réinitialisée(s) (toit supprimé depuis le dernier calcul).")

    return len(orphans)


def compute(recompute_all=False, retry_empty=False):
    flask_app._init_db()  # garantit la présence des colonnes de potentiel solaire

    conn = psycopg2.connect(DATABASE_URL)
    if recompute_all:
        where = ""
    elif retry_empty:
        # Reprend les entreprises déjà calculées mais sans toit trouvé : un
        # échec réseau Overpass ponctuel pendant le calcul en masse peut
        # laisser une entreprise "sans toit" alors qu'un bâtiment OSM existe
        # bien (retrouvé au clic manuel plus tard via /api/company_roof, qui
        # retente l'appel). Beaucoup moins coûteux qu'un --all complet.
        where = "WHERE solar_computed_at IS NULL OR (solar_computed_at IS NOT NULL AND roof_area_m2 IS NULL)"
    else:
        where = "WHERE solar_computed_at IS NULL"

    reset_orphans(conn)

    try:
        with conn, conn.cursor() as cur:
            # Tri géographique : les entreprises proches tombent dans le même
            # paquet et se partagent les tuiles OSM déjà en cache.
            cur.execute(
                f"SELECT id, name, lon, lat FROM companies {where} "
                "ORDER BY round(lat / 0.03), round(lon / 0.03), id"
            )
            companies = cur.fetchall()

        print(f"{len(companies)} entreprise(s) à traiter.")
        found = 0
        started = time.time()
        idx = 0

        # Les entreprises voisines partagent la même tuile OSM : les traiter par
        # paquets géographiques maximise les réutilisations du cache.
        for batch_start in range(0, len(companies), BATCH_SIZE):
            batch = companies[batch_start : batch_start + BATCH_SIZE]

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                roofs = list(
                    executor.map(lambda c: flask_app._find_roof_at_point(c[2], c[3]), batch)
                )

            # Écritures séquentielles : une connexion psycopg2 n'est pas
            # partageable entre threads.
            for (company_id, name, lon, lat), roof in zip(batch, roofs):
                idx += 1
                area = roof["area_m2"] if roof else None
                source = roof["source"] if roof else None
                n_panels, kwc = estimate_solar(area)
                if roof:
                    found += 1

                with conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE companies SET
                            roof_area_m2 = %s,
                            roof_source = %s,
                            solar_panels = %s,
                            solar_kwc = %s,
                            solar_computed_at = now()
                        WHERE id = %s
                        """,
                        (area, source, n_panels, kwc, company_id),
                    )

            elapsed = time.time() - started
            rate = idx / elapsed if elapsed else 0
            remaining = (len(companies) - idx) / rate if rate else 0
            print(
                f"  {idx}/{len(companies)} traitées — {found} toit(s) trouvé(s) "
                f"({elapsed:.0f}s écoulées, ~{remaining / 60:.0f} min restantes)"
            )
    finally:
        conn.close()

    print(f"Terminé : {found}/{len(companies)} entreprises avec un toit identifié.")


if __name__ == "__main__":
    compute(
        recompute_all="--all" in sys.argv[1:],
        retry_empty="--retry-empty" in sys.argv[1:],
    )
