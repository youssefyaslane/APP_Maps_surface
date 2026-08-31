"""Segmentation IA des toits de bâtiments à partir de l'imagerie satellite (MobileSAM).

Au clic sur un point (lat/lon), on récupère les tuiles satellite Esri autour
de ce point, on fait tourner MobileSAM pour segmenter la forme sous le clic,
puis on convertit le masque de pixels en polygone géographique et on calcule
sa surface (même formule que pour les données OSM).
"""
import colorsys
import io
import math
import os
import threading
import zipfile
from collections import OrderedDict

import numpy as np
import requests
from PIL import Image

CACHE_DIR = os.environ.get("CACHE_DIR", os.path.dirname(__file__))
os.makedirs(CACHE_DIR, exist_ok=True)
CHECKPOINT_PATH = os.path.join(CACHE_DIR, "mobile_sam.pt")
CHECKPOINT_URL = "https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt"

TILE_SIZE_PX = 256
ZOOM = 19  # niveau de zoom XYZ utilisé pour l'extraction (haute résolution)
GRID_TILES = 3  # grille de 3x3 tuiles autour du point cliqué, pour avoir du contexte

MIN_ROOF_AREA_M2 = 15.0
MAX_ROOF_AREA_M2 = 50000.0

# Filtre couleur pour écarter la végétation et les routes/asphalte du mode zone
# (le modèle SAM n'a aucune notion sémantique de "toit", il segmente toute
# forme visuellement cohérente). Teinte HSV en degrés, saturation/valeur en %.
VEGETATION_HUE_RANGE = (70, 170)  # verts
VEGETATION_MIN_SATURATION = 0.15
ASPHALT_MAX_SATURATION = 0.12  # gris quasi neutre
ASPHALT_MAX_VALUE = 0.55  # sombre (asphalte), pour ne pas écarter les toits gris clair/béton

# Filtres de forme (mode zone uniquement) : un toit vu du ciel est compact et
# globalement rectangulaire, contrairement aux routes/allées (bandes étirées) et
# à la végétation ou aux cours irrégulières (contours déchiquetés).
# Compacité = 4*pi*aire / perimetre^2 : 0.79 pour un carré, ~0.42 pour un
# rectangle 5:1, ~0.15 pour une bande de route.
MIN_SHAPE_COMPACTNESS = 0.35
# Rectangularité = aire / aire du rectangle englobant : ~1 pour un toit
# rectangulaire, ~0.65 pour un toit en L, ~0.2 pour une route en diagonale.
MIN_SHAPE_RECTANGULARITY = 0.45

_predictor = None
_mask_generator = None


def _lonlat_to_tile_xy(lon, lat, zoom):
    lat_rad = math.radians(lat)
    n = 2.0**zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _tile_xy_to_lonlat(x, y, zoom):
    n = 2.0**zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lon, lat


def _fetch_tile_image(tile_x, tile_y, zoom):
    url = f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{zoom}/{tile_y}/{tile_x}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


def _fetch_composite_image(center_lon, center_lat, zoom=ZOOM, grid=GRID_TILES):
    """Assemble une grille de tuiles centrée sur le point cliqué en une seule image."""
    cx, cy = _lonlat_to_tile_xy(center_lon, center_lat, zoom)
    center_tile_x, center_tile_y = int(cx), int(cy)
    half = grid // 2

    composite = Image.new("RGB", (TILE_SIZE_PX * grid, TILE_SIZE_PX * grid))
    for row in range(grid):
        for col in range(grid):
            tx = center_tile_x - half + col
            ty = center_tile_y - half + row
            try:
                tile_img = _fetch_tile_image(tx, ty, zoom)
            except requests.RequestException:
                tile_img = Image.new("RGB", (TILE_SIZE_PX, TILE_SIZE_PX), (128, 128, 128))
            composite.paste(tile_img, (col * TILE_SIZE_PX, row * TILE_SIZE_PX))

    top_left_lon, top_left_lat = _tile_xy_to_lonlat(center_tile_x - half, center_tile_y - half, zoom)
    bottom_right_lon, bottom_right_lat = _tile_xy_to_lonlat(
        center_tile_x - half + grid, center_tile_y - half + grid, zoom
    )

    click_px_x = (cx - (center_tile_x - half)) * TILE_SIZE_PX
    click_px_y = (cy - (center_tile_y - half)) * TILE_SIZE_PX

    georef = {
        "top_left": (top_left_lon, top_left_lat),
        "bottom_right": (bottom_right_lon, bottom_right_lat),
        "width_px": TILE_SIZE_PX * grid,
        "height_px": TILE_SIZE_PX * grid,
        # Origine en coordonnées de tuiles : permet de convertir lon/lat -> pixel
        # exactement (projection Mercator), sans l'approximation linéaire.
        "tile_x0": center_tile_x - half,
        "tile_y0": center_tile_y - half,
        "zoom": zoom,
    }
    return composite, georef, (click_px_x, click_px_y)


