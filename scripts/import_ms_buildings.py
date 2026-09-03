"""Importe des empreintes de bâtiments détectées par IA (dataset ouvert Microsoft
Global ML Building Footprints, https://github.com/microsoft/GlobalMLBuildingFootprints)
dans la table PostgreSQL `ms_buildings`. Complète les zones peu/pas couvertes par OSM.

Le fichier source est au format .geojsonl (une Feature GeoJSON par ligne), distribué
compressé en .csv.gz malgré le contenu JSON. Téléchargez la tuile correspondant à
votre zone via le lien du dataset (dataset-links.csv sur le dépôt GitHub ci-dessus),
décompressez-la, puis lancez : python -m scripts.import_ms_buildings chemin/vers/fichier.geojsonl
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
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _polygon_centroid(coords):
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return lon, lat


def import_ms_buildings(path):
    conn = psycopg2.connect(DATABASE_URL)
    inserted = 0
    skipped = 0
    batch = []

    def flush():
        nonlocal inserted, batch
        if not batch:
            return
        with conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                # geom_hash est une colonne générée : la contrainte d'unicité
                # rend le script rejouable, réimporter le même fichier ne
                # duplique rien.
                "INSERT INTO ms_buildings (polygon, area_m2, centroid_lon, centroid_lat) "
                "VALUES %s ON CONFLICT (geom_hash) DO NOTHING",
                batch,
            )
        inserted += len(batch)
        batch = []

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    feature = json.loads(line)
                    coords = feature["geometry"]["coordinates"][0]
                except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                    skipped += 1
                    continue

                if len(coords) < 3:
                    skipped += 1
                    continue

                polygon = [[c[0], c[1]] for c in coords]
                area = _polygon_area_m2(polygon)
                if area <= 0:
                    skipped += 1
                    continue

                lon, lat = _polygon_centroid(polygon)
                batch.append((json.dumps(polygon), round(area, 1), lon, lat))

                if len(batch) >= BATCH_SIZE:
                    flush()
                    print(f"  {inserted} importés...", end="\r")

        flush()
    finally:
        conn.close()

    print(f"Importé : {inserted}, ignoré (géométrie invalide) : {skipped}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.import_ms_buildings chemin/vers/fichier.geojsonl")
        sys.exit(1)
    import_ms_buildings(sys.argv[1])
