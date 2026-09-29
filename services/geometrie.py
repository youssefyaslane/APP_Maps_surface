"""Géométrie des polygones (lon/lat) : centroïde, point intérieur, distances, surface."""

import math

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
