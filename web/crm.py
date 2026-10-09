"""Blueprint « crm » : la fiche de suivi d'un prospect et ses actions.

Chaque commercial prend lui-même les prospects qu'il suit, puis ne voit et ne
modifie que les siens ; un admin consulte tout, sans prendre ni modifier. La logique et ses
règles vivent dans services/crm.py.
"""
from flask import Blueprint, jsonify, render_template, request, session

from services import crm

bp = Blueprint("crm", __name__)


def _corps():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _qui():
    return session.get("user_id"), bool(session.get("is_admin"))


def _repondre(company_id, action):
    """Exécute l'action, puis renvoie la fiche à jour (ou l'erreur expliquée)."""
    try:
        action()
        return jsonify(crm.fiche(company_id, *_qui()))
    except crm.ErreurCRM as exc:
        return jsonify({"error": str(exc)}), exc.code


@bp.route("/suivi")
def suivi():
    """Page « Mes opportunités » : suivi des prospects pris par les commerciaux."""
    return render_template("suivi.html")


@bp.route("/api/crm/opportunites")
def api_crm_opportunites():
    return jsonify(crm.opportunites(
        session.get("user_id"),
        is_admin=bool(session.get("is_admin")),
        commercial=request.args.get("commercial") or "moi",
        statut=request.args.get("statut") or None,
        relances=request.args.get("relances") == "1",
        search=(request.args.get("search") or "").strip() or None,
    ))


@bp.route("/api/crm/references")
def api_crm_references():
    return jsonify({**crm.references(), "commerciaux": crm.commerciaux(),
                    "moi": session.get("user_id"), "admin": bool(session.get("is_admin"))})


@bp.route("/api/crm/relances")
def api_crm_relances():
    return jsonify(crm.relances_dues(session.get("user_id")))


@bp.route("/api/crm/<int:company_id>")
def api_crm_fiche(company_id):
    try:
        fiche = crm.fiche(company_id, *_qui())
    except crm.ErreurCRM as exc:
        return jsonify({"error": str(exc)}), exc.code
    if fiche is None:
        return jsonify({"error": "Entreprise introuvable."}), 404
    return jsonify(fiche)


@bp.route("/api/crm/<int:company_id>/prendre", methods=["POST"])
def api_crm_prendre(company_id):
    return _repondre(company_id, lambda: crm.prendre(company_id, *_qui()))


@bp.route("/api/crm/<int:company_id>/liberer", methods=["POST"])
def api_crm_liberer(company_id):
    return _repondre(company_id, lambda: crm.liberer(
        company_id, session.get("user_id"), bool(session.get("is_admin"))))


@bp.route("/api/crm/<int:company_id>/suivi", methods=["POST"])
def api_crm_suivi(company_id):
    """Étape et prochaine relance, enregistrées ensemble."""
    data = _corps()
    return _repondre(company_id, lambda: crm.mettre_a_jour_suivi(
        company_id, data.get("statut"), data.get("raison"), data.get("relance_le"),
        data.get("relance_objet"), *_qui(), commentaire=data.get("commentaire")))


@bp.route("/api/crm/<int:company_id>/decideur", methods=["POST"])
def api_crm_decideur(company_id):
    data = _corps()
    return _repondre(company_id, lambda: crm.enregistrer_decideur(company_id, data, *_qui()))


@bp.route("/api/crm/<int:company_id>/visite", methods=["POST"])
def api_crm_visite(company_id):
    data = _corps()
    return _repondre(company_id, lambda: crm.enregistrer_visite(company_id, data, *_qui()))


@bp.route("/api/crm/<int:company_id>/notes", methods=["POST"])
def api_crm_note(company_id):
    data = _corps()
    return _repondre(company_id, lambda: crm.ajouter_note(company_id, data.get("texte"), *_qui()))
