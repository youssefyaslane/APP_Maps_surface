"""Toits et bâtiments : segments IA et tracés, bâtiments Microsoft et OpenStreetMap
(avec cache par tuile), recherche du toit sous une entreprise, préchauffage des villes."""

import json
import math
import os
import threading
import time
from concurrent.futures import as_completed, ThreadPoolExecutor

import psycopg2
import requests

from services.comptes import _log_audit
from services.etat_base import _get_db_pool
from services.geometrie import (
    _distance_to_polygon_m,
    _point_in_polygon,
    _polygon_area_m2,
    _polygon_centroid,
)
from services.reglages import CACHE_DIR
from services.solar import estimate_solar as _estimate_solar

DISK_CACHE_PATH = os.path.join(CACHE_DIR, "tile_cache.json")
_disk_cache_lock = threading.Lock()

# Toits détectés par IA (clic simple ou zone), persistés dans PostgreSQL pour
# rester affichés d'une session à l'autre, et supprimables par l'utilisateur.
# L'adresse de la base et son schéma sont réglés dans services/db.py.
# Distance de dédoublonnage (en degrés) : deux détections dont le centroïde
# est plus proche que ça sont considérées comme le même toit.
IA_SEGMENT_DEDUP_DEG = 0.00005


def _store_ia_segment(polygon, area_m2, source="ia-segmentation", created_by=None):
    """Sauvegarde un toit (détecté par IA ou tracé manuellement), ou renvoie
    l'entrée existante si déjà stocké au même endroit."""
    lon, lat = _polygon_centroid(polygon)
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, polygon, area_m2, source FROM ia_segments
                WHERE centroid_lon BETWEEN %s AND %s AND centroid_lat BETWEEN %s AND %s
                LIMIT 1
                """,
                (
                    lon - IA_SEGMENT_DEDUP_DEG,
                    lon + IA_SEGMENT_DEDUP_DEG,
                    lat - IA_SEGMENT_DEDUP_DEG,
                    lat + IA_SEGMENT_DEDUP_DEG,
                ),
            )
            existing = cur.fetchone()
            if existing:
                return {"id": existing[0], "polygon": existing[1], "area_m2": existing[2], "source": existing[3]}

            cur.execute(
                """
                INSERT INTO ia_segments (polygon, area_m2, centroid_lon, centroid_lat, source, created_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (json.dumps(polygon), area_m2, lon, lat, source, created_by),
            )
            new_id = cur.fetchone()[0]
            _log_audit(
                cur, created_by, "segment_created", "ia_segments", new_id,
                {"source": source, "area_m2": area_m2},
            )
            # On relève seulement qui est concerné ; le rattachement se fait
            # après le commit, comme à la suppression.
            affected = _companies_inside_polygon(cur, polygon, include_nearby=True)
            stored = {"id": new_id, "polygon": polygon, "area_m2": area_m2, "source": source}
    finally:
        pool.putconn(conn)

    # Hors transaction : le segment est committé, donc _find_roof_at_point le
    # voit. Rejouer l'arbitrage complet plutôt qu'affecter d'office ce nouveau
    # polygone — sans quoi une détection IA écraserait un tracé manuel déjà
    # posé au même endroit, à rebours de l'ordre de priorité.
    if affected:
        _recompute_solar_for_companies(affected)
    return stored


