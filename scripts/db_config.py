"""Base active : consultation, et retour au .env sans passer par l'application.

En temps normal, le retour au .env se fait d'un clic, depuis la page de secours
que l'application affiche quand la base active ne répond plus. Ce script est
le dernier recours, pour le cas où cette page elle-même serait inaccessible :
il ne se connecte à aucune base et n'importe pas app.py, il fonctionne donc
même serveur web arrêté.

    docker compose run --rm web python -m scripts.db_config status
    docker compose run --rm web python -m scripts.db_config use-env
"""
import sys

import db_configs


def _status():
    data = db_configs.load()
    active = next((c for c in data["configs"] if c["id"] == data["active"]), None)
    if active is None:
        print("Base active : celle du fichier .env (aucune configuration de la page n'est activée).")
    else:
        print(
            f"Base active : « {active['label']} » — {active['host']}:{active['port']}, "
            f"base {active['dbname']}, schéma {active['schema'] or 'public'}, "
            f"utilisateur {active['user']}."
        )
    print(f"{len(data['configs'])} configuration(s) enregistrée(s) depuis la page.")


def _use_env():
    aside = db_configs.use_env()
    if aside:
        print(f"Fichier de configurations illisible : conservé sous {aside}.")
    print("Retour à la base du fichier .env. Relancez l'application : docker compose up -d")


def main(argv):
    command = argv[1] if len(argv) > 1 else "status"
    if command == "status":
        try:
            _status()
        except db_configs.ConfigError as exc:
            print(exc)
            return 1
        return 0
    if command == "use-env":
        _use_env()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
