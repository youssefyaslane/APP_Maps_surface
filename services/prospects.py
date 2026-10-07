"""Prospects du tableau de bord : filtres, liste, totaux, détection des panneaux
déjà posés, entreprises déjà équipées, statistiques de la page d'accueil."""

import os

import psycopg2

from services.comptes import _log_audit
from services.etat_base import _get_db_pool
from services.solar import SOLAR_CO2_T_PER_MWH, co2_evite_t, production_mwh
from services.toits import ROOF_LOOKUP_RADIUS_DEG

def _landing_stats():
    """Chiffres de la page d'accueil, comptés sur les toits distincts : une
    toiture partagée par plusieurs sociétés ne vaut qu'une installation."""
    fallback = {
        "prospects_avec_toit": "—", "toits_distincts": "—",
        "surface_totale": "—", "puissance_totale": "—", "batiments_base": "—",
        "sans_toit": "—", "top": [],
    }
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FILTER (WHERE roof_area_m2 IS NOT NULL AND equipped_at IS NULL),
                       count(DISTINCT roof_key) FILTER (WHERE equipped_at IS NULL)
                FROM companies
                """
            )
            avec_toit, distincts = cur.fetchone()
            cur.execute(
                """
                SELECT COALESCE(sum(area), 0), COALESCE(sum(kwc), 0) FROM (
                    SELECT DISTINCT ON (COALESCE(roof_key, 'c:' || id))
                           roof_area_m2 AS area, solar_kwc AS kwc
                    FROM companies WHERE roof_area_m2 IS NOT NULL AND equipped_at IS NULL
                    ORDER BY COALESCE(roof_key, 'c:' || id), solar_kwc DESC NULLS LAST
                ) t
                """
            )
            surface, kwc = cur.fetchone()
            cur.execute(
                "SELECT (SELECT count(*) FROM osm_buildings) + (SELECT count(*) FROM ms_buildings)"
            )
            batiments = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM companies WHERE roof_area_m2 IS NULL AND equipped_at IS NULL")
            sans_toit = cur.fetchone()[0]
            # Meilleures cibles du moment, une par toiture : deux societes d'un
            # meme immeuble ne doivent pas occuper deux lignes du classement.
            cur.execute(
                """
                SELECT DISTINCT ON (COALESCE(roof_key, 'c:' || id))
                       name, city, category, roof_area_m2, solar_kwc, phone, lon, lat
                FROM companies
                WHERE roof_area_m2 IS NOT NULL AND equipped_at IS NULL
                ORDER BY COALESCE(roof_key, 'c:' || id), solar_kwc DESC NULLS LAST
                """
            )
            top = sorted(cur.fetchall(), key=lambda r: -(r[4] or 0))[:5]
    except psycopg2.Error:
        conn.rollback()
        return fallback
    finally:
        pool.putconn(conn)

    def fr(n):
        return f"{int(n):,}".replace(",", "\u202f")

    return {
        "prospects_avec_toit": fr(avec_toit),
        "toits_distincts": fr(distincts),
        "surface_totale": fr(surface),
        "puissance_totale": fr(kwc),
        "batiments_base": fr(batiments),
        "sans_toit": fr(sans_toit),
        "top": [
            {
                "nom": r[0], "ville": r[1] or "—", "secteur": r[2] or "—",
                "surface": fr(r[3] or 0), "kwc": fr(r[4] or 0),
                "tel": r[5], "lon": r[6], "lat": r[7],
            }
            for r in top
        ],
    }


def _set_company_equipped(company_id, equipped, user_id):
    """Marque (ou démarque) une entreprise comme déjà équipée de panneaux.

    Elle sort alors de la liste des prospects, des statistiques et de l'export,
    sans être supprimée : un import la recréerait. Renvoie False si elle
    n'existe pas.
    """
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            # Re-marquer une entreprise déjà marquée garde la date et l'auteur
            # d'origine : c'est le premier constat qui compte.
            cur.execute(
                """
                UPDATE companies SET
                    equipped_at = CASE WHEN %(eq)s THEN COALESCE(equipped_at, now()) END,
                    equipped_by = CASE WHEN %(eq)s THEN COALESCE(equipped_by, %(user)s) END
                WHERE id = %(id)s
                RETURNING name
                """,
                {"eq": equipped, "user": user_id, "id": company_id},
            )
            row = cur.fetchone()
            if row is None:
                return False
            _log_audit(
                cur, user_id,
                "company_marked_equipped" if equipped else "company_unmarked_equipped",
                "companies", company_id, {"name": row[0]},
            )
    finally:
        pool.putconn(conn)
    return True


# Détection automatique des panneaux déjà posés (scripts/panneaux/classer_panneaux.sh) :
# confiance du modèle à partir de laquelle un toit s'affiche « avec panneaux ».
# Seul l'affichage en dépend ; toutes les confiances restent en base.
PV_SEUIL = float(os.environ.get("PV_SEUIL", "0.25"))


def _pv_detection(has_image, scores, dark_score=0.0):
    """Verdict affiché pour un toit ; None s'il n'a pas encore été analysé.

    « oui » : un panneau détecté ET assez sombre pour en être un (dark_score) ;
    « non » sinon. Une détection trop claire (tôle, verrière) compte donc comme
    « non » : la règle écarte ainsi la plupart des erreurs du modèle, au prix de
    manquer de vrais panneaux qui paraissent clairs sur l'image."""
    if has_image is None:
        return None
    if not has_image:
        return {"verdict": "pas d'image", "confiance": None, "nb": 0}
    confirme = (dark_score or 0.0) >= PV_SEUIL
    nb = sum(1 for sc in (scores or []) if sc >= PV_SEUIL) if confirme else 0
    return {"verdict": "oui" if confirme else "non", "confiance": round(dark_score or 0.0, 2), "nb": nb}


