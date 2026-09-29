"""Point d'entrée de l'application : carte du Maroc et prospection solaire.

L'application est découpée en blueprints Flask (dossier web/) :
  auth              connexion, déconnexion, contrôle d'accès de toutes les pages
  accueil           page d'accueil publique (/)
  carte             la carte (/carte) et son API : bâtiments, segmentation IA, toits
  tableau_de_bord   tableau de bord commercial (/dashboard) et son API : prospects, export
  admin             comptes utilisateurs et configuration de la base (/admin/...)
  secours           mode secours quand la base active ne répond plus (/secours)
Tout ce qui n'est pas une route est dans services/ : base de données (connexion,
configurations, schéma, migration), calculs (potentiel solaire, géométrie,
segmentation IA), toits, prospects et comptes.
"""
import os
import secrets
import threading
from datetime import timedelta

from flask import Flask

from services import segmentation
from services.etat_base import _start_database
from services.reglages import CACHE_DIR
from services.toits import _prewarm_cities
from web import accueil, admin, auth, carte, secours, tableau_de_bord

app = Flask(__name__)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)
# Le cookie de session n'accompagne pas une requête POST partie d'un autre site :
# sans ce réglage, une page tierce pourrait faire soumettre au navigateur d'un
# administrateur connecté le formulaire de suppression d'un compte. Chrome
# applique déjà Lax par défaut, pas tous les navigateurs — ici c'est explicite.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


def _get_secret_key():
    """Clé de signature des sessions Flask.

    `SECRET_KEY` prime si elle est fournie. Sinon, une clé aléatoire est générée
    une fois puis écrite dans CACHE_DIR (le volume `cache_data`, déjà utilisé
    pour le modèle IA et le cache OSM) : elle survit ainsi aux redémarrages du
    conteneur sans que personne n'ait à la configurer, et sans se retrouver en
    clair dans docker-compose.yml. La régénérer déconnecte tout le monde — c'est le seul effet de
    bord d'un volume perdu ou d'un CACHE_DIR changé.
    """
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key

    path = os.path.join(CACHE_DIR, "secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key

    key = secrets.token_hex(32)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(key)
    os.replace(tmp_path, path)
    return key


app.secret_key = _get_secret_key()

# « auth » d'abord : son contrôle d'accès s'applique à toutes les pages.
for module in (auth, accueil, carte, tableau_de_bord, admin, secours):
    app.register_blueprint(module.bp)


def _prewarm_segmentation_model():
    """Télécharge le checkpoint et charge MobileSAM en mémoire pour un premier clic rapide."""
    try:
        segmentation._get_predictor()
    except Exception:
        pass


if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    # Base injoignable au démarrage : l'application démarre quand même, en mode
    # secours, pour qu'un administrateur puisse revenir au .env d'un clic.
    if _start_database():
        threading.Thread(target=_prewarm_cities, daemon=True).start()
    threading.Thread(target=_prewarm_segmentation_model, daemon=True).start()

if __name__ == "__main__":
    app.run(debug=True, threaded=True)
