"""Crée ou met à jour un compte utilisateur de l'application.

Sert surtout à amorcer le tout premier compte : une fois qu'un compte admin
existe, créer les suivants se fait depuis l'interface (/admin/users), qui
exige justement d'être déjà admin pour y créer quelqu'un — ce que ce script,
en ligne de commande, n'a pas besoin d'exiger.

Usage:
    python -m scripts.create_user identifiant [--name "Nom affiché"] [--admin]

Le mot de passe est demandé de façon interactive (jamais affiché à l'écran,
jamais passé en argument — un argument de ligne de commande reste visible dans
l'historique du shell et dans la liste des processus).
"""
import argparse
import getpass
import os
import sys

import psycopg2
from werkzeug.security import generate_password_hash

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://maps:maps@localhost:5432/maps")
MIN_PASSWORD_LENGTH = 8


def create_user(username, password, display_name=None, is_admin=None):
    """`is_admin=None` laisse le statut admin inchangé pour un compte existant
    (et vaut « non » pour une création). Sans cette distinction, relancer la
    commande pour simplement réinitialiser un mot de passe oublié — sans
    repasser `--admin` — rétrograderait silencieusement un compte admin
    existant en compte simple."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (username, password_hash, display_name, is_admin)
                VALUES (%s, %s, %s, COALESCE(%s, false))
                ON CONFLICT (username) DO UPDATE SET
                    password_hash = EXCLUDED.password_hash,
                    display_name = EXCLUDED.display_name,
                    is_admin = COALESCE(%s, users.is_admin)
                RETURNING id, (xmax = 0) AS was_inserted
                """,
                (username, generate_password_hash(password), display_name or username, is_admin, is_admin),
            )
            user_id, was_inserted = cur.fetchone()
    finally:
        conn.close()
    return user_id, was_inserted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username", help="Identifiant de connexion")
    parser.add_argument("--name", default=None, help="Nom affiché (par défaut : l'identifiant)")
    parser.add_argument(
        "--admin", dest="is_admin", action=argparse.BooleanOptionalAction, default=None,
        help="--admin donne les droits admin, --no-admin les retire ; omis, le statut existant est conservé",
    )
    args = parser.parse_args()

    username = args.username.strip()
    if not username:
        print("L'identifiant ne peut pas être vide.")
        sys.exit(1)

    password = getpass.getpass("Mot de passe : ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Mot de passe trop court ({MIN_PASSWORD_LENGTH} caractères minimum).")
        sys.exit(1)
    if getpass.getpass("Confirmer le mot de passe : ") != password:
        print("Les deux saisies ne correspondent pas.")
        sys.exit(1)

    user_id, was_inserted = create_user(username, password, args.name, args.is_admin)
    action = "créé" if was_inserted else "mot de passe mis à jour pour"
    role = " (admin)" if args.is_admin else ""
    print(f"Compte {action} : « {username} »{role} (id {user_id}).")