def _fetch_composite_for_bbox(south, west, north, east, zoom=ZOOM):
    """Assemble en une seule image toutes les tuiles couvrant la bbox donnée."""
    tl_x, tl_y = _lonlat_to_tile_xy(west, north, zoom)
    br_x, br_y = _lonlat_to_tile_xy(east, south, zoom)
    tile_x0, tile_y0 = int(tl_x), int(tl_y)
    tile_x1, tile_y1 = int(br_x), int(br_y)

    grid_w = max(tile_x1 - tile_x0 + 1, 1)
    grid_h = max(tile_y1 - tile_y0 + 1, 1)

    composite = Image.new("RGB", (TILE_SIZE_PX * grid_w, TILE_SIZE_PX * grid_h))
    for row in range(grid_h):
        for col in range(grid_w):
            tx = tile_x0 + col
            ty = tile_y0 + row
            try:
                tile_img = _fetch_tile_image(tx, ty, zoom)
            except requests.RequestException:
                tile_img = Image.new("RGB", (TILE_SIZE_PX, TILE_SIZE_PX), (128, 128, 128))
            composite.paste(tile_img, (col * TILE_SIZE_PX, row * TILE_SIZE_PX))

    top_left_lon, top_left_lat = _tile_xy_to_lonlat(tile_x0, tile_y0, zoom)
    bottom_right_lon, bottom_right_lat = _tile_xy_to_lonlat(tile_x0 + grid_w, tile_y0 + grid_h, zoom)

    georef = {
        "top_left": (top_left_lon, top_left_lat),
        "bottom_right": (bottom_right_lon, bottom_right_lat),
        "width_px": TILE_SIZE_PX * grid_w,
        "height_px": TILE_SIZE_PX * grid_h,
        "tile_x0": tile_x0,
        "tile_y0": tile_y0,
        "zoom": zoom,
    }
    return composite, georef


def _pixel_to_lonlat(px, py, georef):
    tl_lon, tl_lat = georef["top_left"]
    br_lon, br_lat = georef["bottom_right"]
    frac_x = px / georef["width_px"]
    frac_y = py / georef["height_px"]
    lon = tl_lon + frac_x * (br_lon - tl_lon)
    lat = tl_lat + frac_y * (br_lat - tl_lat)
    return lon, lat


def _lonlat_to_pixel(lon, lat, georef):
    """Position en pixels d'un point lon/lat dans le composite.

    Passe par les coordonnées de tuiles quand l'origine est connue : la
    latitude n'est pas linéaire en Mercator, une interpolation directe
    décalerait le point. Repli linéaire pour les georef sans origine.
    """
    if "tile_x0" in georef:
        x, y = _lonlat_to_tile_xy(lon, lat, georef["zoom"])
        return (x - georef["tile_x0"]) * TILE_SIZE_PX, (y - georef["tile_y0"]) * TILE_SIZE_PX

    tl_lon, tl_lat = georef["top_left"]
    br_lon, br_lat = georef["bottom_right"]
    px = (lon - tl_lon) / (br_lon - tl_lon) * georef["width_px"]
    py = (lat - tl_lat) / (br_lat - tl_lat) * georef["height_px"]
    return px, py


# --- Segmentation interactive -------------------------------------------
# Le coût d'un clic est presque entièrement dans l'encodeur d'image
# (~2,3s mesurées) ; le décodeur qui produit le masque à partir des points
# ne coûte que ~0,2s. En gardant l'embedding en cache, les clics de
# correction deviennent quasi instantanés.

# ~4,2 Mo par entrée (features 1x256x64x64 en float32).
EMBEDDING_CACHE_MAX = 16
_embedding_cache = OrderedDict()
_embedding_cache_lock = threading.Lock()

# Le prédicteur est un singleton dont set_image() et predict() mutent
# l'état interne : deux requêtes simultanées se mélangeraient les pinceaux.
_predict_lock = threading.Lock()


