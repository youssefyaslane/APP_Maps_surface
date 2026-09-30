"""Image satellite d'un toit (Esri World Imagery, zoom 19, environ 25 cm/pixel).

Partagé par la détection des panneaux : scripts/panneaux/detect_panels_gemini.py (IA en
ligne) et scripts/panneaux/extraire_toits_panneaux.py (modèle YOLO).
"""
import io
import math

import requests

ZOOM = 19
ZOOM_REPLI = 18      # utilisé quand Esri n'a pas d'image au zoom 19 (environ 50 cm/pixel)
TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
MAX_CROP_PX = 3000   # au-delà (sites de plusieurs hectares), on garde le centre du toit

# D'où vient le polygone d'un toit, selon le préfixe de companies.roof_key.
TABLES = {"osm": ("osm_buildings", "osm_id"), "ms": ("ms_buildings", "id"), "ia": ("ia_segments", "id")}


def _px(lon, lat, zoom=ZOOM):
    n = 2.0 ** zoom * 256
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return (lon + 180) / 360 * n, y


def is_placeholder(tile):
    """Esri renvoie une tuile grise « Map data not yet available » là où il n'a
    pas d'image à ce zoom : un gris clair presque uniforme."""
    from PIL import ImageStat
    stat = ImageStat.Stat(tile.convert("L"))
    return stat.stddev[0] < 12 and 180 <= stat.mean[0] <= 230


def polygon_of(cur, roof_key):
    src, rid = roof_key.split(":", 1)
    if src not in TABLES:
        return None
    table, pk = TABLES[src]
    cur.execute(f"SELECT polygon FROM {table} WHERE {pk} = %s", (int(rid),))
    row = cur.fetchone()
    return row[0] if row else None


def _fetch_at(polygon, zoom, http):
    """(image, contour) au zoom donné ; (None, None) si Esri n'a pas d'image."""
    from PIL import Image

    limite = MAX_CROP_PX / 2 ** (ZOOM - zoom)   # même emprise au sol quel que soit le zoom
    pts = [_px(lon, lat, zoom) for lon, lat in polygon]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.12 + 20 / 2 ** (ZOOM - zoom)
    x0, y0, x1, y1 = min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
    if x1 - x0 > limite or y1 - y0 > limite:
        cx, cy, half = (x0 + x1) / 2, (y0 + y1) / 2, limite / 2
        x0, x1, y0, y1 = cx - half, cx + half, cy - half, cy + half
    tx0, ty0, tx1, ty1 = int(x0 // 256), int(y0 // 256), int(x1 // 256), int(y1 // 256)
    img = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
    empty = total = 0
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            resp = http.get(TILE_URL.format(z=zoom, y=ty, x=tx), timeout=20)
            resp.raise_for_status()
            tile = Image.open(io.BytesIO(resp.content)).convert("RGB")
            total += 1
            empty += is_placeholder(tile)
            img.paste(tile, ((tx - tx0) * 256, (ty - ty0) * 256))
    if empty * 2 > total:
        return None, None
    ox, oy = tx0 * 256, ty0 * 256
    box = (int(x0 - ox), int(y0 - oy), int(x1 - ox), int(y1 - oy))
    outline = [(round(x - ox - box[0], 1), round(y - oy - box[1], 1)) for x, y in pts]
    return img.crop(box), outline


def fetch_roof(polygon, session=None):
    """(image, contour) à l'échelle du zoom 19, sans dessin ; (None, None) si
    Esri n'a pas d'image. Le contour est en pixels de l'image.

    Là où le zoom 19 n'existe pas (« Map data not yet available », par exemple
    autour de Had Soualem), on dézoome d'un cran : l'image du zoom 18, deux fois
    moins fine, est agrandie pour garder la même échelle que les autres toits."""
    from PIL import Image

    http = session or requests
    for zoom in (ZOOM, ZOOM_REPLI):
        img, outline = _fetch_at(polygon, zoom, http)
        if img is None:
            continue
        if zoom != ZOOM:
            k = 2 ** (ZOOM - zoom)
            img = img.resize((img.width * k, img.height * k), Image.LANCZOS)
            outline = [(x * k, y * k) for x, y in outline]
        return img, outline
    return None, None


def draw_outline(img, outline, color=(255, 0, 0), width=3):
    from PIL import ImageDraw
    img = img.copy()
    ImageDraw.Draw(img).line(list(outline) + list(outline[:1]), fill=color, width=width)
    return img