def _query_ia_segments(bbox):
    south, west, north, east = bbox
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, polygon, area_m2, source FROM ia_segments
                WHERE centroid_lat BETWEEN %s AND %s AND centroid_lon BETWEEN %s AND %s
                """,
                (south, north, west, east),
            )
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)
    return [{"id": r[0], "polygon": r[1], "area_m2": r[2], "source": r[3]} for r in rows]


def _delete_ia_segment(seg_id, deleted_by=None):
    """Supprime un toit détecté/tracé, puis recalcule le potentiel solaire des
    entreprises qui s'appuyaient dessus (un autre toit peut exister dessous).

    La suppression était jusqu'ici possible sans authentification ni trace :
    n'importe qui sur le réseau pouvait effacer un toit tracé à la main, sans
    que personne ne sache ni qui ni quand. `_log_audit` s'exécute dans la même
    transaction que le DELETE : la suppression et sa trace sont commises
    ensemble, ou aucune des deux ne l'est."""
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT polygon, area_m2, source FROM ia_segments WHERE id = %s", (seg_id,)
            )
            row = cur.fetchone()
            if row is None:
                return False
            polygon, area_m2, source = row

            cur.execute("DELETE FROM ia_segments WHERE id = %s", (seg_id,))
            _log_audit(
                cur, deleted_by, "segment_deleted", "ia_segments", seg_id,
                {"source": source, "area_m2": area_m2},
            )
            affected = _companies_inside_polygon(cur, polygon, only_computed=True, include_nearby=True)
    finally:
        pool.putconn(conn)

    # Hors transaction : la suppression est committée, donc la recherche de toit
    # ne verra plus le segment effacé.
    if affected:
        _recompute_solar_for_companies(affected)
    return True


def _recompute_solar_for_companies(company_ids):
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id, lon, lat FROM companies WHERE id = ANY(%s)", (company_ids,))
            targets = cur.fetchall()
    finally:
        pool.putconn(conn)

    for company_id, lon, lat in targets:
        roof = _find_roof_at_point(lon, lat)
        area = roof["area_m2"] if roof else None
        source = roof["source"] if roof else None
        roof_key = roof["roof_key"] if roof else None
        n_panels, kwc = _estimate_solar(area)

        conn = pool.getconn()
        try:
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
        finally:
            pool.putconn(conn)


def _companies_inside_polygon(cur, polygon, only_computed=False, include_nearby=False):
    """Entreprises dont les coordonnées tombent dans ce polygone (ou à moins de
    ROOF_NEARBY_RADIUS_M s'il faut retrouver celles liées via le rattrapage de
    _find_roof_at_point, par ex. avant de supprimer un toit)."""
    # Marge en degrés pour la présélection SQL, large pour couvrir le rayon de
    # rattrapage en plus du polygone lui-même (~111km par degré de latitude).
    margin = (ROOF_NEARBY_RADIUS_M / 111000.0) if include_nearby else 0.0
    lons = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]

    extra = " AND solar_computed_at IS NOT NULL" if only_computed else ""
    cur.execute(
        f"""
        SELECT id, lon, lat FROM companies
        WHERE lon BETWEEN %s AND %s AND lat BETWEEN %s AND %s{extra}
        """,
        (min(lons) - margin, max(lons) + margin, min(lats) - margin, max(lats) + margin),
    )
    rows = cur.fetchall()

    if not include_nearby:
        return [company_id for company_id, lon, lat in rows if _point_in_polygon(lon, lat, polygon)]

    return [
        company_id
        for company_id, lon, lat in rows
        if _distance_to_polygon_m(lon, lat, polygon) <= ROOF_NEARBY_RADIUS_M
    ]


def _query_companies(bbox):
    south, west, north, east = bbox
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, category, address, city, phone, email, website, rating,
                       lon, lat, roof_area_m2, solar_kwc, equipped_at
                FROM companies
                WHERE lat BETWEEN %s AND %s AND lon BETWEEN %s AND %s
                """,
                (south, north, west, east),
            )
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)
    return [
        {
            "id": r[0],
            "name": r[1],
            "category": r[2],
            "address": r[3],
            "city": r[4],
            "phone": r[5],
            "email": r[6],
            "website": r[7],
            "rating": r[8],
            "lon": r[9],
            "lat": r[10],
            "roof_area_m2": r[11],
            "solar_kwc": r[12],
            "has_roof": r[11] is not None,
            "equipped": r[13] is not None,
        }
        for r in rows
    ]


MS_BUILDINGS_QUERY_LIMIT = 3000


def _query_ms_buildings(bbox):
    south, west, north, east = bbox
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, polygon, area_m2 FROM ms_buildings
                WHERE centroid_lat BETWEEN %s AND %s AND centroid_lon BETWEEN %s AND %s
                LIMIT %s
                """,
                (south, north, west, east, MS_BUILDINGS_QUERY_LIMIT),
            )
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)
    return [{"id": r[0], "polygon": r[1], "area_m2": r[2]} for r in rows]


