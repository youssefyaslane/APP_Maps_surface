"""Flask app: carte du Maroc avec surface des vrais bâtiments (OpenStreetMap) au survol."""
import csv
import io
import json
import math
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import psycopg2.pool
import requests
from flask import Flask, Response, jsonify, render_template, request

import segmentation

app = Flask(__name__)

CACHE_DIR = os.environ.get("CACHE_DIR", os.path.dirname(__file__))
os.makedirs(CACHE_DIR, exist_ok=True)
DISK_CACHE_PATH = os.path.join(CACHE_DIR, "tile_cache.json")
_disk_cache_lock = threading.Lock()

# Toits détectés par IA (clic simple ou zone), persistés dans PostgreSQL pour
# rester affichés d'une session à l'autre, et supprimables par l'utilisateur.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://maps:maps@db:5432/maps")
# Distance de dédoublonnage (en degrés) : deux détections dont le centroïde
# est plus proche que ça sont considérées comme le même toit.
IA_SEGMENT_DEDUP_DEG = 0.00005
_db_pool = None


def _get_db_pool():
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    last_error = None
    for _ in range(15):
        try:
            _db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
            return _db_pool
        except psycopg2.OperationalError as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Impossible de se connecter à PostgreSQL: {last_error}")


def _init_db():
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ia_segments (
                    id SERIAL PRIMARY KEY,
                    polygon JSONB NOT NULL,
                    area_m2 DOUBLE PRECISION NOT NULL,
                    centroid_lon DOUBLE PRECISION NOT NULL,
                    centroid_lat DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "ALTER TABLE ia_segments ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'ia-segmentation'"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ia_segments_centroid "
                "ON ia_segments (centroid_lat, centroid_lon)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    category TEXT,
                    address TEXT,
                    city TEXT,
                    phone TEXT,
                    email TEXT,
                    website TEXT,
                    rating DOUBLE PRECISION,
                    lon DOUBLE PRECISION NOT NULL,
                    lat DOUBLE PRECISION NOT NULL,
                    place_id TEXT UNIQUE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_companies_coords ON companies (lat, lon)"
            )
            # Potentiel solaire calculé par compute_solar_potential.py (toit
            # trouvé sous l'entreprise + estimation panneaux/puissance).
            cur.execute(
                """
                ALTER TABLE companies
                    ADD COLUMN IF NOT EXISTS roof_area_m2 DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS roof_source TEXT,
                    ADD COLUMN IF NOT EXISTS solar_panels INTEGER,
                    ADD COLUMN IF NOT EXISTS solar_kwc DOUBLE PRECISION,
                    ADD COLUMN IF NOT EXISTS solar_computed_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS roof_key TEXT
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_companies_solar_kwc "
                "ON companies (solar_kwc DESC NULLS LAST)"
            )
            # Sert au regroupement des entreprises partageant un même toit.
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_companies_roof_key ON companies (roof_key)"
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ms_buildings (
                    id SERIAL PRIMARY KEY,
                    polygon JSONB NOT NULL,
                    area_m2 DOUBLE PRECISION NOT NULL,
                    centroid_lon DOUBLE PRECISION NOT NULL,
                    centroid_lat DOUBLE PRECISION NOT NULL
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_ms_buildings_centroid "
                "ON ms_buildings (centroid_lat, centroid_lon)"
            )
    finally:
        pool.putconn(conn)


def _polygon_centroid(coords):
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return lon, lat


def _point_inside_polygon_guaranteed(coords):
    """Point garanti à l'intérieur du polygone, contrairement au centroïde
    géométrique qui peut tomber dehors sur une forme en L/U/complexe (~22%
    mesuré sur les bâtiments Microsoft). Triangule en éventail depuis le
    premier sommet et prend le centroïde du plus grand triangle valide."""
    n = len(coords)
    if n < 3:
        return coords[0] if coords else (0.0, 0.0)

    lon, lat = _polygon_centroid(coords)
    if _point_in_polygon(lon, lat, coords):
        return lon, lat

    x0, y0 = coords[0]
    best_area, best_point = 0.0, (lon, lat)
    for i in range(1, n - 1):
        x1, y1 = coords[i]
        x2, y2 = coords[i + 1]
        area = abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0))
        if area > best_area:
            tri_lon = (x0 + x1 + x2) / 3
            tri_lat = (y0 + y1 + y2) / 3
            if _point_in_polygon(tri_lon, tri_lat, coords):
                best_area, best_point = area, (tri_lon, tri_lat)

    return best_point


def _store_ia_segment(polygon, area_m2, source="ia-segmentation"):
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
                INSERT INTO ia_segments (polygon, area_m2, centroid_lon, centroid_lat, source)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
                """,
                (json.dumps(polygon), area_m2, lon, lat, source),
            )
            new_id = cur.fetchone()[0]
            _apply_solar_for_polygon(cur, polygon, area_m2, source, f"ia:{new_id}")
            return {"id": new_id, "polygon": polygon, "area_m2": area_m2, "source": source}
    finally:
        pool.putconn(conn)


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