def _composite_tile_key(lon, lat, zoom=ZOOM, grid=GRID_TILES):
    """Identité du composite : deux clics sur le même bâtiment retombent sur
    la même clé et partagent donc le même embedding."""
    cx, cy = _lonlat_to_tile_xy(lon, lat, zoom)
    return (int(cx), int(cy), zoom, grid)


def _get_embedding(lon, lat):
    """Embedding de l'imagerie autour du point, calculé une fois puis réutilisé."""
    key = _composite_tile_key(lon, lat)

    with _embedding_cache_lock:
        entry = _embedding_cache.get(key)
        if entry is not None:
            _embedding_cache.move_to_end(key)
            return entry

    composite, georef, _click = _fetch_composite_image(lon, lat)
    predictor = _get_predictor()

    with _predict_lock:
        predictor.set_image(np.array(composite))
        entry = {
            "features": predictor.features.clone(),
            "original_size": predictor.original_size,
            "input_size": predictor.input_size,
            "georef": georef,
        }

    with _embedding_cache_lock:
        _embedding_cache[key] = entry
        _embedding_cache.move_to_end(key)
        while len(_embedding_cache) > EMBEDDING_CACHE_MAX:
            _embedding_cache.popitem(last=False)

    return entry


def _predict_from_points(entry, points_px, labels, prev_logits=None):
    """Masque produit par le décodeur à partir des points accumulés.

    `prev_logits` réinjecte le masque de l'itération précédente : c'est ce
    qui permet à une correction d'affiner le contour existant plutôt que de
    repartir de zéro.
    """
    predictor = _get_predictor()

    with _predict_lock:
        predictor.features = entry["features"]
        predictor.original_size = entry["original_size"]
        predictor.input_size = entry["input_size"]
        predictor.is_image_set = True

        kwargs = {
            "point_coords": np.array(points_px, dtype=np.float32),
            "point_labels": np.array(labels, dtype=np.int32),
        }
        if prev_logits is None:
            # Premier point : on laisse le modèle proposer plusieurs échelles.
            kwargs["multimask_output"] = True
        else:
            # Correction : on affine un masque précis, pas la peine d'explorer.
            kwargs["mask_input"] = prev_logits[None, :, :]
            kwargs["multimask_output"] = False

        masks, scores, logits = predictor.predict(**kwargs)

    best = int(np.argmax(scores))
    return masks[best], logits[best]


def _mask_to_result(mask, georef):
    """Masque booléen -> polygone géographique + surface, ou None."""
    polygon_px = _mask_to_polygon_px(mask)
    if not polygon_px or len(polygon_px) < 3:
        return None

    polygon_lonlat = [_pixel_to_lonlat(px, py, georef) for px, py in polygon_px]
    area = _polygon_area_m2(polygon_lonlat)
    if area <= 0:
        return None

    return {
        "polygon": [[lon_, lat_] for lon_, lat_ in polygon_lonlat],
        "area_m2": round(area, 1),
    }


def segment_start(lon, lat):
    """Premier clic. Renvoie (résultat, état) — l'état doit être conservé pour
    les corrections suivantes. Résultat None si rien n'a pu être segmenté."""
    entry = _get_embedding(lon, lat)
    px, py = _lonlat_to_pixel(lon, lat, entry["georef"])

    mask, logits = _predict_from_points(entry, [[px, py]], [1])
    result = _mask_to_result(mask, entry["georef"])

    state = {
        "anchor": (lon, lat),
        "points_px": [[px, py]],
        "labels": [1],
        "logits": logits,
    }
    return result, state


def segment_add_point(state, lon, lat, label):
    """Clic de correction : label=1 pour étendre le toit, 0 pour retirer une
    zone. Met `state` à jour et renvoie le nouveau résultat (ou None)."""
    anchor_lon, anchor_lat = state["anchor"]
    # Recalcule l'embedding si le cache l'a évincé entre-temps.
    entry = _get_embedding(anchor_lon, anchor_lat)

    px, py = _lonlat_to_pixel(lon, lat, entry["georef"])
    state["points_px"].append([px, py])
    state["labels"].append(1 if label else 0)

    mask, logits = _predict_from_points(
        entry, state["points_px"], state["labels"], state["logits"]
    )
    state["logits"] = logits
    return _mask_to_result(mask, entry["georef"])


