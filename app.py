"""Flask app: carte du Maroc avec surface des vrais bâtiments (OpenStreetMap) au survol."""
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Flask, jsonify, render_template, request

import segmentation

app = Flask(__name__)

CACHE_DIR = os.environ.get("CACHE_DIR", os.path.dirname(__file__))
os.makedirs(CACHE_DIR, exist_ok=True)
DISK_CACHE_PATH = os.path.join(CACHE_DIR, "tile_cache.json")
_disk_cache_lock = threading.Lock()

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
CACHE_TTL_SECONDS = 1800
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


@app.route("/api/cities")
def api_cities():
    return jsonify(
        {key: {"label": c["label"], "center": c["center"], "zoom": c["zoom"]} for key, c in CITIES.items()}
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

    return jsonify(
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [result["polygon"] + [result["polygon"][0]]]},
            "properties": {"area_m2": result["area_m2"], "source": "ia-segmentation"},
        }
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
    threading.Thread(target=_prewarm_cities, daemon=True).start()
    threading.Thread(target=_prewarm_segmentation_model, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True)
