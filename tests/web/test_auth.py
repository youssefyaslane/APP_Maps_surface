"""Authentification : sanitisation de la redirection post-connexion et
politique d'accès public/privé.

Le reste de l'authentification (hachage, vérification du mot de passe,
session) délègue à Flask/Werkzeug — testé en amont, inutile de le retester
ici. Ce qui est spécifique à ce projet, et qui casse silencieusement si on le
change sans y penser, c'est ce qui suit.
"""
import app


def test_un_chemin_relatif_est_accepte():
    assert app._safe_next_url("/dashboard") == "/dashboard"
    assert app._safe_next_url("/carte?lat=33.5&lon=-7.6") == "/carte?lat=33.5&lon=-7.6"


def test_une_url_absolue_est_rejetee():
    # Sans ce filtre, ?next=https://site-piege.example renverrait l'utilisateur,
    # une fois authentifié, vers un site externe — la page de connexion
    # deviendrait un redirecteur ouvert exploitable en hameçonnage.
    assert app._safe_next_url("https://site-piege.example") is None
    assert app._safe_next_url("http://site-piege.example/dashboard") is None


def test_un_double_slash_est_rejete():
    # //site-piege.example est interprété par le navigateur comme une URL
    # absolue (protocole relatif), pas comme un chemin — même risque que
    # l'URL absolue, sous une forme qui passerait un filtre trop naïf.
    assert app._safe_next_url("//site-piege.example") is None


def test_une_valeur_vide_ou_absente_est_rejetee():
    assert app._safe_next_url("") is None
    assert app._safe_next_url(None) is None


def test_seule_la_connexion_et_le_statique_sont_publics():
    # Toute nouvelle route ajoutée plus tard est protégée par défaut : c'est
    # avant_request qui applique la règle, pas un décorateur qu'on pourrait
    # oublier d'ajouter — l'oubli précis qui avait laissé la suppression de
    # toit accessible sans authentification.
    assert app.PUBLIC_ENDPOINTS == {"login", "static"}
