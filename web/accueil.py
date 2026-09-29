"""Blueprint « accueil » : page d'accueil publique."""

import os

from flask import Blueprint, current_app, render_template

from services.prospects import _landing_stats

bp = Blueprint("accueil", __name__)

@bp.route("/")
def landing():
    """Page de présentation. Les chiffres viennent de la base : une vitrine qui
    afficherait des valeurs figées vieillirait mal."""
    logo = "img/logo-netis.png" if os.path.exists(
        os.path.join(current_app.static_folder, "img", "logo-netis.png")
    ) else None
    return render_template("landing.html", stats=_landing_stats(), logo=logo)
