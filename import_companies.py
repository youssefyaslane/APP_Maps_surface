"""Importe les entreprises depuis un ou plusieurs fichiers Excel (exports de
différents scrapers Google Maps) dans la table PostgreSQL `companies`.
Usage: python import_companies.py [chemin.xlsx ...]
Sans argument, importe tous les .xlsx trouvés dans Data_clients/."""
import os
import sys

import openpyxl
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://maps:maps@localhost:5432/maps")

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "Data_clients")


def _find_all_xlsx():
    if not os.path.isdir(DEFAULT_PATH):
        return []
    return sorted(
        os.path.join(DEFAULT_PATH, f) for f in os.listdir(DEFAULT_PATH) if f.lower().endswith(".xlsx")
    )


def _first(*values):
    for v in values:
        if v not in (None, ""):
            return v
    return None


def import_companies(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    col = {name: idx for idx, name in enumerate(header)}

    def get(row, name):
        idx = col.get(name)
        if idx is None or idx >= len(row):
            return None
        return row[idx]

    conn = psycopg2.connect(DATABASE_URL)
    inserted = 0
    skipped = 0
    try:
        with conn, conn.cursor() as cur:
            for row in rows:
                lat = _first(get(row, "latitude"), get(row, "location/lat"))
                lon = _first(get(row, "longitude"), get(row, "location/lng"))
                name = get(row, "title")
                if lat is None or lon is None or not name:
                    skipped += 1
                    continue

                phone = _first(get(row, "phone"), get(row, "phones/0"))
                email = _first(get(row, "email"), get(row, "emails/0"))
                category = _first(get(row, "category"), get(row, "categories/0"))
                rating = _first(get(row, "rating"), get(row, "totalScore"))
                place_id = get(row, "placeId")

                cur.execute(
                    """
                    INSERT INTO companies (name, category, address, city, phone, email, website, rating, lon, lat, place_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (place_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        category = EXCLUDED.category,
                        address = EXCLUDED.address,
                        city = EXCLUDED.city,
                        phone = EXCLUDED.phone,
                        email = EXCLUDED.email,
                        website = EXCLUDED.website,
                        rating = EXCLUDED.rating,
                        lon = EXCLUDED.lon,
                        lat = EXCLUDED.lat
                    """,
                    (
                        name,
                        category,
                        get(row, "address"),
                        get(row, "city"),
                        phone,
                        email,
                        get(row, "website"),
                        rating,
                        float(lon),
                        float(lat),
                        place_id,
                    ),
                )
                inserted += 1
    finally:
        conn.close()

    print(f"Importé/mis à jour : {inserted}, ignoré (coordonnées ou nom manquants) : {skipped}")


if __name__ == "__main__":
    paths = sys.argv[1:] if len(sys.argv) > 1 else _find_all_xlsx()
    if not paths:
        print("Aucun fichier .xlsx trouvé. Usage: python import_companies.py [chemin.xlsx ...]")
        sys.exit(1)
    for path in paths:
        print(f"--- {os.path.basename(path)} ---")
        import_companies(path)
