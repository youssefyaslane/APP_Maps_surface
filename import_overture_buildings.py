"""Importe les empreintes de bâtiments Overture Maps dans la table
`overture_buildings`, pour pouvoir les comparer visuellement à OSM et Microsoft.

Overture (https://overturemaps.org) ne fournit pas une détection supplémentaire :
il fusionne OpenStreetMap, Microsoft ML, Google Open Buildings et quelques
cadastres nationaux, en dédoublonnant les contours dont l'IoU dépasse 0,5. Sur
Casablanca, la fusion se compose à 70% de Microsoft ML et 30% d'OSM — les deux
sources déjà utilisées par l'application. La couche sert donc à vérifier sur
pièce, pas à ajouter de l'information.

Chaque bâtiment garde son identifiant Overture (GERS), stable d'une version à
l'autre, ainsi que la liste des jeux de données dont il provient.

Téléchargement de la zone puis import :

    pip install overturemaps
    overturemaps download --bbox=-7.75,33.45,-7.40,33.65 \
        -f geojson --type=building -o casa.geojson
    python import_overture_buildings.py casa.geojson
"""
import json
import math
import os
import sys

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://maps:maps@localhost:5432/maps")
BATCH_SIZE = 2000


def _polygon_area_m2(coords):
    """Surface d'un anneau lon/lat, projeté localement (même formule qu'ailleurs)."""
    if len(coords) < 3:
        return 0.0
    lat0 = sum(c[1] for c in coords) / len(coords)
    lat0_rad = math.radians(lat0)
    R = 6378137.0

    def project(lon, lat):
        x = math.radians(lon) * R * math.cos(lat0_rad)
        y = math.radians(lat) * R
        return x, y

    pts = [project(lon, lat) for lon, lat in coords]
    total = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _centroid(coords):
    return (
        sum(c[0] for c in coords) / len(coords),
        sum(c[1] for c in coords) / len(coords),
    )


def _iter_features(path):
    """Accepte une FeatureCollection compacte comme un flux GeoJSON par ligne."""
    with open(path, encoding="utf-8") as fh:
        head = fh.read(512)
        fh.seek(0)
        if '"FeatureCollection"' in head:
            for feat in json.load(fh).get("features", []):
                yield feat
            return
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _outer_rings(geometry):
    """Anneaux extérieurs, que la géométrie soit un Polygon ou un MultiPolygon."""
    gtype = geometry.get("type")
    if gtype == "Polygon":
        return [geometry["coordinates"][0]]
    if gtype == "MultiPolygon":
        return [poly[0] for poly in geometry["coordinates"]]
    return []


def main(path):
    conn = psycopg2.connect(DATABASE_URL)
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS overture_buildings (
                id TEXT PRIMARY KEY,
                polygon JSONB NOT NULL,
                area_m2 DOUBLE PRECISION NOT NULL,
                centroid_lon DOUBLE PRECISION NOT NULL,
                centroid_lat DOUBLE PRECISION NOT NULL,
                sources TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_overture_buildings_centroid "
            "ON overture_buildings (centroid_lat, centroid_lon)"
        )

    rows = []
    imported = skipped = 0

    def flush():
        nonlocal rows
        if not rows:
            return
        with conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """
                INSERT INTO overture_buildings
                    (id, polygon, area_m2, centroid_lon, centroid_lat, sources)
                VALUES %s
                ON CONFLICT (id) DO NOTHING
                """,
                rows,
            )
        rows = []

    for feat in _iter_features(path):
        geom = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        bid = feat.get("id") or props.get("id")
        if not bid:
            skipped += 1
            continue

        for i, ring in enumerate(_outer_rings(geom)):
            area = _polygon_area_m2(ring)
            if area <= 0:
                skipped += 1
                continue
            lon, lat = _centroid(ring)
            datasets = sorted({s.get("dataset", "?") for s in (props.get("sources") or [])})
            # Un MultiPolygon donne plusieurs anneaux pour un seul identifiant.
            key = bid if i == 0 else f"{bid}#{i}"
            rows.append((key, json.dumps(ring), area, lon, lat, ",".join(datasets) or None))
            imported += 1

        if len(rows) >= BATCH_SIZE:
            flush()
            print(f"  {imported} bâtiments importés...", flush=True)

    flush()
    conn.close()
    print(f"Terminé : {imported} bâtiments importés, {skipped} ignorés.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