def _delete_ia_segment(seg_id):
    """Supprime un toit détecté/tracé, puis recalcule le potentiel solaire des
    entreprises qui s'appuyaient dessus (un autre toit peut exister dessous)."""
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT polygon FROM ia_segments WHERE id = %s", (seg_id,))
            row = cur.fetchone()
            if row is None:
                return False
            polygon = row[0]

            cur.execute("DELETE FROM ia_segments WHERE id = %s", (seg_id,))
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


# Hypothèses d'installation, identiques à l'affichage carte (static/app.js) et
# au calcul en masse (compute_solar_potential.py).
SOLAR_PANEL_AREA_M2 = 1.7
SOLAR_PANEL_POWER_W = 400
SOLAR_USABLE_ROOF_FRACTION = 0.7


def _estimate_solar(area_m2):
    if not area_m2 or area_m2 <= 0:
        return 0, 0.0
    n_panels = int(area_m2 * SOLAR_USABLE_ROOF_FRACTION / SOLAR_PANEL_AREA_M2)
    return n_panels, round(n_panels * SOLAR_PANEL_POWER_W / 1000, 2)


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


def _apply_solar_for_polygon(cur, polygon, area_m2, source, roof_key=None):
    """Renseigne le potentiel solaire des entreprises situées sous ce toit
    fraîchement enregistré, pour qu'elles apparaissent aussitôt au tableau de
    bord. Ne touche pas celles déjà rattachées à un bâtiment OSM (prioritaire).

    Utilise le même rayon de rattrapage que _find_roof_at_point : le point GPS
    d'une entreprise tombe souvent juste à côté du toit (parfois à moins d'un
    mètre), et un test strict laisserait alors le marqueur rouge alors que la
    recherche en direct, elle, trouve bien le toit."""
    affected = _companies_inside_polygon(cur, polygon, include_nearby=True)
    if not affected:
        return 0

    n_panels, kwc = _estimate_solar(area_m2)
    cur.execute(
        """
        UPDATE companies SET
            roof_area_m2 = %s,
            roof_source = %s,
            roof_key = %s,
            solar_panels = %s,
            solar_kwc = %s,
            solar_computed_at = now()
        WHERE id = ANY(%s) AND (roof_source IS DISTINCT FROM 'osm')
        """,
        (area_m2, source, roof_key, n_panels, kwc, affected),
    )
    return cur.rowcount


def _query_companies(bbox):
    south, west, north, east = bbox
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, category, address, city, phone, email, website, rating,
                       lon, lat, roof_area_m2, solar_kwc
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
        }
        for r in rows
    ]


def _count_companies():
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM companies")
            return cur.fetchone()[0]
    finally:
        pool.putconn(conn)


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


def _point_in_polygon(lon, lat, polygon):
    """Test point-dans-polygone par ray casting (algorithme standard)."""
    inside = False
    n = len(polygon)
    x, y = lon, lat
    x1, y1 = polygon[-1]
    for x2, y2 in polygon:
        if (y1 > y) != (y2 > y):
            x_intersect = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_intersect:
                inside = not inside
        x1, y1 = x2, y2
    return inside


