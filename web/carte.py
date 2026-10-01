"""Blueprint « carte » : la carte (/carte) et son API (bâtiments, segmentation IA, toits, entreprises)."""

import secrets
import threading
import time
from concurrent.futures import as_completed, ThreadPoolExecutor

import requests
from flask import Blueprint, jsonify, render_template, request, session

from services import segmentation
from services.calepinage import calepiner
from services.geometrie import _polygon_area_m2
from services.solar import config as solar_config
from services.toits import (
    _bbox_within_morocco,
    _build_geojson,
    _cache,
    _delete_ia_segment,
    _fetch_overpass,
    _find_roof_at_point,
    _query_companies,
    _query_ia_segments,
    _query_ms_buildings,
    _query_osm_buildings,
    _save_disk_cache,
    _store_ia_segment,
    _tile_bbox,
    _tile_keys_for_bbox,
    CACHE_TTL_SECONDS,
    CITIES,
    OVERPASS_URLS,
)

bp = Blueprint("carte", __name__)

@bp.route("/carte")
def index():
    # Les hypothèses solaires sont injectées dans la page plutôt que récupérées
    # par un appel : un survol de bâtiment peut survenir avant qu'une requête
    # asynchrone soit revenue, et la carte afficherait alors une puissance
    # calculée sur des valeurs de repli — donc différente du tableau de bord.
    return render_template("index.html", solar_config=solar_config())


@bp.route("/api/cities")
def api_cities():
    return jsonify(
        {key: {"label": c["label"], "center": c["center"], "zoom": c["zoom"]} for key, c in CITIES.items()}
    )


@bp.route("/api/geocode")
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


@bp.route("/api/buildings")
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

    # Table locale d'abord : la carte et le calcul du potentiel doivent montrer
    # exactement les mêmes bâtiments, sinon une surface survolée ne correspond
    # pas à celle du tableau de bord.
    local = _query_osm_buildings(bbox)
    if local:
        return jsonify({"type": "FeatureCollection", "features": local})

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


@bp.route("/api/segment")
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

    stored = _store_ia_segment(result["polygon"], result["area_m2"], created_by=session.get("user_id"))

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


@bp.route("/api/segment/start", methods=["POST"])
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


@bp.route("/api/segment/refine", methods=["POST"])
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


@bp.route("/api/segment/undo", methods=["POST"])
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


@bp.route("/api/segment/commit", methods=["POST"])
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

    stored = _store_ia_segment(polygon, round(area, 1), created_by=session.get("user_id"))

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


@bp.route("/api/segment/cancel", methods=["POST"])
def api_segment_cancel():
    body = request.get_json(silent=True) or {}
    with _seg_sessions_lock:
        _seg_sessions.pop(body.get("session_id"), None)
    return jsonify({"ok": True})


@bp.route("/api/roof_manual", methods=["POST"])
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

    stored = _store_ia_segment(
        polygon, round(area, 1), source="manual-trace", created_by=session.get("user_id")
    )

    return jsonify(
        {
            "type": "Feature",
            "id": stored["id"],
            "geometry": {"type": "Polygon", "coordinates": [stored["polygon"] + [stored["polygon"][0]]]},
            "properties": {"area_m2": stored["area_m2"], "source": stored["source"]},
        }
    )


@bp.route("/api/ia_segments")
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


@bp.route("/api/ia_segments/<int:seg_id>", methods=["DELETE"])
def api_delete_ia_segment(seg_id):
    if not _delete_ia_segment(seg_id, deleted_by=session.get("user_id")):
        return jsonify({"error": "Segmentation introuvable"}), 404
    return jsonify({"ok": True})


@bp.route("/api/companies")
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
                "equipped": r["equipped"],
            },
        }
        for r in rows
    ]
    return jsonify({"type": "FeatureCollection", "features": features})


@bp.route("/api/ms_buildings")
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


@bp.route("/api/company_roof")
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


@bp.route("/api/roof_layout")
def api_roof_layout():
    """Panneaux posés sur le toit d'une entreprise : leur nombre et leurs
    contours, pour les dessiner sur la carte (voir services/calepinage.py)."""
    try:
        lon = float(request.args["lon"])
        lat = float(request.args["lat"])
    except (KeyError, ValueError):
        return jsonify({"error": "Paramètres lon/lat invalides"}), 400

    roof = _find_roof_at_point(lon, lat)
    if roof is None or not roof.get("polygon"):
        return jsonify({"error": "Aucun toit sous cette entreprise"}), 404
    return jsonify(calepiner(roof["polygon"]))