# Rayon de recherche (en degrés) pour trouver les toits candidats autour
# d'une entreprise avant le test point-dans-polygone (~300m).
ROOF_LOOKUP_RADIUS_DEG = 0.003

# Rayon de rattrapage (mètres) : le point GPS d'une entreprise (souvent
# l'entrée ou le trottoir, pas le toit) tombe fréquemment juste à côté du
# polygone réel. Si aucun toit ne contient le point, on prend le plus proche
# dans ce rayon plutôt que de renoncer. 20m capte l'imprécision GPS courante
# sans risquer d'attribuer le bâtiment du voisin.
ROOF_NEARBY_RADIUS_M = 20.0


def _find_roof_at_point(lon, lat):
    """Cherche un toit (ia_segments, OSM ou ms_buildings) contenant ce point.

    Ordre de priorité, du plus fiable au moins fiable :

    1. `manual-trace` — quelqu'un a dessiné ce contour à la main, en regardant
       l'image satellite. C'est le seul cas où un humain a tranché, et il l'a
       souvent fait précisément parce que les autres sources étaient absentes
       ou fausses : le laisser derrière OSM annulerait cette correction.
    2. `ia-segmentation` — segmentation déclenchée au clic sur ce bâtiment
       précis, donc vérifiée de visu au moment de la détection.
    3. `osm` — contour cartographié, fiable mais parfois ancien ou absent.
    4. `ms-buildings` — détection automatique en masse, jamais relue.

    Si aucun polygone ne contient exactement le point (le GPS d'une entreprise
    pointe souvent l'entrée ou le trottoir, pas le toit), reprend le bâtiment
    le plus proche dans un rayon de ROOF_NEARBY_RADIUS_M, même ordre de
    priorité des sources en cas de distances comparables."""
    bbox = (
        lat - ROOF_LOOKUP_RADIUS_DEG,
        lon - ROOF_LOOKUP_RADIUS_DEG,
        lat + ROOF_LOOKUP_RADIUS_DEG,
        lon + ROOF_LOOKUP_RADIUS_DEG,
    )

    # roof_key identifie le polygone lui-même, pas seulement sa surface : sans
    # lui, deux entreprises sous le même toit deviennent deux lignes qui ne
    # savent pas qu'elles parlent du même bâtiment, et le potentiel total est
    # compté deux fois.
    osm_candidates = [
        {
            "area_m2": f["properties"]["area_m2"],
            "source": "osm",
            "polygon": f["geometry"]["coordinates"][0],
            "holes": f["geometry"]["coordinates"][1:],
            "roof_key": f"osm:{f['id']}",
        }
        for f in _cached_osm_buildings_at(bbox)
    ]
    # Les deux sortent de la même table, mais pas de la même main : un tracé
    # manuel est un arbitrage humain, une segmentation reste une detection.
    segments = [
        {"area_m2": c["area_m2"], "source": c["source"], "polygon": c["polygon"], "roof_key": f"ia:{c['id']}"}
        for c in _query_ia_segments(bbox)
    ]
    manual_candidates = [c for c in segments if c["source"] == "manual-trace"]
    ia_candidates = [c for c in segments if c["source"] != "manual-trace"]

    ms_candidates = [
        {"area_m2": c["area_m2"], "source": "ms-buildings", "polygon": c["polygon"], "roof_key": f"ms:{c['id']}"}
        for c in _query_ms_buildings(bbox)
    ]

    by_priority = (manual_candidates, ia_candidates, osm_candidates, ms_candidates)

    for candidates in by_priority:
        for c in candidates:
            if not _point_in_polygon(lon, lat, c["polygon"]):
                continue
            # Un point tombant dans une cour intérieure n'est pas sur le toit.
            if any(_point_in_polygon(lon, lat, h) for h in c.get("holes") or []):
                continue
            return c

    # Départage stable : deux bâtiments superposés à distance quasi identique
    # (immeubles redessinés, doublons de saisie) doivent toujours donner le même
    # toit, sinon la surface d'un prospect change d'un recalcul à l'autre.
    best, best_dist = None, ROOF_NEARBY_RADIUS_M
    for candidates in by_priority:
        for c in sorted(candidates, key=lambda x: x["roof_key"]):
            d = _distance_to_polygon_m(lon, lat, c["polygon"])
            if d < best_dist:
                best, best_dist = c, d
        if best is not None:
            # Une source plus prioritaire a déjà un candidat proche : on
            # s'arrête là plutôt que de préférer une source moins fiable
            # simplement parce qu'elle serait légèrement plus proche.
            break

    return best


