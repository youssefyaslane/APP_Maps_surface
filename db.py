"""Connexion à PostgreSQL — un seul endroit pour l'application et les scripts.

L'adresse de la base était recopiée dans app.py et dans cinq scripts, avec deux
valeurs par défaut différentes (`db:5432` d'un côté, `localhost:5432` de
l'autre). Elle vit désormais ici.

La base est désignée par les variables standard de libpq — PGHOST, PGPORT,
PGDATABASE, PGUSER, PGPASSWORD — que docker-compose.yml remplit depuis .env.
Passer par elles plutôt que par une URL évite d'avoir à encoder le mot de
passe : un `@` ou un `/` dedans casserait une URL sans message clair.
DATABASE_URL reste accepté et prime s'il est défini.

DB_SCHEMA désigne le schéma qui porte les tables. Sur un serveur partagé, les
tables de l'application ne vivent pas dans `public` : le chemin de recherche
est fixé à ce seul schéma, si bien que les requêtes (qui nomment les tables
sans schéma) ne peuvent ni lire ni modifier les tables des autres projets.

Une configuration activée depuis la page d'administration de la base
(db_configs.py) prend le pas sur le .env — pour l'application comme pour les
scripts, qui écrivent donc toujours dans la même base que la page affiche.
"""
import os

import psycopg2
import psycopg2.extensions

import db_configs

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "").strip() or None

# Désigne la base du .env, quelle que soit la configuration active.
ENV = db_configs.ENV_ID


def search_path_option(schema):
    """Option libpq qui fixe le chemin de recherche sur `schema`.

    Deux niveaux d'échappement : le nom est d'abord cité comme identifiant SQL
    (un schéma nommé `solar intelligence` contient un espace), puis les espaces
    et barres obliques inverses sont échappés pour libpq, qui découpe sa chaîne
    `options` sur les espaces.
    """
    ident = '"' + schema.replace('"', '""') + '"'
    escaped = ident.replace("\\", "\\\\").replace(" ", "\\ ")
    return f"-csearch_path={escaped}"


def connect_kwargs():
    """Arguments de connexion pour la base du .env (schéma compris)."""
    if DB_SCHEMA is None:
        return {}
    return {"options": search_path_option(DB_SCHEMA)}


def _resolve(target):
    if target is None:
        return db_configs.active_target() or ENV
    return target


def connect_params(target=None):
    """(dsn, kwargs) pour psycopg2.connect ou un pool de connexions.

    `target` : None pour la base active, ENV pour celle du .env, ou une
    configuration déchiffrée de la page d'administration.
    """
    target = _resolve(target)
    if target == ENV:
        return DATABASE_URL, connect_kwargs()
    # Tous les paramètres sont donnés : libpq ne complète plus rien avec les
    # PG* du .env, qui désignent une autre base.
    kwargs = {
        "host": target["host"],
        "port": target["port"],
        "dbname": target["dbname"],
        "user": target["user"],
        "password": target["password"],
        "sslmode": target.get("sslmode") or "prefer",
    }
    if target.get("schema"):
        kwargs["options"] = search_path_option(target["schema"])
    return "", kwargs


def connect(target=None, **extra):
    dsn, kwargs = connect_params(target)
    return psycopg2.connect(dsn, **{**kwargs, **extra})


def schema_of(target=None):
    """Schéma dans lequel vivent les tables de cette base."""
    target = _resolve(target)
    if target == ENV:
        return DB_SCHEMA or "public"
    return target.get("schema") or "public"


def env_description():
    """La base du .env telle que l'affiche la page — jamais son mot de passe."""
    parsed = {}
    if DATABASE_URL:
        try:
            parsed = psycopg2.extensions.parse_dsn(DATABASE_URL)
        except psycopg2.ProgrammingError:
            parsed = {}
    return {
        "id": ENV,
        "label": db_configs.env_label(),
        "db_type": "postgresql",
        "host": parsed.get("host") or os.environ.get("PGHOST") or "localhost",
        "port": int(parsed.get("port") or os.environ.get("PGPORT") or 5432),
        "dbname": parsed.get("dbname") or os.environ.get("PGDATABASE") or "",
        "user": parsed.get("user") or os.environ.get("PGUSER") or "",
        "schema": DB_SCHEMA or "",
        "sslmode": parsed.get("sslmode") or os.environ.get("PGSSLMODE") or "prefer",
        "created_at": None,
    }