def _point_to_segment_distance_m(lon, lat, x1, y1, x2, y2):
    """Distance approximative (mètres) d'un point à un segment [(x1,y1)-(x2,y2)],
    coordonnées en degrés. Projection plane locale, suffisante à cette échelle."""
    lat0_rad = math.radians(lat)
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(lat0_rad)

    px, py = lon * m_per_deg_lon, lat * m_per_deg_lat
    ax, ay = x1 * m_per_deg_lon, y1 * m_per_deg_lat
    bx, by = x2 * m_per_deg_lon, y2 * m_per_deg_lat

    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _distance_to_polygon_m(lon, lat, polygon):
    """Distance (mètres) d'un point au polygone le plus proche (0 si dedans)."""
    if _point_in_polygon(lon, lat, polygon):
        return 0.0
    n = len(polygon)
    best = None
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        d = _point_to_segment_distance_m(lon, lat, x1, y1, x2, y2)
        if best is None or d < best:
            best = d
    return best if best is not None else float("inf")


def _find_roof_at_point(lon, lat):
    """Cherche un toit (OSM, ia_segments, ou ms_buildings) contenant ce point.
    OSM est prioritaire (données cartographiées réelles) sur les sources
    détectées par IA (ms-buildings, ia-segmentation), moins fiables.

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
            "roof_key": f"osm:{f['id']}",
        }
        for f in _cached_osm_buildings_at(bbox)
    ]
    ia_candidates = [
        {"area_m2": c["area_m2"], "source": c["source"], "polygon": c["polygon"], "roof_key": f"ia:{c['id']}"}
        for c in _query_ia_segments(bbox)
    ]
    ms_candidates = [
        {"area_m2": c["area_m2"], "source": "ms-buildings", "polygon": c["polygon"], "roof_key": f"ms:{c['id']}"}
        for c in _query_ms_buildings(bbox)
    ]

    for candidates in (osm_candidates, ia_candidates, ms_candidates):
        for c in candidates:
            if _point_in_polygon(lon, lat, c["polygon"]):
                return c

    best, best_dist = None, ROOF_NEARBY_RADIUS_M
    for candidates in (osm_candidates, ia_candidates, ms_candidates):
        for c in candidates:
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
    """Bâtiments OSM couvrant la bbox donnée (plusieurs tuiles si le point est
    proche d'une frontière de tuile)."""
    features = []
    for tile_key in _tile_keys_for_bbox(bbox):
        features.extend(_cached_osm_tile(tile_key))
    return features


def _bbox_within_morocco(bbox):
    south, west, north, east = bbox
    m_south, m_west, m_north, m_east = MOROCCO_BBOX
    return not (east < m_west or west > m_east or north < m_south or south > m_north)


def _polygon_area_m2(coords):
    """Aire d'un polygone (liste de [lon, lat]) via projection équirectangulaire locale."""
    if len(coords) < 3:
        return 0.0

    lat0 = sum(c[1] for c in coords) / len(coords)
    lat0_rad = math.radians(lat0)
    R = 6378137.0  # rayon terrestre moyen (m)

    def project(lon, lat):
        x = math.radians(lon) * R * math.cos(lat0_rad)
        y = math.radians(lat) * R
        return x, y

    pts = [project(lon, lat) for lon, lat in coords]

    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/cities")
def api_cities():
    return jsonify(
        {key: {"label": c["label"], "center": c["center"], "zoom": c["zoom"]} for key, c in CITIES.items()}
    )


@app.route("/api/geocode")
def api_geocode():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Paramètre q requis"}), 400

    headers = {"User-Agent": "maroc-buildings-map/1.0 (Flask demo app)"}
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"format": "json", "q": query, "countrycodes": "ma", "limit": 1},
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
    except requests.RequestException as exc:
        return jsonify({"error": f"Échec de la recherche: {exc}"}), 502

    if not results:
        return jsonify({"error": "Aucun lieu trouvé"}), 404

    result = results[0]
    return jsonify(
        {"lat": float(result["lat"]), "lon": float(result["lon"]), "display_name": result["display_name"]}
    )