def _prospects_filter_clauses(min_kwc=None, city=None, category=None, search=None, alias="",
                              equipped=False, pv=None):
    """Clauses de filtrage du tableau de bord. `alias` préfixe les colonnes
    ("c." par exemple) quand la requête joint une autre table.

    Les entreprises déjà équipées de panneaux ne sont plus des prospects :
    elles sortent de la liste, des filtres et de l'export. `equipped=True`
    donne la vue inverse, celle d'où l'on peut les rétablir.

    `pv` filtre sur la détection automatique : "avec" (panneaux détectés et
    confirmés par la règle des pixels sombres) ou "sans" (toit analysé, rien
    de confirmé). Les toits pas encore
    analysés ou sans image ne sortent que sans ce filtre."""
    p = f"{alias}." if alias else ""
    clauses = [
        f"{p}solar_computed_at IS NOT NULL",
        f"{p}roof_area_m2 IS NOT NULL",
        f"{p}equipped_at IS NOT NULL" if equipped else f"{p}equipped_at IS NULL",
    ]
    params = []

    if min_kwc is not None:
        clauses.append(f"{p}solar_kwc >= %s")
        params.append(min_kwc)
    # Ville et catégorie viennent désormais de listes déroulantes alimentées par
    # /api/prospect_filters : la valeur est exacte. Un ILIKE '%...%' ferait
    # remonter les catégories englobantes (choisir « Fabricant » ramenait aussi
    # « Fabricant de meubles »), et le nombre affiché en face du choix ne
    # correspondrait plus au nombre de lignes obtenues.
    if city:
        clauses.append(f"lower(trim({p}city)) = lower(%s)")
        params.append(city.strip())
    if category:
        clauses.append(f"lower(trim({p}category)) = lower(%s)")
        params.append(category.strip())
    if search:
        clauses.append(f"({p}name ILIKE %s OR {p}address ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    if pv in ("avec", "sans"):
        # Qualifié par la table même sans alias : dans la sous-requête, un
        # roof_key nu désignerait celui de pv_detections, et la condition
        # serait toujours vraie.
        outer = alias or "companies"
        comparaison = ">=" if pv == "avec" else "<"
        clauses.append(
            "EXISTS (SELECT 1 FROM pv_detections pd "
            f"WHERE pd.roof_key = {outer}.roof_key AND pd.has_image AND pd.dark_score {comparaison} %s)"
        )
        params.append(PV_SEUIL)

    return clauses, params


def _count_prospects(min_kwc=None, city=None, category=None, search=None, equipped=False, pv=None):
    clauses, params = _prospects_filter_clauses(min_kwc, city, category, search, equipped=equipped, pv=pv)
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM companies WHERE {' AND '.join(clauses)}", params)
            return cur.fetchone()[0]
    finally:
        pool.putconn(conn)


def _query_prospects(min_kwc=None, city=None, category=None, search=None, limit=None, offset=None,
                     equipped=False, pv=None):
    """Entreprises avec leur potentiel solaire calculé, triées par puissance
    installable décroissante (alimente le tableau de bord commercial)."""
    clauses, params = _prospects_filter_clauses(
        min_kwc, city, category, search, alias="c", equipped=equipped, pv=pv
    )

    # shared_count : nombre d'entreprises rattachées au même toit. Un toit ne
    # s'équipe qu'une fois, donc un prospect qui partage le sien avec 22 autres
    # ne représente pas la surface entière.
    # Le décompte se fait sur TOUTE la table, pas sur le résultat filtré : sinon
    # un filtre par ville masquerait les colocataires des autres villes et
    # afficherait un toit partagé comme exclusif.
    sql = f"""
        WITH shared AS (
            SELECT roof_key, count(*) AS n
            FROM companies
            WHERE roof_key IS NOT NULL
            GROUP BY roof_key
        )
        SELECT c.id, c.name, c.category, c.address, c.city, c.phone, c.email, c.website,
               c.lon, c.lat, c.roof_area_m2, c.roof_source, c.solar_panels, c.solar_kwc,
               COALESCE(s.n, 1) AS shared_count, c.solar_yield_kwh_kwc,
               d.has_image, d.scores, d.dark_score
        FROM companies c
        LEFT JOIN shared s ON s.roof_key = c.roof_key
        LEFT JOIN pv_detections d ON d.roof_key = c.roof_key
        WHERE {' AND '.join(clauses)}
        ORDER BY c.solar_kwc DESC NULLS LAST
    """
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    if offset:
        sql += " OFFSET %s"
        params.append(offset)

    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)

    columns = [
        "id", "name", "category", "address", "city", "phone", "email", "website",
        "lon", "lat", "roof_area_m2", "roof_source", "solar_panels", "solar_kwc",
        "shared_count", "solar_yield_kwh_kwc",
    ]
    prospects = []
    for row in rows:
        prospect = dict(zip(columns, row[:len(columns)]))
        prospect["production_mwh"] = production_mwh(prospect["solar_kwc"], prospect["solar_yield_kwh_kwc"])
        prospect["co2_t"] = co2_evite_t(prospect["production_mwh"])
        prospect["pv"] = _pv_detection(*row[len(columns):])
        prospects.append(prospect)
    return prospects


