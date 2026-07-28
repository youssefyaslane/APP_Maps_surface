"""Segmentation IA des toits de bâtiments à partir de l'imagerie satellite (MobileSAM).

Au clic sur un point (lat/lon), on récupère les tuiles satellite Esri autour
de ce point, on fait tourner MobileSAM pour segmenter la forme sous le clic,
puis on convertit le masque de pixels en polygone géographique et on calcule
sa surface (même formule que pour les données OSM).
"""
import io
import math
import os

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

_predictor = None


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
    }
    return composite, georef, (click_px_x, click_px_y)


def _pixel_to_lonlat(px, py, georef):
    tl_lon, tl_lat = georef["top_left"]
    br_lon, br_lat = georef["bottom_right"]
    frac_x = px / georef["width_px"]
    frac_y = py / georef["height_px"]
    lon = tl_lon + frac_x * (br_lon - tl_lon)
    lat = tl_lat + frac_y * (br_lat - tl_lat)
    return lon, lat


def _download_checkpoint():
    if os.path.exists(CHECKPOINT_PATH):
        return
    resp = requests.get(CHECKPOINT_URL, timeout=60, stream=True)
    resp.raise_for_status()
    tmp_path = CHECKPOINT_PATH + ".tmp"
    with open(tmp_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)
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
    """Segmente le bâtiment/toit visible sous le point (lon, lat) cliqué sur la carte.

    Retourne un dict {polygon: [[lon, lat], ...], area_m2: float} ou None si
    aucune forme n'a pu être segmentée.
    """
    composite, georef, (click_px_x, click_px_y) = _fetch_composite_image(lon, lat)

    predictor = _get_predictor()
    image_np = np.array(composite)
    predictor.set_image(image_np)

    input_point = np.array([[click_px_x, click_px_y]])
    input_label = np.array([1])

    masks, scores, _ = predictor.predict(
        point_coords=input_point,
        point_labels=input_label,
        multimask_output=True,
    )
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
