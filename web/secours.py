"""Blueprint « secours » : mode secours quand la base active ne répond plus."""

import psycopg2
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from werkzeug.exceptions import InternalServerError
from werkzeug.security import check_password_hash

from services import db, db_configs, db_migration, etat_base
from services.comptes import _find_user_by_username
from services.etat_base import (
    _db_reachable,
    _mark_db_down,
    _recovery_response,
    _start_database,
    DatabaseUnavailable,
)
from web.admin import _db_label, _log_database_event

bp = Blueprint("secours", __name__)

@bp.app_errorhandler(DatabaseUnavailable)
@bp.app_errorhandler(db_configs.ConfigError)
def _handle_db_unavailable(exc):
    _mark_db_down(str(exc))
    return _recovery_response()


@bp.app_errorhandler(psycopg2.OperationalError)
@bp.app_errorhandler(psycopg2.InterfaceError)
def _handle_db_error(exc):
    # Un verrou ou un délai dépassé sur une requête n'est pas une panne : le
    # mode secours n'est déclenché que si une connexion neuve échoue aussi.
    if _db_reachable():
        current_app.logger.error("Erreur de base de données", exc_info=exc)
        return InternalServerError()
    _mark_db_down(db_migration.explain(exc))
    return _recovery_response()


def _verify_env_admin(username, password):
    """Vérifie un compte admin dans la base du .env ; renvoie le motif d'un refus."""
    if not username or not password:
        return "Identifiant et mot de passe requis."
    try:
        conn = db.connect(db.ENV, connect_timeout=5)
    except psycopg2.Error as exc:
        return "La base du .env ne répond pas non plus : " + db_migration.explain(exc)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash, is_admin FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
    except psycopg2.Error as exc:
        return "Impossible de vérifier le compte dans la base du .env : " + db_migration.explain(exc)
    finally:
        conn.close()
    if row is None or not check_password_hash(row[0], password):
        return "Identifiant ou mot de passe incorrect pour la base du .env."
    if not row[1]:
        return "Ce compte n'est pas administrateur dans la base du .env."
    return None


def _recovery_context():
    try:
        active_id = db_configs.active_id()
    except db_configs.ConfigError:
        active_id = "?"
    env_label = db_configs.env_label()
    label = env_label
    if active_id:
        try:
            label = _db_label(active_id)
        except db_configs.ConfigError:
            label = "Configuration enregistrée depuis la page"
    return {"label": label, "is_env": not active_id, "env_label": env_label, **(etat_base._db_down or {})}


def _render_recovery(error=None, status=200):
    return render_template("recovery.html", error=error, **_recovery_context()), status


@bp.route("/secours")
def db_recovery():
    if etat_base._db_down is None:
        return redirect(url_for("accueil.landing"))
    return _render_recovery()


@bp.route("/secours/reessayer", methods=["POST"])
def db_recovery_retry():
    if etat_base._db_down is None:
        return redirect(url_for("accueil.landing"))
    if not _db_reachable():
        return _render_recovery("La base ne répond toujours pas.", 503)
    if not _start_database():
        return _render_recovery("La base répond, mais la préparation de ses tables a échoué.", 503)
    return redirect(url_for("accueil.landing"))


@bp.route("/secours/revenir-env", methods=["POST"])
def db_recovery_use_env():
    if etat_base._db_down is None:
        return redirect(url_for("accueil.landing"))
    context = _recovery_context()
    if context["is_env"]:
        return _render_recovery(
            "C'est la base du .env elle-même qui ne répond pas : il n'y a pas d'autre base "
            "vers laquelle revenir.", 409,
        )
    username = request.form.get("username", "").strip()
    error = _verify_env_admin(username, request.form.get("password", ""))
    if error:
        return _render_recovery(error, 401)

    db_configs.use_env()
    if not _start_database():
        return _render_recovery("Retour au .env effectué, mais cette base ne répond pas non plus.", 503)
    user = _find_user_by_username(username)
    _log_database_event(
        user["id"] if user else None, "db_recovered",
        {"de": context["label"], "motif": context.get("reason")},
    )
    # Session remise à zéro : l'administrateur se reconnecte, sur une base où
    # son compte vient d'être vérifié.
    session.clear()
    return redirect(url_for("auth.login"))