# Seuil au-delà duquel un prospect est considéré comme une cible prioritaire
# (installation d'envergure, à traiter en premier par les commerciaux).
BIG_PROSPECT_KWC = 100


def _prospects_summary():
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    count(*),
                    count(*) FILTER (WHERE solar_computed_at IS NOT NULL),
                    count(*) FILTER (WHERE roof_area_m2 IS NOT NULL AND equipped_at IS NULL),
                    count(*) FILTER (WHERE equipped_at IS NOT NULL)
                FROM companies
                """
            )
            total, computed, with_roof, equipped = cur.fetchone()

            # Un toit ne s'equipe qu'une fois : agreger par entreprise comptait
            # plusieurs fois les batiments partages (mesure : 16,4% de
            # gonflement). Puissance, panneaux ET surface moyenne se calculent
            # donc sur les toits distincts — la moyenne est d'ailleurs annoncee
            # « par toit », pas par prospect.
            cur.execute(
                """
                SELECT COALESCE(sum(kwc), 0), COALESCE(sum(panels), 0),
                       count(*), COALESCE(avg(area), 0),
                       count(*) FILTER (WHERE kwc >= %s),
                       COALESCE(sum(kwc * rendement), 0) / 1000
                FROM (
                    SELECT DISTINCT ON (COALESCE(roof_key, 'company:' || id))
                           solar_kwc AS kwc, solar_panels AS panels, roof_area_m2 AS area,
                           solar_yield_kwh_kwc AS rendement
                    FROM companies
                    WHERE roof_area_m2 IS NOT NULL AND equipped_at IS NULL
                    ORDER BY COALESCE(roof_key, 'company:' || id), solar_kwc DESC NULLS LAST
                ) t
                """,
                (BIG_PROSPECT_KWC,),
            )
            total_kwc, total_panels, distinct_roofs, avg_area, big, total_mwh = cur.fetchone()
    finally:
        pool.putconn(conn)

    return {
        "total_companies": total,
        "computed": computed,
        "with_roof": with_roof,
        "distinct_roofs": distinct_roofs,
        "shared_companies": with_roof - distinct_roofs,
        "total_kwc": round(float(total_kwc), 1),
        "total_panels": int(total_panels),
        "total_production_mwh": round(float(total_mwh)),
        "total_co2_t": round(float(total_mwh) * SOLAR_CO2_T_PER_MWH),
        "avg_roof_area_m2": round(float(avg_area), 1),
        "big_prospects": big,
        "big_prospect_threshold": BIG_PROSPECT_KWC,
        "equipped": equipped,
    }


def _prospect_filter_values(min_kwc=None, city=None, category=None, search=None, equipped=False,
                            pv=None):
    """Villes et catégories réellement présentes parmi les prospects calculés.

    Les filtres étaient deux champs libres : sur 21 villes et plus de cent
    catégories, un commercial devait deviner l'orthographe exacte (« Mohammédia »
    accentué, « Âïn-Harrouda ») pour que le ILIKE trouve quelque chose. La liste
    ne propose que des valeurs qui rendront un résultat non vide.

    Chaque liste tient compte des autres filtres actifs, mais **pas du sien**.
    Sans les autres filtres, les nombres mentaient dès qu'un second filtre était
    posé : avec « Siège social » actif, la liste annonçait encore « Casablanca
    (1080) » pour 86 lignes réelles, et proposait six villes qui ne rendaient
    plus rien. En incluant son propre filtre, choisir une ville réduirait la
    liste des villes à cette seule ville et on ne pourrait plus en changer.
    """
    city_clauses, city_params = _prospects_filter_clauses(
        min_kwc, None, category, search, equipped=equipped, pv=pv
    )
    cat_clauses, cat_params = _prospects_filter_clauses(
        min_kwc, city, None, search, equipped=equipped, pv=pv
    )

    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            # Comptage des entreprises, et non des toits distincts : ce nombre
            # annonce la longueur de la liste que le choix va produire. Compter
            # les toitures donnerait « Siège social (94) » pour 97 lignes
            # affichées, l'écart venant des immeubles partagés. Les toitures
            # distinctes restent le bon décompte pour les cartes de
            # statistiques, qui parlent d'installations à vendre.
            cur.execute(
                f"""
                SELECT trim(city), count(*)
                FROM companies
                WHERE {" AND ".join(city_clauses)}
                  AND city IS NOT NULL AND trim(city) <> ''
                GROUP BY 1 ORDER BY 2 DESC, 1
                """,
                city_params,
            )
            cities = [{"value": r[0], "count": r[1]} for r in cur.fetchall()]

            cur.execute(
                f"""
                SELECT trim(category), count(*)
                FROM companies
                WHERE {" AND ".join(cat_clauses)}
                  AND category IS NOT NULL AND trim(category) <> ''
                GROUP BY 1 ORDER BY 2 DESC, 1
                """,
                cat_params,
            )
            categories = [{"value": r[0], "count": r[1]} for r in cur.fetchall()]
    finally:
        pool.putconn(conn)

    return {"cities": cities, "categories": categories}


def _query_unmatched_big_roofs(min_area_m2):
    """Grands toits (Microsoft ou IA/tracé manuel) sans aucune entreprise déjà
    reliée à proximité (ROOF_LOOKUP_RADIUS_DEG) : angles morts du flux normal,
    qui part des entreprises pour chercher leur toit plutôt que l'inverse.
    Utile pour repérer au Google Maps/scraper les prospects pas encore
    présents dans companies malgré un grand bâtiment détecté."""
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT polygon, centroid_lon, centroid_lat, area_m2, 'ms-buildings' AS source
                FROM ms_buildings m
                WHERE area_m2 >= %(min_area)s
                  AND NOT EXISTS (
                    SELECT 1 FROM companies c
                    WHERE c.lon BETWEEN m.centroid_lon - %(radius)s AND m.centroid_lon + %(radius)s
                      AND c.lat BETWEEN m.centroid_lat - %(radius)s AND m.centroid_lat + %(radius)s
                  )
                UNION ALL
                SELECT polygon, centroid_lon, centroid_lat, area_m2, source
                FROM ia_segments s
                WHERE area_m2 >= %(min_area)s
                  AND NOT EXISTS (
                    SELECT 1 FROM companies c
                    WHERE c.lon BETWEEN s.centroid_lon - %(radius)s AND s.centroid_lon + %(radius)s
                      AND c.lat BETWEEN s.centroid_lat - %(radius)s AND s.centroid_lat + %(radius)s
                  )
                ORDER BY area_m2 DESC
                """,
                {"min_area": min_area_m2, "radius": ROOF_LOOKUP_RADIUS_DEG},
            )
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)

    results = []
    for polygon, centroid_lon, centroid_lat, area_m2, source in rows:
        lons = [p[0] for p in polygon]
        lats = [p[1] for p in polygon]
        results.append(
            {
                "min_lon": min(lons),
                "max_lon": max(lons),
                "min_lat": min(lats),
                "max_lat": max(lats),
                "area_m2": area_m2,
                "source": source,
            }
        )
    return results