@app.route("/api/buildings")
def api_buildings():
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres bbox invalides (south, west, north, east requis)"}), 400

    bbox = (south, west, north, east)

    if (north - south) * (east - west) > 0.05:
        return jsonify({"error": "Zone trop grande, veuillez zoomer pour voir les bâtiments"}), 400

    if not _bbox_within_morocco(bbox):
        return jsonify({"type": "FeatureCollection", "features": []})

    now = time.time()
    all_features = []
    missing_tiles = []

    for lat_idx, lon_idx in _tile_keys_for_bbox(bbox):
        tile_key = (lat_idx, lon_idx)
        cached = _cache.get(tile_key)
        if cached and now - cached["ts"] < CACHE_TTL_SECONDS:
            all_features.extend(cached["features"])
        else:
            missing_tiles.append(tile_key)

    if missing_tiles:
        errors = []
        max_workers = min(len(OVERPASS_URLS) * 2, len(missing_tiles))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_tile = {
                executor.submit(_fetch_overpass, _tile_bbox(*tile_key)): tile_key
                for tile_key in missing_tiles
            }
            for future in as_completed(future_to_tile):
                tile_key = future_to_tile[future]
                try:
                    osm_data = future.result()
                except RuntimeError as exc:
                    errors.append(str(exc))
                    continue
                tile_features = _build_geojson(osm_data)
                _cache[tile_key] = {"ts": now, "features": tile_features}
                all_features.extend(tile_features)

        if errors and not any(k in _cache for k in missing_tiles):
            return jsonify({"error": errors[0]}), 502

        _save_disk_cache()

    seen_ids = set()
    unique_features = []
    for feat in all_features:
        fid = feat["id"]
        if fid in seen_ids:
            continue
        seen_ids.add(fid)
        unique_features.append(feat)

    return jsonify({"type": "FeatureCollection", "features": unique_features})


