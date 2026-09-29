"""Comptes utilisateurs et journal des actions (audit_log)."""

import json

from services.etat_base import _get_db_pool

def _find_user_by_username(username):
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, display_name, is_admin FROM users WHERE username = %s",
                (username,),
            )
            row = cur.fetchone()
    finally:
        pool.putconn(conn)
    if row is None:
        return None
    return {
        "id": row[0], "username": row[1], "password_hash": row[2],
        "display_name": row[3], "is_admin": row[4],
    }


def _find_user_by_id(user_id):
    """Compte encore présent en base, ou None s'il a été supprimé depuis."""
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, is_admin FROM users WHERE id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    finally:
        pool.putconn(conn)
    if row is None:
        return None
    return {"id": row[0], "username": row[1], "display_name": row[2], "is_admin": row[3]}


def _delete_user(user_id, deleted_by):
    """Supprime un compte ; renvoie None si c'est fait, sinon le motif du refus.

    Ce qu'il laisse derrière lui survit : les clés étrangères de ia_segments et
    d'audit_log sont en ON DELETE SET NULL, seul l'auteur s'efface. Ses toits
    tracés restent donc sur la carte et au tableau de bord.
    """
    if user_id == deleted_by:
        return "Vous ne pouvez pas supprimer votre propre compte."

    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            # Verrouiller les administrateurs avant de les compter : deux admins
            # qui se suppriment l'un l'autre au même instant verraient sinon
            # chacun « il en reste un autre », et l'outil se retrouverait sans
            # personne pour créer de comptes.
            cur.execute("SELECT id FROM users WHERE is_admin FOR UPDATE")
            admins = {r[0] for r in cur.fetchall()}

            cur.execute("SELECT username, is_admin FROM users WHERE id = %s FOR UPDATE", (user_id,))
            row = cur.fetchone()
            if row is None:
                return "Ce compte n'existe plus."
            username, is_admin = row
            if is_admin and not (admins - {user_id}):
                return "Impossible de supprimer le dernier administrateur."

            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
            # L'identifiant est recopié dans le détail : la ligne effacée,
            # entity_id ne désigne plus rien de lisible.
            _log_audit(
                cur, deleted_by, "user_deleted", "users", user_id,
                {"username": username, "is_admin": is_admin},
            )
    finally:
        pool.putconn(conn)
    return None


def _list_users():
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, display_name, is_admin, created_at FROM users ORDER BY username"
            )
            rows = cur.fetchall()
    finally:
        pool.putconn(conn)
    return [
        {"id": r[0], "username": r[1], "display_name": r[2], "is_admin": r[3], "created_at": r[4]}
        for r in rows
    ]


def _create_user(username, password, display_name=None, is_admin=False, created_by=None):
    """Crée un compte, ou renvoie None si l'identifiant existe déjà.

    Contrairement à `scripts/create_user.py` (pensé pour la ligne de commande,
    où relancer la commande pour changer un mot de passe oublié est le geste
    naturel), la page d'administration ne doit pas silencieusement écraser un
    compte existant si on se trompe d'identifiant en le créant : mieux vaut un
    message d'erreur explicite qu'un mot de passe remplacé par erreur.
    """
    from werkzeug.security import generate_password_hash

    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
            if cur.fetchone() is not None:
                return None
            cur.execute(
                """
                INSERT INTO users (username, password_hash, display_name, is_admin)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (username, generate_password_hash(password), display_name or username, is_admin),
            )
            new_id = cur.fetchone()[0]
            _log_audit(
                cur, created_by, "user_created", "users", new_id,
                {"username": username, "is_admin": is_admin},
            )
            return new_id
    finally:
        pool.putconn(conn)


def _log_audit(cur, user_id, action, entity, entity_id, details=None):
    """Enregistre une action dans le même curseur/transaction que la mutation
    qu'elle décrit : soit les deux sont committées ensemble, soit aucune."""
    cur.execute(
        """
        INSERT INTO audit_log (user_id, action, entity, entity_id, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, action, entity, entity_id, json.dumps(details) if details is not None else None),
    )
