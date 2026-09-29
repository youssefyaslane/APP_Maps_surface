"""Blueprint « admin » : comptes utilisateurs et configuration de la base de données."""

import psycopg2
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from services import db, db_configs, db_migration, schema as table_schema
from services.comptes import _create_user, _delete_user, _list_users, _log_audit
from services.etat_base import _get_db_pool, _reset_db_pool
from web.auth import _admin_required

bp = Blueprint("admin", __name__)

MIN_PASSWORD_LENGTH = 8


@bp.route("/admin/users", methods=["GET", "POST"])
@_admin_required
def admin_users():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        display_name = request.form.get("display_name", "").strip()
        is_admin = request.form.get("is_admin") == "on"

        if not username:
            error = "L'identifiant est obligatoire."
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = f"Mot de passe trop court ({MIN_PASSWORD_LENGTH} caractères minimum)."
        elif password != confirm:
            error = "Les deux mots de passe ne correspondent pas."
        else:
            new_id = _create_user(
                username, password, display_name or None, is_admin,
                created_by=session.get("user_id"),
            )
            if new_id is None:
                error = f"L'identifiant « {username} » existe déjà."
            else:
                return redirect(url_for("admin.admin_users"))

    return render_template(
        "admin/admin_users.html", users=_list_users(), error=error, me=session.get("user_id")
    )


@bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@_admin_required
def admin_delete_user(user_id):
    error = _delete_user(user_id, deleted_by=session.get("user_id"))
    if error:
        return render_template(
            "admin/admin_users.html", users=_list_users(), error=error, me=session.get("user_id")
        ), 409
    return redirect(url_for("admin.admin_users"))


# --- Administration de la base -------------------------------------------
#
# Page réservée aux administrateurs : enregistrer d'autres bases PostgreSQL,
# les tester, y recopier les données, puis y faire passer l'application.
# Les configurations vivent hors de la base (services/db_configs.py) ; le diagnostic
# et la copie, dans services/db_migration.py.

DB_ADMIN_MESSAGES = {
    "added": "Configuration enregistrée. Testez-la, puis migrez les données avant de l'activer.",
    "updated": "Configuration modifiée.",
    "deleted": "Configuration supprimée. La base elle-même n'a pas été touchée.",
    "activated": "Base activée : l'application travaille désormais sur cette base.",
}


def _current_admin():
    return {"id": session.get("user_id"), "username": session.get("username")}


def _active_config_id():
    return db_configs.active_id() or db_configs.ENV_ID


def _db_target(config_id):
    """Cible déchiffrée pour un identifiant de la page, ou la base du .env."""
    if config_id == db_configs.ENV_ID:
        return db.ENV
    return db_configs.target(config_id)


def _db_label(config_id):
    if config_id == db_configs.ENV_ID:
        return db.env_description()["label"]
    configs, _ = db_configs.list_public()
    return next((c["label"] for c in configs if c["id"] == config_id), config_id)


def _log_database_event(user_id, action, details):
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            _log_audit(cur, user_id, action, "database", None, details)
    finally:
        pool.putconn(conn)


def _activate_database(config_id, admin, force=False):
    """Fait passer l'application sur une autre base.

    Renvoie None, ou (motif du refus, forçable). Les refus non forçables évitent
    une situation dont on ne sort qu'en ligne de commande : une base sans les
    tables, ou une base où votre compte n'existe pas — la relecture du compte à
    chaque requête vous déconnecterait sur-le-champ.

    Le refus forçable protège le travail récent : activer une base en retard
    sur la base active, c'est laisser ce travail derrière soi — et une
    synchronisation dans l'autre sens l'effacerait ensuite. C'est ici, et non
    au moment de synchroniser, que ce danger se voit de façon sûre : les
    scripts d'import écrivent sans rien laisser au journal.
    """
    if db_migration.job_status().get("state") == "running":
        return "Une migration est en cours : attendez qu'elle soit terminée.", False
    previous = _active_config_id()
    if config_id == previous:
        return "C'est déjà la base active.", False
    try:
        target = _db_target(config_id)
    except db_configs.ConfigError as exc:
        return str(exc), False

    report = db_migration.inspect(target, admin=admin)
    if not report["ok"]:
        return f"Connexion impossible : {report['error']}", False
    if not report["schema_exists"]:
        return f"Le schéma « {report['schema']} » n'existe pas sur ce serveur.", False
    missing = [t for t in table_schema.REQUIRED_TABLES if report["tables"].get(t) is None]
    if missing:
        return (
            "Tables absentes sur cette base (" + ", ".join(missing) + ") : "
            "migrez d'abord les données.", False,
        )
    if not report["admin_present"]:
        return (
            "Votre compte administrateur n'existe pas sur cette base : vous seriez "
            "déconnecté aussitôt, sans pouvoir revenir sur cette page. Migrez d'abord les données.",
            False,
        )

    if not force:
        try:
            comparison = db_migration.sync(target)
        except (db_migration.MigrationError, db_configs.ConfigError) as exc:
            return str(exc), False
        except psycopg2.Error as exc:
            return f"Comparaison impossible : {db_migration.explain(exc)}", False
        if not db_migration.is_identical(comparison["diff"]):
            totals = {
                k: sum(d[k] for d in comparison["diff"].values())
                for k in ("added", "changed", "removed")
            }
            return (
                f"« {_db_label(config_id)} » n'a pas les dernières données de la base active "
                f"({totals['added']} ligne(s) à ajouter, {totals['changed']} à modifier, "
                f"{totals['removed']} à retirer). Migrez d'abord les données vers elle, puis "
                f"activez-la : sinon, le travail fait depuis la dernière migration resterait "
                f"seulement sur « {_db_label(previous)} ».",
                True,
            )

    db_configs.set_active(None if config_id == db_configs.ENV_ID else config_id)
    _reset_db_pool()
    try:
        # Première écriture sur la nouvelle base. Si elle échoue, retour
        # immédiat à la précédente plutôt qu'une application privée de base.
        _log_database_event(
            admin["id"], "db_activated",
            {"de": _db_label(previous), "vers": _db_label(config_id)},
        )
    except Exception as exc:
        db_configs.set_active(None if previous == db_configs.ENV_ID else previous)
        _reset_db_pool()
        return f"La base n'a pas répondu après la bascule ({exc}) : retour à la précédente.", False
    return None


