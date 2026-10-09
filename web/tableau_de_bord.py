"""Blueprint « tableau » : tableau de bord commercial (/dashboard) et son API."""

import csv
import io

from flask import Blueprint, jsonify, render_template, request, Response, session

from services.prospects import (
    _count_prospects,
    _prospect_filter_values,
    _prospects_summary,
    _query_prospects,
    _query_unmatched_big_roofs,
    _set_company_equipped,
)
from services.solar import estimate_solar as _estimate_solar

bp = Blueprint("tableau", __name__)


def _filtres_crm():
    """Filtres du CRM lus dans la requête (voir _prospects_filter_clauses)."""
    return {
        "statut": request.args.get("statut") or None,
        "commercial": request.args.get("commercial") or None,
        "user_id": session.get("user_id"),
        "relances": request.args.get("relances") == "1",
    }

@bp.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")


@bp.route("/api/companies/<int:company_id>/equipped", methods=["POST"])
def api_company_equipped(company_id):
    """Ouvert à tout compte connecté, pas seulement aux administrateurs : ce
    sont les commerciaux qui constatent qu'une entreprise est déjà équipée."""
    data = request.get_json(silent=True)
    # Valeur explicite exigée : un corps vide ou mal formé ne doit rien marquer.
    if not isinstance(data, dict) or not isinstance(data.get("equipped"), bool):
        return jsonify({"error": "Préciser « equipped » : true ou false."}), 400
    if not _set_company_equipped(company_id, data["equipped"], session.get("user_id")):
        return jsonify({"error": "Entreprise introuvable."}), 404
    return jsonify({"id": company_id, "equipped": data["equipped"]})


@bp.route("/api/prospect_filters")
def api_prospect_filters():
    """Valeurs proposées par les listes déroulantes du tableau de bord.

    Prend les mêmes paramètres que /api/prospects : les nombres affichés en face
    de chaque choix doivent correspondre à la liste que ce choix produira,
    filtres en cours compris.
    """
    return jsonify(
        _prospect_filter_values(
            min_kwc=request.args.get("min_kwc", type=float),
            city=request.args.get("city"),
            category=request.args.get("category"),
            search=request.args.get("search"),
            equipped=request.args.get("equipped") == "1",
            pv=request.args.get("pv"),
            **_filtres_crm(),
        )
    )


@bp.route("/api/prospects")
def api_prospects():
    try:
        min_kwc = request.args.get("min_kwc", type=float)
        limit = request.args.get("limit", default=50, type=int)
        offset = request.args.get("offset", default=0, type=int)
    except ValueError:
        return jsonify({"error": "Paramètres de filtre invalides"}), 400

    filters = dict(
        min_kwc=min_kwc,
        city=request.args.get("city"),
        category=request.args.get("category"),
        search=request.args.get("search"),
        equipped=request.args.get("equipped") == "1",
        pv=request.args.get("pv"),
        **_filtres_crm(),
    )
    prospects = _query_prospects(**filters, limit=limit, offset=offset)
    total_filtered = _count_prospects(**filters)
    return jsonify(
        {
            "summary": _prospects_summary(),
            "prospects": prospects,
            "total_filtered": total_filtered,
            "limit": limit,
            "offset": offset,
        }
    )


@bp.route("/api/prospects.csv")
def api_prospects_csv():
    """Export CSV de la liste de prospects, pour les commerciaux (Excel)."""
    prospects = _query_prospects(
        min_kwc=request.args.get("min_kwc", type=float),
        city=request.args.get("city"),
        category=request.args.get("category"),
        search=request.args.get("search"),
        equipped=request.args.get("equipped") == "1",
        pv=request.args.get("pv"),
        **_filtres_crm(),
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Nom", "Catégorie", "Adresse", "Ville", "Téléphone", "Email", "Site web",
            "Surface toit (m²)", "Source toit", "Toit partagé", "Entreprises sur ce toit",
            "Panneaux estimés", "Puissance (kWc)", "Production (MWh/an)",
            "Productible (kWh/kWc/an)", "CO₂ évité (t/an)", "Économies max (DH/an)",
            "Pris par", "Latitude", "Longitude",
            "Panneaux déjà posés (détection)", "Confiance détection",
        ]
    )
    for p in prospects:
        shared = p.get("shared_count", 1) or 1
        writer.writerow(
            [
                p["name"], p["category"], p["address"], p["city"], p["phone"],
                p["email"], p["website"], p["roof_area_m2"], p["roof_source"],
                "oui" if shared > 1 else "non", shared,
                p["solar_panels"], p["solar_kwc"], p["production_mwh"],
                p["solar_yield_kwh_kwc"], p["co2_t"], p["economies_dh"],
                p["crm_commercial"], p["lat"], p["lon"],
                (p["pv"] or {}).get("verdict", "non analysé"), (p["pv"] or {}).get("confiance"),
            ]
        )

    # BOM UTF-8 pour qu'Excel ouvre correctement les accents.
    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=prospects_solaire.csv"},
    )


@bp.route("/api/unmatched_roofs.csv")
def api_unmatched_roofs_csv():
    """Export CSV des grands toits (Microsoft/IA) sans entreprise connue à
    proximité - prospects potentiels absents du fichier scraper actuel."""
    try:
        min_area = request.args.get("min_area", default=2000.0, type=float)
    except ValueError:
        return jsonify({"error": "Paramètre min_area invalide"}), 400

    roofs = _query_unmatched_big_roofs(min_area)

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(
        [
            "Latitude min", "Latitude max", "Longitude min", "Longitude max",
            "Surface toit (m²)", "Panneaux estimés", "Puissance (kWc)", "Source toit",
        ]
    )
    for r in roofs:
        n_panels, kwc = _estimate_solar(r["area_m2"])
        writer.writerow(
            [r["min_lat"], r["max_lat"], r["min_lon"], r["max_lon"], r["area_m2"], n_panels, kwc, r["source"]]
        )

    return Response(
        "﻿" + buffer.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=grands_toits_sans_entreprise.csv"},
    )
