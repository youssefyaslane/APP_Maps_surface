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
"""
import os

import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "").strip() or None


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
    """Arguments à passer à psycopg2.connect (ou à un pool de connexions)."""
    if DB_SCHEMA is None:
        return {}
    return {"options": search_path_option(DB_SCHEMA)}


def connect():
    return psycopg2.connect(DATABASE_URL, **connect_kwargs())
