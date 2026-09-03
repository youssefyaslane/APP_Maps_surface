"""Exporte en CSV les grands toits (Microsoft ou IA/tracé manuel) sans aucune
entreprise connue à proximité : zone (min/max latitude et longitude), surface
et potentiel solaire.

Usage: python -m scripts.export_unmatched_roofs [surface_min_m2] [chemin_sortie.csv]

Sans argument, exporte les toits >= 2000 m² vers grands_toits_sans_entreprise.csv.
"""
import csv
import os
import sys

os.environ.setdefault("WERKZEUG_RUN_MAIN", "true")  # évite le préchargement au démarrage

import app

DEFAULT_MIN_AREA_M2 = 2000.0
DEFAULT_OUTPUT = "grands_toits_sans_entreprise.csv"


def export_csv(min_area_m2, output_path):
    app._init_db()
    roofs = app._query_unmatched_big_roofs(min_area_m2)

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [
                "Latitude min", "Latitude max", "Longitude min", "Longitude max",
                "Surface toit (m²)", "Panneaux estimés", "Puissance (kWc)", "Source toit",
            ]
        )
        for r in roofs:
            n_panels, kwc = app._estimate_solar(r["area_m2"])
            writer.writerow(
                [r["min_lat"], r["max_lat"], r["min_lon"], r["max_lon"], r["area_m2"], n_panels, kwc, r["source"]]
            )

    print(f"{len(roofs)} toit(s) >= {min_area_m2:.0f} m² exporté(s) vers {output_path}")


if __name__ == "__main__":
    min_area = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MIN_AREA_M2
    output = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT
    export_csv(min_area, output)