def segment_undo_point(state):
    """Annule le dernier point de correction. Renvoie le résultat recalculé,
    ou None s'il ne reste que le point d'origine."""
    if len(state["points_px"]) < 2:
        return None

    state["points_px"].pop()
    state["labels"].pop()

    entry = _get_embedding(*state["anchor"])
    # Repart d'une prédiction propre : les logits courants intègrent le point
    # qu'on vient justement de retirer.
    state["logits"] = None
    mask, logits = _predict_from_points(entry, state["points_px"], state["labels"])
    state["logits"] = logits
    return _mask_to_result(mask, entry["georef"])


def _is_valid_checkpoint(path):
    """Un .pt PyTorch est un zip : un fichier tronqué (téléchargement
    interrompu) reste présent sur disque mais échoue ce test."""
    try:
        return zipfile.is_zipfile(path)
    except OSError:
        return False


def _download_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        if _is_valid_checkpoint(CHECKPOINT_PATH):
            return
        os.remove(CHECKPOINT_PATH)

    resp = requests.get(CHECKPOINT_URL, timeout=60, stream=True)
    resp.raise_for_status()
    tmp_path = CHECKPOINT_PATH + ".tmp"
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)

    if not _is_valid_checkpoint(tmp_path):
        os.remove(tmp_path)
        raise RuntimeError("Téléchargement du modèle MobileSAM corrompu (fichier tronqué), réessayez")

    os.replace(tmp_path, CHECKPOINT_PATH)


def _get_predictor():
    global _predictor
    if _predictor is not None:
        return _predictor

    _download_checkpoint()

    from mobile_sam import SamPredictor, sam_model_registry

    model = sam_model_registry["vit_t"](checkpoint=CHECKPOINT_PATH)
    model.eval()
    _predictor = SamPredictor(model)
    return _predictor


def _get_mask_generator():
    global _mask_generator
    if _mask_generator is not None:
        return _mask_generator

    _download_checkpoint()

    from mobile_sam import SamAutomaticMaskGenerator, sam_model_registry

    model = sam_model_registry["vit_t"](checkpoint=CHECKPOINT_PATH)
    model.eval()
    _mask_generator = SamAutomaticMaskGenerator(
        model,
        points_per_side=20,
        pred_iou_thresh=0.86,
        stability_score_thresh=0.9,
        min_mask_region_area=200,
    )
    return _mask_generator


def _is_vegetation_or_asphalt(image_np, mask):
    """Détermine si un masque correspond à de la végétation ou de l'asphalte
    (routes/parkings) d'après sa couleur moyenne, pour l'écarter des toits
    détectés en mode zone."""
    pixels = image_np[mask]
    if len(pixels) == 0:
        return False

    r, g, b = (pixels[:, i].mean() / 255.0 for i in range(3))
    hue, saturation, value = colorsys.rgb_to_hsv(r, g, b)
    hue_deg = hue * 360

    if saturation >= VEGETATION_MIN_SATURATION and VEGETATION_HUE_RANGE[0] <= hue_deg <= VEGETATION_HUE_RANGE[1]:
        return True

    if saturation <= ASPHALT_MAX_SATURATION and value <= ASPHALT_MAX_VALUE:
        return True

    return False


def _has_roof_like_shape(polygon_px):
    """Écarte les formes géométriquement improbables pour un toit : bandes
    étirées (routes, allées) et contours déchiquetés (végétation, cours).
    Travaille en pixels, les ratios étant sans dimension.

    Retourne (True, None) si la forme est plausible, sinon (False, motif).
    """
    n = len(polygon_px)
    if n < 3:
        return False, "polygone dégénéré"

    area = 0.0
    perimeter = 0.0
    for i in range(n):
        x1, y1 = polygon_px[i]
        x2, y2 = polygon_px[(i + 1) % n]
        area += x1 * y2 - x2 * y1
        perimeter += math.hypot(x2 - x1, y2 - y1)
    area = abs(area) / 2.0

    if area <= 0 or perimeter <= 0:
        return False, "polygone dégénéré"

    compactness = 4 * math.pi * area / (perimeter**2)
    if compactness < MIN_SHAPE_COMPACTNESS:
        return False, f"forme trop étirée/déchiquetée (compacité {compactness:.2f})"

    xs = [p[0] for p in polygon_px]
    ys = [p[1] for p in polygon_px]
    bbox_area = (max(xs) - min(xs)) * (max(ys) - min(ys))
    if bbox_area <= 0:
        return False, "polygone dégénéré"

    rectangularity = area / bbox_area
    if rectangularity < MIN_SHAPE_RECTANGULARITY:
        return False, f"remplit mal son rectangle ({rectangularity:.2f})"

    return True, None


