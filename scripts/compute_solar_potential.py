"""Calcule le potentiel solaire de chaque entreprise en base : cherche le toit
sous ses coordonnées (OSM, toits détectés/tracés, puis Microsoft), estime le
nombre de panneaux installables et la puissance correspondante, et stocke le
résultat dans la table `companies`.

Usage: python -m scripts.compute_solar_potential [--all|--retry-empty|--production]

Par défaut, ne traite que les entreprises pas encore calculées (reprise
possible après interruption). Avec --all, recalcule tout. Avec --retry-empty,
retraite aussi les entreprises déjà calculées mais sans toit trouvé (rattrape
les échecs réseau Overpass ponctuels, sans le coût d'un --all complet).

Il enregistre ensuite le productible PVGIS (kWh par kWc et par an) des
entreprises qui n'en ont pas encore, d'où se déduit leur production annuelle.
Avec --production, seul ce productible est recalculé, pour toutes : à lancer
après un changement d'inclinaison, d'orientation ou de pertes
(SOLAR_TILT_DEG…).
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from services import db, pvgis
from services.geometrie import _point_in_polygon
from services.solar import estimate_solar
from services.etat_base import _init_db
from services.toits import ROOF_LOOKUP_RADIUS_DEG, _find_roof_at_point, _query_ia_segments

# Recherches de toit menées en parallèle (l'appel Overpass domine le temps de
# calcul). Volontairement modéré pour ne pas se faire limiter par les miroirs.
MAX_WORKERS = 6
BATCH_SIZE = 50


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
        radius = ROOF_LOOKUP_RADIUS_DEG
        bbox = (lat - radius, lon - radius, lat + radius, lon + radius)
        if not any(
            _point_in_polygon(lon, lat, c["polygon"])
            for c in _query_ia_segments(bbox)
        ):
            orphans.append(company_id)

    if orphans:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE companies SET
                    roof_area_m2 = NULL, roof_source = NULL, roof_key = NULL,
                    solar_panels = NULL, solar_kwc = NULL, solar_computed_at = NULL
                WHERE id = ANY(%s)
                """,
                (orphans,),
            )
        print(f"{len(orphans)} entreprise(s) réinitialisée(s) (toit supprimé depuis le dernier calcul).")

    return len(orphans)


def compute(recompute_all=False, retry_empty=False):
    _init_db()  # garantit la présence des colonnes de potentiel solaire

    conn = db.connect()
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
                    executor.map(lambda c: _find_roof_at_point(c[2], c[3]), batch)
                )

            # Écritures séquentielles : une connexion psycopg2 n'est pas
            # partageable entre threads.
            for (company_id, name, lon, lat), roof in zip(batch, roofs):
                idx += 1
                area = roof["area_m2"] if roof else None
                source = roof["source"] if roof else None
                roof_key = roof["roof_key"] if roof else None
                n_panels, kwc = estimate_solar(area)
                if roof:
                    found += 1

                with conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE companies SET
                            roof_area_m2 = %s,
                            roof_source = %s,
                            roof_key = %s,
                            solar_panels = %s,
                            solar_kwc = %s,
                            solar_computed_at = now()
                        WHERE id = %s
                        """,
                        (area, source, roof_key, n_panels, kwc, company_id),
                    )

            elapsed = time.time() - started
            rate = idx / elapsed if elapsed else 0
            remaining = (len(companies) - idx) / rate if rate else 0
            print(
                f"  {idx}/{len(companies)} traitées — {found} toit(s) trouvé(s) "
                f"({elapsed:.0f}s écoulées, ~{remaining / 60:.0f} min restantes)"
            )
        print(f"Terminé : {found}/{len(companies)} entreprises avec un toit identifié.")
        remplir_productibles(conn, tout=recompute_all)
    finally:
        conn.close()


def remplir_productibles(conn, tout=False):
    """Productible PVGIS des entreprises. Un PVGIS injoignable n'annule pas
    le calcul des toits, déjà enregistré : il suffira de relancer."""
    try:
        pvgis.remplir(conn, tout=tout)
    except pvgis.PvgisIndisponible as exc:
        print(f"⚠ PVGIS injoignable ({exc}) : production non calculée, relancer plus tard.")


if __name__ == "__main__":
    if "--production" in sys.argv[1:]:
        _init_db()
        connexion = db.connect()
        try:
            remplir_productibles(connexion, tout=True)
        finally:
            connexion.close()
    else:
        compute(
            recompute_all="--all" in sys.argv[1:],
            retry_empty="--retry-empty" in sys.argv[1:],
        )