# Miroirs Overpass accessibles depuis ce réseau (certains miroirs comme
# overpass-api.de/overpass.kumi.systems sont bloqués par le pare-feu local).
OVERPASS_URLS = [
    "https://lz4.overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

CITIES = {
    "casablanca": {"label": "Casablanca", "center": (33.5731, -7.5898), "zoom": 16},
    "rabat": {"label": "Rabat", "center": (34.0209, -6.8417), "zoom": 16},
    "marrakech": {"label": "Marrakech", "center": (31.6295, -7.9811), "zoom": 16},
    "fes": {"label": "Fès", "center": (34.0331, -5.0003), "zoom": 16},
    "tanger": {"label": "Tanger", "center": (35.7595, -5.8340), "zoom": 16},
    "agadir": {"label": "Agadir", "center": (30.4278, -9.5981), "zoom": 16},
}

# Bounding box approximative du Maroc (south, west, north, east)
MOROCCO_BBOX = (27.6, -13.2, 35.95, -0.9)

# Cache indexé par tuile de grille : une fois une tuile chargée, les
# pans/zooms qui restent dans la même tuile ne re-sollicitent pas Overpass.
# Doublement mémoire (accès rapide) + disque (survit aux redémarrages).
_cache = {}
# Les empreintes de bâtiments ne bougent pas d'une semaine à l'autre. Un TTL de
# 30 min obligeait un recalcul en masse (~1h45 sur 1600 entreprises) à
# re-télécharger plusieurs fois les mêmes tuiles Overpass.
CACHE_TTL_SECONDS = 7 * 24 * 3600
TILE_SIZE_DEG = 0.03


def _tile_key_str(tile_key):
    return f"{tile_key[0]}:{tile_key[1]}"


def _load_disk_cache():
    if not os.path.exists(DISK_CACHE_PATH):
        return
    try:
        with open(DISK_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        now = time.time()
        for key_str, entry in raw.items():
            if now - entry["ts"] >= CACHE_TTL_SECONDS:
                continue
            lat_str, lon_str = key_str.split(":")
            _cache[(int(lat_str), int(lon_str))] = entry
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        pass


def _save_disk_cache():
    with _disk_cache_lock:
        serializable = {_tile_key_str(k): v for k, v in _cache.items()}
        tmp_path = DISK_CACHE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f)
        os.replace(tmp_path, DISK_CACHE_PATH)


def _tile_keys_for_bbox(bbox):
    south, west, north, east = bbox
    keys = []
    lat = math.floor(south / TILE_SIZE_DEG)
    while lat * TILE_SIZE_DEG < north:
        lon = math.floor(west / TILE_SIZE_DEG)
        while lon * TILE_SIZE_DEG < east:
            keys.append((lat, lon))
            lon += 1
        lat += 1
    return keys


def _tile_bbox(lat_idx, lon_idx):
    return (
        lat_idx * TILE_SIZE_DEG,
        lon_idx * TILE_SIZE_DEG,
        (lat_idx + 1) * TILE_SIZE_DEG,
        (lon_idx + 1) * TILE_SIZE_DEG,
    )


_osm_tile_locks_guard = threading.Lock()
_osm_tile_locks = {}


def _osm_tile_lock(tile_key):
    """Verrou par tuile : évite que plusieurs recherches simultanées sur la
    même zone déclenchent chacune leur propre appel Overpass."""
    with _osm_tile_locks_guard:
        return _osm_tile_locks.setdefault(tile_key, threading.Lock())


def _cached_osm_tile(tile_key):
    """Bâtiments OSM d'une tuile, via le cache partagé avec la carte (évite un
    appel Overpass par entreprise lors des calculs en masse)."""
    cached = _cache.get(tile_key)
    if cached and time.time() - cached["ts"] < CACHE_TTL_SECONDS:
        return cached["features"]

    with _osm_tile_lock(tile_key):
        # Une autre requête a pu remplir la tuile pendant l'attente du verrou.
        cached = _cache.get(tile_key)
        if cached and time.time() - cached["ts"] < CACHE_TTL_SECONDS:
            return cached["features"]

        try:
            osm_data = _fetch_overpass(_tile_bbox(*tile_key))
        except (RuntimeError, requests.RequestException):
            return []

        features = _build_geojson(osm_data)
        _cache[tile_key] = {"ts": time.time(), "features": features}

    return features


def _cached_osm_buildings_at(bbox):
    """Bâtiments OSM couvrant la bbox donnée.

    Lit la table `osm_buildings`, alimentée hors ligne par
    import_osm_buildings.py depuis un extrait Geofabrik. Repli sur Overpass tant
    que la table n'a pas été remplie, pour qu'une installation neuve reste
    fonctionnelle sans import préalable.
    """
    rows = _query_osm_buildings(bbox)
    if rows:
        return rows

    features = []
    for tile_key in _tile_keys_for_bbox(bbox):
        features.extend(_cached_osm_tile(tile_key))
    return features


def _query_osm_buildings(bbox):
    """Bâtiments OSM en base, au format GeoJSON attendu par la cascade de toits.

    L'identifiant renvoyé est celui d'OpenStreetMap, identique à ce que donnait
    Overpass : les roof_key déjà calculés (`osm:1111649392`) restent valides.
    """
    south, west, north, east = bbox
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT osm_id, polygon, area_m2, name, building_type, levels, holes
                FROM osm_buildings
                WHERE centroid_lat BETWEEN %s AND %s AND centroid_lon BETWEEN %s AND %s
                LIMIT %s
                """,
                (south, north, west, east, MS_BUILDINGS_QUERY_LIMIT),
            )
            rows = cur.fetchall()
    except psycopg2.errors.UndefinedTable:
        conn.rollback()
        return []  # table absente tant que l'import n'a pas été lancé
    finally:
        pool.putconn(conn)

    return [
        {
            "type": "Feature",
            "id": r[0],
            # Les cours intérieures sont rendues comme trous du polygone : la
            # carte les laisse voir, et la surface les exclut déjà.
            "geometry": {"type": "Polygon", "coordinates": [r[1]] + (r[6] or [])},
            "properties": {
                "id": r[0],
                "name": r[3],
                "building_type": r[4],
                "levels": r[5],
                "area_m2": round(r[2], 1),
            },
        }
        for r in rows
    ]


def _bbox_within_morocco(bbox):
    south, west, north, east = bbox
    m_south, m_west, m_north, m_east = MOROCCO_BBOX
    return not (east < m_west or west > m_east or north < m_south or south > m_north)


def _fetch_overpass(bbox):
    south, west, north, east = bbox
    query = f"""
    [out:json][timeout:25];
    (
      way["building"]({south},{west},{north},{east});
      relation["building"]({south},{west},{north},{east});
    );
    out body;
    >;
    out skel qt;
    """

    headers = {
        "User-Agent": "maroc-buildings-map/1.0 (Flask demo app)",
        "Accept": "application/json",
    }

    last_error = None
    for url in OVERPASS_URLS:
        for attempt in range(2):
            try:
                resp = requests.post(url, data={"data": query}, headers=headers, timeout=15)
                if resp.status_code == 429:
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                last_error = exc
                continue
    raise RuntimeError(f"Overpass API indisponible: {last_error}")


def _build_geojson(osm_data):
    nodes = {}
    for el in osm_data.get("elements", []):
        if el["type"] == "node":
            nodes[el["id"]] = (el["lon"], el["lat"])

    features = []
    for el in osm_data.get("elements", []):
        if el["type"] != "way":
            continue
        tags = el.get("tags", {})
        if "building" not in tags:
            continue

        node_ids = el.get("nodes", [])
        coords = [nodes[nid] for nid in node_ids if nid in nodes]
        if len(coords) < 3:
            continue
        if coords[0] != coords[-1]:
            coords.append(coords[0])

        area = _polygon_area_m2(coords)
        if area <= 0:
            continue

        name = tags.get("name") or tags.get("building") or "Bâtiment"
        levels = tags.get("building:levels")

        features.append(
            {
                "type": "Feature",
                "id": el["id"],
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {
                    "id": el["id"],
                    "name": name,
                    "building_type": tags.get("building"),
                    "levels": levels,
                    "area_m2": round(area, 1),
                },
            }
        )

    return features


def _city_viewport_bbox(center, half_span_deg=0.012):
    lat, lon = center
    return (lat - half_span_deg, lon - half_span_deg, lat + half_span_deg, lon + half_span_deg)


def _osm_buildings_cover(bbox):
    """Vrai si l'extrait OpenStreetMap ingéré couvre déjà cette zone.

    L'import se fait sur une emprise choisie (aujourd'hui la région de
    Casablanca), pas sur le pays entier : la question n'est donc pas « la table
    est-elle remplie » mais « contient-elle cet endroit ». Une ville hors
    emprise continue de passer par Overpass.
    """
    south, west, north, east = bbox
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM osm_buildings
                    WHERE centroid_lat BETWEEN %s AND %s
                      AND centroid_lon BETWEEN %s AND %s
                )
                """,
                (south, north, west, east),
            )
            return cur.fetchone()[0]
    except psycopg2.Error:
        conn.rollback()
        return False  # table absente : l'import n'a pas encore été lancé
    finally:
        pool.putconn(conn)


