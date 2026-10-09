"""Blueprint « auth » : connexion, déconnexion, et contrôle d'accès de toutes les pages."""

from functools import wraps

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from services import etat_base
from services.comptes import _find_user_by_id, _find_user_by_username

bp = Blueprint("auth", __name__)

# Routes accessibles sans être connecté : la page de connexion elle-même, et
# les fichiers statiques (CSS/JS/images — bloquer leur chargement casserait la
# page de connexion en boucle). Tout le reste est un outil interne et ne doit
# rien montrer à un visiteur non authentifié, y compris la page d'accueil.
PUBLIC_ENDPOINTS = {"auth.login", "static"}


RECOVERY_ENDPOINTS = {"secours.db_recovery", "secours.db_recovery_retry", "secours.db_recovery_use_env"}


@bp.before_app_request
def _require_login():
    # Mode secours : tant que la base active est injoignable, seules la page de
    # secours et les fichiers statiques répondent. Rien n'est lu ni écrit
    # ailleurs en attendant qu'un administrateur choisisse.
    if etat_base._db_down is not None:
        if request.endpoint in RECOVERY_ENDPOINTS or request.endpoint == "static":
            return
        if request.path.startswith("/api/"):
            return jsonify({"error": "Base de données injoignable"}), 503
        return redirect(url_for("secours.db_recovery"))
    if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
        return
    user_id = session.get("user_id")
    if user_id is not None:
        # Le cookie est signé et valable 30 jours, mais il ne dit pas si le
        # compte existe encore : sans cette relecture, un compte supprimé depuis
        # la page Comptes garderait l'accès — droits d'administration compris —
        # jusqu'à l'expiration de son cookie.
        user = _find_user_by_id(user_id)
        if user is not None:
            # Même relecture pour les droits : un changement fait en base
            # s'applique tout de suite, pas à la prochaine connexion.
            if session.get("is_admin") != user["is_admin"]:
                session["is_admin"] = user["is_admin"]
            return
        session.clear()
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentification requise"}), 401
    return redirect(url_for("auth.login", next=request.path))


def _safe_next_url(raw):
    """N'accepte qu'un chemin relatif interne comme redirection post-connexion.

    Un `next` non filtré transformerait la page de connexion en redirecteur
    ouvert : `?next=https://site-piege.example` renverrait l'utilisateur, une
    fois authentifié, vers un site externe qui peut ressembler à s'y méprendre
    à l'application.
    """
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return None
    return raw


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", error=None, next=request.args.get("next", ""))

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    next_url = _safe_next_url(request.form.get("next", ""))

    user = _find_user_by_username(username) if username else None
    if user is None or not check_password_hash(user["password_hash"], password):
        return render_template("login.html", error="Identifiants invalides", next=next_url or ""), 401

    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["display_name"] = user["display_name"] or user["username"]
    session["is_admin"] = user["is_admin"]
    return redirect(next_url or url_for("accueil.landing"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.app_context_processor
def _inject_current_user():
    if session.get("user_id") is None:
        return {"current_user": None}
    return {
        "current_user": {
            "id": session.get("user_id"),
            "username": session.get("username"),
            "display_name": session.get("display_name"),
            "is_admin": bool(session.get("is_admin")),
        }
    }


def _admin_required(view):
    """Réserve une route aux comptes admin.

    Un accès sans droit admin renvoie 403 plutôt que de rediriger vers
    /login (qui laisserait croire, à tort, qu'il suffit de se reconnecter) —
    l'utilisateur est déjà connecté, il n'a simplement pas ce droit."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return render_template(
                "error.html",
                code=403,
                message="Réservé aux comptes administrateur.",
            ), 403
        return view(*args, **kwargs)
    return wrapped