def _mask_to_polygon_px(mask):
    """Extrait le plus grand contour externe d'un masque booléen (approche sans cv2)."""
    import cv2

    mask_u8 = (mask.astype(np.uint8)) * 255
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    epsilon = 0.005 * cv2.arcLength(largest, True)
    approx = cv2.approxPolyDP(largest, epsilon, True)
    return [(int(pt[0][0]), int(pt[0][1])) for pt in approx]


def _polygon_area_m2(coords_lonlat):
    if len(coords_lonlat) < 3:
        return 0.0
    lat0 = sum(c[1] for c in coords_lonlat) / len(coords_lonlat)
    lat0_rad = math.radians(lat0)
    R = 6378137.0

    def project(lon, lat):
        x = math.radians(lon) * R * math.cos(lat0_rad)
        y = math.radians(lat) * R
        return x, y

    pts = [project(lon, lat) for lon, lat in coords_lonlat]
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def segment_building_at(lon, lat):
    """Segmente le bâtiment/toit visible sous le point (lon, lat), en un seul
    coup, sans possibilité de correction.

    Retourne un dict {polygon: [[lon, lat], ...], area_m2: float} ou None si
    aucune forme n'a pu être segmentée.
    """
    result, _state = segment_start(lon, lat)
    return result


def segment_building_in_box(south, west, north, east):
    """[Expérimental] Segmente le bâtiment contenu dans la boîte donnée, plutôt
    qu'un point unique cliqué. Objectif : lever l'ambiguïté d'échelle qu'un
    simple point ne peut pas exprimer (ex: deux toits voisins fusionnés).

    Retourne un dict {polygon: [[lon, lat], ...], area_m2: float} ou None.
    """
    # Contexte visuel autour de la boîte, pour que le modèle voie un peu au-delà
    # (même logique que la grille 3x3 du mode clic).
    pad_lat = (north - south) * 0.4
    pad_lon = (east - west) * 0.4
    composite, georef = _fetch_composite_for_bbox(
        south - pad_lat, west - pad_lon, north + pad_lat, east + pad_lon
    )

    predictor = _get_predictor()
    image_np = np.array(composite)
    predictor.set_image(image_np)

    x0, y0 = _lonlat_to_pixel(west, north, georef)
    x1, y1 = _lonlat_to_pixel(east, south, georef)
    input_box = np.array([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)])

    masks, scores, _ = predictor.predict(box=input_box, multimask_output=True)
    best_mask = masks[int(np.argmax(scores))]

    polygon_px = _mask_to_polygon_px(best_mask)
    if not polygon_px or len(polygon_px) < 3:
        return None

    polygon_lonlat = [_pixel_to_lonlat(px, py, georef) for px, py in polygon_px]
    area = _polygon_area_m2(polygon_lonlat)
    if area <= 0:
        return None

    return {
        "polygon": [[lon_, lat_] for lon_, lat_ in polygon_lonlat],
        "area_m2": round(area, 1),
    }


def segment_roofs_in_zone(south, west, north, east):
    """Détecte et segmente automatiquement tous les toits visibles dans la bbox donnée.

    Retourne une liste de dicts {polygon: [[lon, lat], ...], area_m2: float}.
    Peut lever ValueError si la zone demandée est trop grande.
    """
    composite, georef = _fetch_composite_for_bbox(south, west, north, east)

    generator = _get_mask_generator()
    image_np = np.array(composite)
    annotations = generator.generate(image_np)

    width_px, height_px = georef["width_px"], georef["height_px"]

    results = []
    for ann in annotations:
        x, y, w, h = ann["bbox"]
        # Écarte les formes qui touchent le bord de l'image (probablement coupées)
        if x <= 0 or y <= 0 or x + w >= width_px or y + h >= height_px:
            continue

        if _is_vegetation_or_asphalt(image_np, ann["segmentation"]):
            continue

        polygon_px = _mask_to_polygon_px(ann["segmentation"])
        if not polygon_px or len(polygon_px) < 3:
            continue

        plausible, _reason = _has_roof_like_shape(polygon_px)
        if not plausible:
            continue

        polygon_lonlat = [_pixel_to_lonlat(px, py, georef) for px, py in polygon_px]
        area = _polygon_area_m2(polygon_lonlat)
        if area < MIN_ROOF_AREA_M2 or area > MAX_ROOF_AREA_M2:
            continue

        results.append(
            {
                "polygon": [[lon_, lat_] for lon_, lat_ in polygon_lonlat],
                "area_m2": round(area, 1),
            }
        )

    return results