def _render_database_admin(error=None, status=200, force_url=None):
    try:
        configs, active_id = db_configs.list_public()
    except db_configs.ConfigError as exc:
        configs, active_id, error = [], None, error or str(exc)
    active_id = active_id or db_configs.ENV_ID

    rows = [db.env_description()] + configs
    for row in rows:
        row["is_env"] = row["id"] == db_configs.ENV_ID
        row["is_active"] = row["id"] == active_id
        created = row.get("created_at") or ""
        row["created_label"] = (
            f"{created[8:10]}/{created[5:7]}/{created[:4]}" if len(created) >= 10 else "—"
        )
    active = next((r for r in rows if r["is_active"]), rows[0])

    report = db_migration.inspect(None)
    tables = report.get("tables") or {}

    def nombre(value):
        return f"{value:,}".replace(",", " ") if isinstance(value, int) else "—"

    content = (
        f"{nombre(tables.get('companies'))} entreprises · "
        f"{nombre(tables.get('ia_segments'))} toits tracés · "
        f"{nombre(tables.get('users'))} comptes"
    )
    return render_template(
        "admin/admin_database.html",
        rows=rows,
        active=active,
        report=report,
        content=content,
        key_ok=db_configs.key_configured(),
        job=db_migration.job_status(),
        message=DB_ADMIN_MESSAGES.get(request.args.get("ok")),
        error=error,
        force_url=force_url,
    ), status


@bp.route("/admin/database")
@_admin_required
def admin_database():
    return _render_database_admin()


@bp.route("/admin/database/configs", methods=["POST"])
@_admin_required
def admin_database_add():
    try:
        config_id = db_configs.add(request.form)
    except db_configs.ConfigError as exc:
        return _render_database_admin(error=str(exc), status=400)
    _log_database_event(session.get("user_id"), "db_config_added", {"label": _db_label(config_id)})
    return redirect(url_for("admin.admin_database", ok="added"))


@bp.route("/admin/database/configs/<config_id>/edit", methods=["POST"])
@_admin_required
def admin_database_edit(config_id):
    try:
        if config_id == db_configs.ENV_ID:
            # Seul le nom se règle ici : la connexion vient du fichier .env.
            db_configs.set_env_label(request.form.get("label"))
        else:
            db_configs.update(config_id, request.form)
    except db_configs.ConfigError as exc:
        return _render_database_admin(error=str(exc), status=409)
    return redirect(url_for("admin.admin_database", ok="updated"))


@bp.route("/admin/database/configs/<config_id>/delete", methods=["POST"])
@_admin_required
def admin_database_delete(config_id):
    label = _db_label(config_id)
    try:
        db_configs.delete(config_id)
    except db_configs.ConfigError as exc:
        return _render_database_admin(error=str(exc), status=409)
    _log_database_event(session.get("user_id"), "db_config_deleted", {"label": label})
    return redirect(url_for("admin.admin_database", ok="deleted"))


@bp.route("/admin/database/configs/<config_id>/test", methods=["POST"])
@_admin_required
def admin_database_test(config_id):
    try:
        target = _db_target(config_id)
    except db_configs.ConfigError as exc:
        return jsonify({"ok": False, "error": str(exc)})
    return jsonify(db_migration.inspect(target, admin=_current_admin()))


@bp.route("/admin/database/configs/<config_id>/migrate", methods=["POST"])
@_admin_required
def admin_database_migrate(config_id):
    if config_id == _active_config_id():
        return jsonify({"error": "C'est déjà la base active : il n'y a rien à y recopier."}), 409
    try:
        target = _db_target(config_id)
    except db_configs.ConfigError as exc:
        return jsonify({"error": str(exc)}), 409
    label = _db_label(config_id)
    apply = request.form.get("mode") == "apply"
    admin_id = session.get("user_id")

    def journaliser():
        # Dans la base active, avant l'instantané : la cible en reçoit la copie.
        # Aucune écriture ne va jamais dans une base inactive, sinon ses
        # identifiants divergeraient de ceux de la base active.
        _log_database_event(admin_id, "db_synced", {"vers": label})

    try:
        db_migration.start(target, label, apply=apply, before_apply=journaliser if apply else None)
    except db_migration.MigrationError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"started": True, "mode": "apply" if apply else "preview"})


@bp.route("/admin/database/migration")
@_admin_required
def admin_database_migration_status():
    return jsonify(db_migration.job_status())


@bp.route("/admin/database/configs/<config_id>/activate", methods=["POST"])
@_admin_required
def admin_database_activate(config_id):
    refusal = _activate_database(
        config_id, _current_admin(), force=request.form.get("force") == "1"
    )
    if refusal:
        error, can_force = refusal
        force_url = url_for("admin.admin_database_activate", config_id=config_id) if can_force else None
        return _render_database_admin(error=error, status=409, force_url=force_url)
    return redirect(url_for("admin.admin_database", ok="activated"))
