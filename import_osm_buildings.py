"""Importe les bâtiments OpenStreetMap dans la table `osm_buildings`, depuis un
extrait Geofabrik, pour ne plus dépendre d'Overpass au moment du calcul.

Overpass était appelé dans le chemin critique du calcul solaire : un recalcul
complet prenait 13 minutes sur 1 600 entreprises, dominées par l'attente réseau.
Avec les bâtiments en base, la même opération ne coûte plus que quelques
secondes, et ne peut plus échouer pour cause de quota ou d'indisponibilité.

Préparation du fichier source (osmium-tool requis) :

    curl -sLO https://download.geofabrik.de/africa/morocco-latest.osm.pbf
    osmium extract --bbox=-8.10,33.20,-6.80,34.00 morocco-latest.osm.pbf -o zone.pbf
    osmium tags-filter zone.pbf w/building a/building -o batiments.pbf
    osmium export batiments.pbf -f geojsonseq --add-unique-id=type_id \
        -o batiments.geojsonl
    python import_osm_buildings.py batiments.geojsonl

Identifiants : osmium numérote les surfaces selon sa propre convention —
`way_id * 2` pour une surface issue d'un chemin, `relation_id * 2 + 1` pour une
surface issue d'une relation. On la décode pour retrouver l'identifiant OSM
d'origine, celui que renvoyait Overpass, afin que les `roof_key` déjà en base
(`osm:1111649392`) restent valides.
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
        return math.radians(lon) * R * math.cos(lat0_rad), math.radians(lat) * R

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


def _osm_id(feature_id):
    """Identifiant OSM d'origine à partir de l'identifiant de surface osmium.

    `a1234` est une surface : pair → chemin (id/2), impair → relation ((id-1)/2).
    Les autres préfixes (`w`, `n`) désignent des objets non surfaciques.
    """
    if not feature_id or feature_id[0] != "a":
        return None
    try:
        n = int(feature_id[1:])
    except ValueError:
        return None
    return n // 2 if n % 2 == 0 else (n - 1) // 2


def _largest_polygon(geometry):
    """Anneaux du plus grand polygone : [extérieur, trou, trou, ...].

    Les cours intérieures comptent : un îlot urbain peut en avoir dix-sept, et
    ne garder que le contour extérieur ferait passer le jardin central pour de
    la toiture — surface gonflée, et une entreprise située dans la cour serait
    rattachée à tout le pâté de maisons.
    """
    gtype = geometry.get("type")
    if gtype == "Polygon":
        return geometry["coordinates"]
    if gtype == "MultiPolygon":
        polys = geometry["coordinates"]
        return max(polys, key=lambda p: _polygon_area_m2(p[0])) if polys else []
    return []


def main(path):
    conn = psycopg2.connect(DATABASE_URL)
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS osm_buildings (
                osm_id BIGINT PRIMARY KEY,
                polygon JSONB NOT NULL,
                holes JSONB,
                area_m2 DOUBLE PRECISION NOT NULL,
                centroid_lon DOUBLE PRECISION NOT NULL,
                centroid_lat DOUBLE PRECISION NOT NULL,
                name TEXT,
                building_type TEXT,
                levels TEXT,
                imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_osm_buildings_centroid "
            "ON osm_buildings (centroid_lat, centroid_lon)"
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
                INSERT INTO osm_buildings
                    (osm_id, polygon, holes, area_m2, centroid_lon, centroid_lat,
                     name, building_type, levels)
                VALUES %s
                ON CONFLICT (osm_id) DO UPDATE SET
                    polygon = EXCLUDED.polygon,
                    holes = EXCLUDED.holes,
                    area_m2 = EXCLUDED.area_m2,
                    centroid_lon = EXCLUDED.centroid_lon,
                    centroid_lat = EXCLUDED.centroid_lat,
                    name = EXCLUDED.name,
                    building_type = EXCLUDED.building_type,
                    levels = EXCLUDED.levels,
                    imported_at = now()
                """,
                rows,
            )
        rows = []

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                feat = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            osm_id = _osm_id(feat.get("id", ""))
            rings = _largest_polygon(feat.get("geometry") or {})
            if osm_id is None or not rings:
                skipped += 1
                continue

            ring, holes = rings[0], rings[1:]
            area = _polygon_area_m2(ring) - sum(_polygon_area_m2(h) for h in holes)
            if area <= 0:
                skipped += 1
                continue

            if ring[0] != ring[-1]:
                ring = ring + [ring[0]]
            lon, lat = _centroid(ring)
            props = feat.get("properties") or {}
            rows.append(
                (
                    osm_id,
                    json.dumps(ring),
                    json.dumps(holes) if holes else None,
                    area,
                    lon,
                    lat,
                    props.get("name") or props.get("building") or "Bâtiment",
                    props.get("building"),
                    props.get("building:levels"),
                )
            )
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
