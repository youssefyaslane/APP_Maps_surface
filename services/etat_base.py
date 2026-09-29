"""Connexion à la base active : pool de connexions, création des tables, et état
« base injoignable » qui fait passer l'application en mode secours.

Ces deux états sont modifiés en cours de route : les autres modules les lisent
par ce module (etat_base._db_down), jamais par une copie importée."""

import time

import psycopg2
import psycopg2.pool
from flask import jsonify, redirect, request, url_for

from services import db, db_configs, db_migration, schema as table_schema

_db_pool = None
# Renseigné quand la base active est injoignable : l'application passe alors
# en mode secours (voir « Mode secours » plus bas) au lieu de s'arrêter.
_db_down = None


class DatabaseUnavailable(RuntimeError):
    """La base active ne répond pas."""


def _get_db_pool():
    global _db_pool
    if _db_pool is not None:
        return _db_pool
    last_error = None
    # Cinq tentatives, et non plus quinze : ces reprises couvraient le démarrage
    # du conteneur de base local, retiré depuis la migration vers le serveur
    # partagé. Chaque tentative de plus ne fait que retarder le mode secours.
    for _ in range(5):
        try:
            # Relu à chaque création du pool : c'est ce qui fait suivre à
            # l'application la base activée depuis la page d'administration.
            dsn, kwargs = db.connect_params()
            # Sans délai, une adresse injoignable bloque la connexion pendant
            # des minutes au lieu d'échouer.
            _db_pool = psycopg2.pool.ThreadedConnectionPool(
                1, 10, dsn, connect_timeout=5, **kwargs
            )
            return _db_pool
        except psycopg2.OperationalError as exc:
            last_error = exc
            time.sleep(1)
    raise DatabaseUnavailable(db_migration.explain(last_error))


def _reset_db_pool():
    """Abandonne le pool courant : la requête suivante se connecte à la base
    désormais active. Une requête en cours sur l'ancienne base peut échouer
    une fois — prix accepté pour une bascule rare, faite par un administrateur."""
    global _db_pool
    old, _db_pool = _db_pool, None
    if old is not None:
        try:
            old.closeall()
        except psycopg2.Error:
            pass


def _init_db():
    """Crée les tables manquantes dans la base active (définitions : services/schema.py)."""
    pool = _get_db_pool()
    conn = pool.getconn()
    try:
        with conn, conn.cursor() as cur:
            table_schema.create_all(cur)
    finally:
        pool.putconn(conn)


# --- Mode secours ----------------------------------------------------------
#
# Si la base active ne répond plus — au démarrage ou en cours de route —,
# l'application ne se rabat jamais toute seule sur une autre base : elle y
# écrirait vos toits sans que personne ne s'en aperçoive. Elle n'affiche plus
# qu'une page, depuis laquelle un administrateur réessaie, ou revient d'un clic
# à la base du .env. C'est dans cette base-là qu'il prouve être admin : celle
# qui ne répond pas ne peut pas vérifier son compte.


def _mark_db_down(reason):
    global _db_down
    _db_down = {"reason": reason, "since": time.strftime("%d/%m/%Y à %H:%M")}
    _reset_db_pool()


def _db_reachable():
    """Vrai si la base active accepte une connexion neuve, sans passer par le pool."""
    try:
        conn = db.connect(connect_timeout=3)
    except (psycopg2.Error, db_configs.ConfigError):
        return False
    conn.close()
    return True


def _start_database():
    """Prépare la base active ; passe en mode secours si elle ne répond pas."""
    global _db_down
    try:
        _init_db()
    except (DatabaseUnavailable, db_configs.ConfigError) as exc:
        _mark_db_down(str(exc))
        return False
    _db_down = None
    return True


def _recovery_response():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Base de données injoignable"}), 503
    return redirect(url_for("secours.db_recovery"))