@app.route("/api/segment")
def api_segment():
    try:
        lon = float(request.args["lon"])
        lat = float(request.args["lat"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres lon/lat invalides"}), 400

    if not _bbox_within_morocco((lat, lon, lat, lon)):
        return jsonify({"error": "Point hors du Maroc"}), 400

    try:
        result = segmentation.segment_building_at(lon, lat)
    except Exception as exc:
        return jsonify({"error": f"Échec de la segmentation: {exc}"}), 502

    if result is None:
        return jsonify({"error": "Aucun bâtiment détecté à cet endroit"}), 404

    stored = _store_ia_segment(result["polygon"], result["area_m2"])

    return jsonify(
        {
            "type": "Feature",
            "id": stored["id"],
            "geometry": {"type": "Polygon", "coordinates": [stored["polygon"] + [stored["polygon"][0]]]},
            "properties": {"area_m2": stored["area_m2"], "source": "ia-segmentation"},
        }
    )


# --- Segmentation interactive -------------------------------------------
# Le premier clic encode l'imagerie (~2,3s) ; les clics de correction
# réutilisent cet embedding et ne coûtent que ~0,2s. L'état de la session
# (points accumulés + masque courant) reste côté serveur : les logits sont
# un tableau numpy, non sérialisable vers le navigateur.

SEG_SESSION_TTL_SECONDS = 900
SEG_SESSION_MAX = 50
_seg_sessions = {}
_seg_sessions_lock = threading.Lock()


def _prune_seg_sessions(now):
    """Purge les sessions expirées, puis les plus anciennes s'il en reste trop
    (appelé sous _seg_sessions_lock)."""
    expired = [k for k, v in _seg_sessions.items() if now - v["ts"] >= SEG_SESSION_TTL_SECONDS]
    for k in expired:
        del _seg_sessions[k]

    while len(_seg_sessions) > SEG_SESSION_MAX:
        oldest = min(_seg_sessions, key=lambda k: _seg_sessions[k]["ts"])
        del _seg_sessions[oldest]


def _get_seg_session(session_id):
    with _seg_sessions_lock:
        entry = _seg_sessions.get(session_id)
        if entry is None or time.time() - entry["ts"] >= SEG_SESSION_TTL_SECONDS:
            return None
        entry["ts"] = time.time()
        return entry["state"]


def _seg_feature(result, session_id):
    """Aperçu non enregistré : pas encore d'id en base, le toit n'est stocké
    qu'à la validation."""
    return {
        "session_id": session_id,
        "area_m2": result["area_m2"],
        "polygon": result["polygon"],
    }


@app.route("/api/segment/start", methods=["POST"])
def api_segment_start():
    body = request.get_json(silent=True) or {}
    try:
        lon = float(body["lon"])
        lat = float(body["lat"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Paramètres lon/lat invalides"}), 400

    if not _bbox_within_morocco((lat, lon, lat, lon)):
        return jsonify({"error": "Point hors du Maroc"}), 400

    try:
        result, state = segmentation.segment_start(lon, lat)
    except Exception as exc:
        return jsonify({"error": f"Échec de la segmentation: {exc}"}), 502

    if result is None:
        return jsonify({"error": "Aucun bâtiment détecté à cet endroit"}), 404

    session_id = secrets.token_urlsafe(12)
    now = time.time()
    with _seg_sessions_lock:
        _prune_seg_sessions(now)
        _seg_sessions[session_id] = {"state": state, "ts": now}

    return jsonify(_seg_feature(result, session_id))


@app.route("/api/segment/refine", methods=["POST"])
def api_segment_refine():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")
    try:
        lon = float(body["lon"])
        lat = float(body["lat"])
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "Paramètres lon/lat invalides"}), 400

    # label 1 = étendre le toit, 0 = retirer cette zone
    label = 0 if body.get("label") in (0, "0", False) else 1

    state = _get_seg_session(session_id)
    if state is None:
        return jsonify({"error": "Session de segmentation expirée, recommencez"}), 404

    try:
        result = segmentation.segment_add_point(state, lon, lat, label)
    except Exception as exc:
        return jsonify({"error": f"Échec de la correction: {exc}"}), 502

    if result is None:
        return jsonify({"error": "Cette correction ne laisse aucune forme exploitable"}), 409

    return jsonify(_seg_feature(result, session_id))


@app.route("/api/segment/undo", methods=["POST"])
def api_segment_undo():
    body = request.get_json(silent=True) or {}
    state = _get_seg_session(body.get("session_id"))
    if state is None:
        return jsonify({"error": "Session de segmentation expirée, recommencez"}), 404

    try:
        result = segmentation.segment_undo_point(state)
    except Exception as exc:
        return jsonify({"error": f"Échec de l'annulation: {exc}"}), 502

    if result is None:
        return jsonify({"error": "Plus rien à annuler"}), 409

    return jsonify(_seg_feature(result, body.get("session_id")))


@app.route("/api/segment/commit", methods=["POST"])
def api_segment_commit():
    """Enregistre en base le toit affiché en aperçu, une fois corrigé."""
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id")

    state = _get_seg_session(session_id)
    if state is None:
        return jsonify({"error": "Session de segmentation expirée, recommencez"}), 404

    raw_polygon = body.get("polygon")
    if not raw_polygon or not isinstance(raw_polygon, list) or len(raw_polygon) < 3:
        return jsonify({"error": "Contour invalide"}), 400

    try:
        polygon = [[float(p[0]), float(p[1])] for p in raw_polygon]
    except (ValueError, TypeError, IndexError):
        return jsonify({"error": "Contour invalide"}), 400

    area = _polygon_area_m2(polygon)
    if area <= 0:
        return jsonify({"error": "Contour invalide"}), 400

    stored = _store_ia_segment(polygon, round(area, 1))

    with _seg_sessions_lock:
        _seg_sessions.pop(session_id, None)

    return jsonify(
        {
            "type": "Feature",
            "id": stored["id"],
            "geometry": {"type": "Polygon", "coordinates": [stored["polygon"] + [stored["polygon"][0]]]},
            "properties": {"area_m2": stored["area_m2"], "source": stored["source"]},
        }
    )


@app.route("/api/segment/cancel", methods=["POST"])
def api_segment_cancel():
    body = request.get_json(silent=True) or {}
    with _seg_sessions_lock:
        _seg_sessions.pop(body.get("session_id"), None)
    return jsonify({"ok": True})


@app.route("/api/roof_manual", methods=["POST"])
def api_roof_manual():
    """Toit tracé manuellement par l'utilisateur (clics formant les sommets du
    contour), sans passer par le modèle IA. Indépendant du clic simple, de la
    zone et de la persistance des détections IA."""
    body = request.get_json(silent=True) or {}
    raw_points = body.get("points")
    if not raw_points or not isinstance(raw_points, list) or len(raw_points) < 3:
        return jsonify({"error": "Il faut au moins 3 points pour former un contour"}), 400

    try:
        polygon = [[float(p[0]), float(p[1])] for p in raw_points]
    except (KeyError, ValueError, TypeError, IndexError):
        return jsonify({"error": "Points invalides"}), 400

    ref_lon, ref_lat = polygon[0]
    if not _bbox_within_morocco((ref_lat, ref_lon, ref_lat, ref_lon)):
        return jsonify({"error": "Point hors du Maroc"}), 400

    area = _polygon_area_m2(polygon)
    if area <= 0:
        return jsonify({"error": "Contour invalide"}), 400

    stored = _store_ia_segment(polygon, round(area, 1), source="manual-trace")

    return jsonify(
        {
            "type": "Feature",
            "id": stored["id"],
            "geometry": {"type": "Polygon", "coordinates": [stored["polygon"] + [stored["polygon"][0]]]},
            "properties": {"area_m2": stored["area_m2"], "source": stored["source"]},
        }
    )


@app.route("/api/segment_zone")
def api_segment_zone():
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres bbox invalides (south, west, north, east requis)"}), 400

    if not _bbox_within_morocco((south, west, north, east)):
        return jsonify({"error": "Zone hors du Maroc"}), 400

    try:
        results = segmentation.segment_roofs_in_zone(south, west, north, east)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Échec de la segmentation: {exc}"}), 502

    stored_segments = [_store_ia_segment(r["polygon"], r["area_m2"]) for r in results]

    features = [
        {
            "type": "Feature",
            "id": s["id"],
            "geometry": {"type": "Polygon", "coordinates": [s["polygon"] + [s["polygon"][0]]]},
            "properties": {"area_m2": s["area_m2"], "source": "ia-segmentation"},
        }
        for s in stored_segments
    ]
    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/api/ia_segments")
def api_ia_segments():
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres bbox invalides (south, west, north, east requis)"}), 400

    if (north - south) * (east - west) > 0.05:
        return jsonify({"error": "Zone trop grande"}), 400

    rows = _query_ia_segments((south, west, north, east))
    features = [
        {
            "type": "Feature",
            "id": r["id"],
            "geometry": {"type": "Polygon", "coordinates": [r["polygon"] + [r["polygon"][0]]]},
            "properties": {"area_m2": r["area_m2"], "source": r["source"]},
        }
        for r in rows
    ]
    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/api/ia_segments/<int:seg_id>", methods=["DELETE"])
def api_delete_ia_segment(seg_id):
    if not _delete_ia_segment(seg_id):
        return jsonify({"error": "Segmentation introuvable"}), 404
    return jsonify({"ok": True})


@app.route("/api/companies")
def api_companies():
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres bbox invalides (south, west, north, east requis)"}), 400

    rows = _query_companies((south, west, north, east))
    features = [
        {
            "type": "Feature",
            "id": r["id"],
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "name": r["name"],
                "category": r["category"],
                "address": r["address"],
                "city": r["city"],
                "phone": r["phone"],
                "email": r["email"],
                "website": r["website"],
                "rating": r["rating"],
                "roof_area_m2": r["roof_area_m2"],
                "solar_kwc": r["solar_kwc"],
                "has_roof": r["has_roof"],
            },
        }
        for r in rows
    ]
    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/api/ms_buildings")
def api_ms_buildings():
    """Bâtiments détectés par IA sur imagerie satellite, dataset ouvert
    Microsoft Global ML Building Footprints (complète les zones peu/pas
    couvertes par OSM)."""
    try:
        south = float(request.args["south"])
        west = float(request.args["west"])
        north = float(request.args["north"])
        east = float(request.args["east"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres bbox invalides (south, west, north, east requis)"}), 400

    if (north - south) * (east - west) > 0.05:
        return jsonify({"error": "Zone trop grande, veuillez zoomer"}), 400

    rows = _query_ms_buildings((south, west, north, east))
    features = [
        {
            "type": "Feature",
            "id": r["id"],
            "geometry": {"type": "Polygon", "coordinates": [r["polygon"] + [r["polygon"][0]]]},
            "properties": {"area_m2": r["area_m2"], "source": "ms-buildings"},
        }
        for r in rows
    ]
    return jsonify({"type": "FeatureCollection", "features": features})


@app.route("/api/company_roof")
def api_company_roof():
    """Trouve le toit (ms_buildings, ia_segments ou OSM) sous les coordonnées
    d'une entreprise, pour relier potentiel solaire et prospect."""
    try:
        lon = float(request.args["lon"])
        lat = float(request.args["lat"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres lon/lat invalides"}), 400

    roof = _find_roof_at_point(lon, lat)
    if roof is None:
        return jsonify({"area_m2": None})

    return jsonify(
        {
            "area_m2": roof["area_m2"],
            "source": roof["source"],
            "polygon": roof["polygon"],
        }
    )


def _prospects_filter_clauses(min_kwc=None, city=None, category=None, search=None, alias=""):
    """Clauses de filtrage du tableau de bord. `alias` préfixe les colonnes
    ("c." par exemple) quand la requête joint une autre table."""
    p = f"{alias}." if alias else ""
    clauses = [f"{p}solar_computed_at IS NOT NULL", f"{p}roof_area_m2 IS NOT NULL"]
    params = []

    if min_kwc is not None:
        clauses.append(f"{p}solar_kwc >= %s")
        params.append(min_kwc)
    if city:
        clauses.append(f"{p}city ILIKE %s")
        params.append(f"%{city}%")
    if category:
        clauses.append(f"{p}category ILIKE %s")
        params.append(f"%{category}%")
    if search:
        clauses.append(f"({p}name ILIKE %s OR {p}address ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])

    return clauses, params


def _count_prospects(min_kwc=None, city=None, category=None, search=None):
    clauses, params = _prospects_filter_clauses(min_kwc, city, category, search)
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM companies WHERE {' AND '.join(clauses)}", params)
            return cur.fetchone()[0]
    finally:
        pool.putconn(conn)


def _query_prospects(min_kwc=None, city=None, category=None, search=None, limit=None, offset=None):
    """Entreprises avec leur potentiel solaire calculé, triées par puissance
    installable décroissante (alimente le tableau de bord commercial)."""
    clauses, params = _prospects_filter_clauses(min_kwc, city, category, search, alias="c")

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
               COALESCE(s.n, 1) AS shared_count
        FROM companies c
        LEFT JOIN shared s ON s.roof_key = c.roof_key
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
        "shared_count",
    ]
    return [dict(zip(columns, row)) for row in rows]


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
                    count(*) FILTER (WHERE roof_area_m2 IS NOT NULL)
                FROM companies
                """
            )
            total, computed, with_roof = cur.fetchone()

            # Un toit ne s'equipe qu'une fois : agreger par entreprise comptait
            # plusieurs fois les batiments partages (mesure : 16,4% de
            # gonflement). Puissance, panneaux ET surface moyenne se calculent
            # donc sur les toits distincts — la moyenne est d'ailleurs annoncee
            # « par toit », pas par prospect.
            cur.execute(
                """
                SELECT COALESCE(sum(kwc), 0), COALESCE(sum(panels), 0),
                       count(*), COALESCE(avg(area), 0),
                       count(*) FILTER (WHERE kwc >= %s)
                FROM (
                    SELECT DISTINCT ON (COALESCE(roof_key, 'company:' || id))
                           solar_kwc AS kwc, solar_panels AS panels, roof_area_m2 AS area
                    FROM companies
                    WHERE roof_area_m2 IS NOT NULL
                    ORDER BY COALESCE(roof_key, 'company:' || id), solar_kwc DESC NULLS LAST
                ) t
                """,
                (BIG_PROSPECT_KWC,),
            )
            total_kwc, total_panels, distinct_roofs, avg_area, big = cur.fetchone()
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
        "avg_roof_area_m2": round(float(avg_area), 1),
        "big_prospects": big,
        "big_prospect_threshold": BIG_PROSPECT_KWC,
    }


@app.route("/api/prospects")
def api_prospects():
    try:
        min_kwc = request.args.get("min_kwc", type=float)
        limit = request.args.get("limit", default=50, type=int)
        offset = request.args.get("offset", default=0, type=int)
    except ValueError:
        return jsonify({"error": "Paramètres de filtre invalides"}), 400

    filters = dict(
        min_kwc=min_kwc,
        city=request.args.get("city"),
        category=request.args.get("category"),
        search=request.args.get("search"),
    )
    prospects = _query_prospects(**filters, limit=limit, offset=offset)
    total_filtered = _count_prospects(**filters)
    return jsonify(
        {
            "summary": _prospects_summary(),
            "prospects": prospects,
            "total_filtered": total_filtered,
            "limit": limit,
            "offset": offset,
        }
    )


@app.route("/api/prospects.csv")
def api_prospects_csv():
    """Export CSV de la liste de prospects, pour les commerciaux (Excel)."""
    prospects = _query_prospects(
        min_kwc=request.args.get("min_kwc", type=float),
        city=request.args.get("city"),
        category=request.args.get("category"),
        search=request.args.get("search"),
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Nom", "Catégorie", "Adresse", "Ville", "Téléphone", "Email", "Site web",
            "Surface toit (m²)", "Source toit", "Toit partagé", "Entreprises sur ce toit",
            "Panneaux estimés", "Puissance (kWc)", "Latitude", "Longitude",
        ]
    )
    for p in prospects:
        shared = p.get("shared_count", 1) or 1
        writer.writerow(
            [
                p["name"], p["category"], p["address"], p["city"], p["phone"],
                p["email"], p["website"], p["roof_area_m2"], p["roof_source"],
                "oui" if shared > 1 else "non", shared,
                p["solar_panels"], p["solar_kwc"], p["lat"], p["lon"],
            ]
        )

    # BOM UTF-8 pour qu'Excel ouvre correctement les accents.
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=prospects_solaire.csv"},
    )


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


@app.route("/api/unmatched_roofs.csv")
def api_unmatched_roofs_csv():
    """Export CSV des grands toits (Microsoft/IA) sans entreprise connue à
    proximité - prospects potentiels absents du fichier scraper actuel."""
    try:
        min_area = request.args.get("min_area", default=2000.0, type=float)
    except ValueError:
        return jsonify({"error": "Paramètre min_area invalide"}), 400

    roofs = _query_unmatched_big_roofs(min_area)

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Latitude min", "Latitude max", "Longitude min", "Longitude max",
            "Surface toit (m²)", "Panneaux estimés", "Puissance (kWc)", "Source toit",
        ]
    )
    for r in roofs:
        n_panels, kwc = _estimate_solar(r["area_m2"])
        writer.writerow(
            [r["min_lat"], r["max_lat"], r["min_lon"], r["max_lon"], r["area_m2"], n_panels, kwc, r["source"]]
        )

    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=grands_toits_sans_entreprise.csv"},
    )


def _city_viewport_bbox(center, half_span_deg=0.012):
    lat, lon = center
    return (lat - half_span_deg, lon - half_span_deg, lat + half_span_deg, lon + half_span_deg)


def _prewarm_cities():
    """Précharge en arrière-plan les tuiles autour de chaque ville pour un premier affichage instantané."""
    _load_disk_cache()

    tiles_to_warm = set()
    now = time.time()
    for city in CITIES.values():
        bbox = _city_viewport_bbox(city["center"])
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


def _prewarm_segmentation_model():
    """Télécharge le checkpoint et charge MobileSAM en mémoire pour un premier clic rapide."""
    try:
        segmentation._get_predictor()
    except Exception:
        pass


if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    _init_db()
    threading.Thread(target=_prewarm_cities, daemon=True).start()
    threading.Thread(target=_prewarm_segmentation_model, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True, threaded=True)
