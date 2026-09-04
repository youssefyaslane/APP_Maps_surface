"""Importe les entreprises depuis un ou plusieurs fichiers Excel (exports de
différents scrapers Google Maps) dans la table PostgreSQL `companies`.
Usage: python -m scripts.import_companies [chemin.xlsx ...]
Sans argument, importe tous les .xlsx trouvés dans Data_clients/."""
import os
import sys

import openpyxl
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://maps:maps@localhost:5432/maps")

# Data_clients/ est à la racine du projet, pas dans scripts/ : le dossier de ce
# fichier a changé quand les scripts ont été rangés, et le chemin relatif
# pointait depuis vers un dossier inexistant — l'import sans argument ne
# trouvait plus aucun fichier et s'arrêtait sans rien dire d'utile.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(PROJECT_ROOT, "Data_clients")

# ~50 m en degrés. Deux exports du même commerce ne donnent pas exactement les
# mêmes coordonnées : le rapprochement par nom seul serait faux (« Ain Sebaa »,
# « Casablanca » désignent des sociétés distinctes), par coordonnées seules
# aussi (plusieurs sociétés partagent une adresse).
DUPLICATE_RADIUS_DEG = 0.00045


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


def _find_twin_without_place_id(cur, name, lon, lat):
    """Entreprise déjà en base au même nom et au même endroit, à utiliser quand
    la ligne importée n'a pas de `placeId`.

    Sans ce repli, `ON CONFLICT (place_id)` ne se déclenche jamais pour une
    ligne sans identifiant — deux NULL ne sont pas égaux pour un index unique —
    et chaque relance de l'import recrée la même entreprise indéfiniment.
    """
    r = DUPLICATE_RADIUS_DEG
    cur.execute(
        """
        SELECT id FROM companies
        WHERE place_id IS NULL
          AND lower(trim(name)) = lower(trim(%s))
          AND lon BETWEEN %s AND %s
          AND lat BETWEEN %s AND %s
        LIMIT 1
        """,
        (name, lon - r, lon + r, lat - r, lat + r),
    )
    row = cur.fetchone()
    return row[0] if row else None


def report_possible_duplicates(conn):
    """Signale les entreprises de même nom quasi au même endroit mais de
    `placeId` différents : Google les considère comme deux fiches, ce sont
    souvent deux passages du scraper sur le même établissement.

    Volontairement un avertissement, pas une fusion : la base contient de vrais
    homonymes distincts (des noms de quartier employés comme raison sociale), et
    fusionner à l'aveugle perdrait des prospects réels.
    """
    r = DUPLICATE_RADIUS_DEG
    with conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id, b.id, a.name, a.city
            FROM companies a
            JOIN companies b
              ON a.id < b.id
             AND lower(trim(a.name)) = lower(trim(b.name))
             AND b.lon BETWEEN a.lon - %(r)s AND a.lon + %(r)s
             AND b.lat BETWEEN a.lat - %(r)s AND a.lat + %(r)s
            ORDER BY a.name
            """,
            {"r": r},
        )
        pairs = cur.fetchall()

    if not pairs:
        return

    print(f"\n⚠ {len(pairs)} doublon(s) probable(s) — même nom à moins de 50 m :")
    for id_a, id_b, name, city in pairs[:20]:
        print(f"   #{id_a} / #{id_b}  {name} ({city or 'ville inconnue'})")
    if len(pairs) > 20:
        print(f"   … et {len(pairs) - 20} autre(s)")
    print("   À vérifier à la main : un même toit compté deux fois gonfle le potentiel total.")


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
                lat = _first(get(row, "latitude"), get(row, "location/lat"), get(row, "Latitude"))
                lon = _first(get(row, "longitude"), get(row, "location/lng"), get(row, "Longitude"))
                name = _first(get(row, "title"), get(row, "Nom"))
                if lat is None or lon is None or not name:
                    skipped += 1
                    continue

                phone = _first(get(row, "phone"), get(row, "phones/0"), get(row, "Téléphone"))
                email = _first(get(row, "email"), get(row, "emails/0"))
                category = _first(get(row, "category"), get(row, "categories/0"), get(row, "Catégorie"))
                rating = _first(get(row, "rating"), get(row, "totalScore"), get(row, "Note"))
                place_id = _first(get(row, "placeId"), get(row, "Place ID"))
                address = _first(get(row, "address"), get(row, "Adresse"))
                website = _first(get(row, "website"), get(row, "Site Web"))
                lon, lat = float(lon), float(lat)

                values = (name, category, address, get(row, "city"), phone, email, website, rating)

                twin_id = None if place_id else _find_twin_without_place_id(cur, name, lon, lat)
                if twin_id is not None:
                    cur.execute(
                        """
                        UPDATE companies SET
                            name = %s, category = %s, address = %s, city = %s,
                            phone = %s, email = %s, website = %s, rating = %s,
                            lon = %s, lat = %s
                        WHERE id = %s
                        """,
                        (*values, lon, lat, twin_id),
                    )
                else:
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
                        (*values, lon, lat, place_id),
                    )
                inserted += 1
    finally:
        conn.close()

    print(f"Importé/mis à jour : {inserted}, ignoré (coordonnées ou nom manquants) : {skipped}")


if __name__ == "__main__":
    paths = sys.argv[1:] if len(sys.argv) > 1 else _find_all_xlsx()
    if not paths:
        print(f"Aucun fichier .xlsx trouvé dans {DEFAULT_PATH}")
        print("Usage: python -m scripts.import_companies [chemin.xlsx ...]")
        sys.exit(1)
    for path in paths:
        print(f"--- {os.path.basename(path)} ---")
        import_companies(path)

    conn = psycopg2.connect(DATABASE_URL)
    try:
        report_possible_duplicates(conn)
    finally:
        conn.close()

    print("\nPensez à lancer : python -m scripts.compute_solar_potential")