def _prewarm_cities():
    """Précharge en arrière-plan les tuiles des villes qu'OpenStreetMap ingéré
    ne couvre pas encore, pour un premier affichage instantané.

    Depuis l'ingestion de l'extrait Geofabrik, la carte lit la base pour la
    région importée : y préchauffer Overpass lançait des dizaines d'appels
    réseau à chaque démarrage pour remplir un cache que plus personne ne
    consultait. Les villes hors emprise, elles, en dépendent toujours.
    """
    # Chargé dans tous les cas : c'est le cache des zones non ingérées, et le
    # perdre ferait retomber ces villes sur Overpass à chaque redémarrage.
    _load_disk_cache()

    tiles_to_warm = set()
    now = time.time()
    for city in CITIES.values():
        bbox = _city_viewport_bbox(city["center"])
        if _osm_buildings_cover(bbox):
            continue
        for tile_key in _tile_keys_for_bbox(bbox):
            cached = _cache.get(tile_key)
            if not cached or now - cached["ts"] >= CACHE_TTL_SECONDS:
                tiles_to_warm.add(tile_key)

    if not tiles_to_warm:
        return

    max_workers = min(len(OVERPASS_URLS) * 2, len(tiles_to_warm))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_tile = {
            executor.submit(_fetch_overpass, _tile_bbox(*tile_key)): tile_key for tile_key in tiles_to_warm
        }
        for future in as_completed(future_to_tile):
            tile_key = future_to_tile[future]
            try:
                osm_data = future.result()
            except RuntimeError:
                continue
            _cache[tile_key] = {"ts": now, "features": _build_geojson(osm_data)}

    _save_disk_cache()
