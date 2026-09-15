"""Mode secours : la base active ne répond plus.

L'application ne doit ni s'arrêter — plus rien sur quoi cliquer —, ni se
rabattre en silence sur une autre base — elle y écrirait vos toits sans que
personne ne le sache. Elle n'affiche plus qu'une page, d'où un administrateur
de la base du .env revient à celle-ci d'un clic. Les bases sont simulées.
"""
import psycopg2
import pytest
from werkzeug.security import generate_password_hash

import app as app_module

PANNE = {"reason": "Serveur introuvable : vérifiez le nom ou l'adresse.", "since": "15/09/2026 à 16:00"}


@pytest.fixture
def secours(monkeypatch):
    monkeypatch.setattr(app_module, "_db_down", dict(PANNE))
    monkeypatch.setattr(app_module, "_reset_db_pool", lambda: None)
    monkeypatch.setattr(app_module.db_configs, "active_id", lambda: "abc")
    monkeypatch.setattr(app_module, "_db_label", lambda config_id: "test2")
    monkeypatch.setattr(app_module.db_configs, "env_label", lambda: "Production")
    return app_module.app.test_client()


def test_le_retour_porte_le_nom_choisi_pour_la_base_du_env(secours):
    assert "Revenir à « Production »" in secours.get("/secours").get_data(as_text=True)


def test_toute_page_mene_a_la_page_de_secours(secours):
    reponse = secours.get("/dashboard")
    assert reponse.status_code == 302
    assert reponse.headers["Location"].endswith("/secours")


def test_l_api_repond_503(secours):
    assert secours.get("/api/cities").status_code == 503


def test_la_page_de_secours_s_affiche_sans_connexion(secours):
    reponse = secours.get("/secours")
    assert reponse.status_code == 200
    assert "test2" in reponse.get_data(as_text=True)


def test_de_mauvais_identifiants_laissent_en_secours(secours, monkeypatch):
    bascules = []
    monkeypatch.setattr(app_module, "_verify_env_admin", lambda u, p: "Identifiant ou mot de passe incorrect.")
    monkeypatch.setattr(app_module.db_configs, "use_env", lambda: bascules.append("env"))
    reponse = secours.post("/secours/revenir-env", data={"username": "x", "password": "y"})
    assert reponse.status_code == 401
    assert bascules == []
    assert app_module._db_down is not None


def test_un_admin_du_env_revient_d_un_clic(secours, monkeypatch):
    bascules = []

    def redemarre():
        app_module._db_down = None
        return True

    monkeypatch.setattr(app_module, "_verify_env_admin", lambda u, p: None)
    monkeypatch.setattr(app_module.db_configs, "use_env", lambda: bascules.append("env"))
    monkeypatch.setattr(app_module, "_start_database", redemarre)
    monkeypatch.setattr(app_module, "_find_user_by_username", lambda u: {"id": 6})
    monkeypatch.setattr(app_module, "_log_database_event", lambda *a, **k: None)
    reponse = secours.post("/secours/revenir-env", data={"username": "admin_netis", "password": "ok"})
    assert reponse.status_code == 302
    assert "/login" in reponse.headers["Location"]
    assert bascules == ["env"]


def test_pas_de_retour_quand_c_est_le_env_qui_est_en_panne(secours, monkeypatch):
    monkeypatch.setattr(app_module.db_configs, "active_id", lambda: None)
    reponse = secours.post("/secours/revenir-env", data={"username": "a", "password": "b"})
    assert reponse.status_code == 409


def test_hors_panne_la_page_de_secours_renvoie_a_l_accueil(monkeypatch):
    monkeypatch.setattr(app_module, "_db_down", None)
    monkeypatch.setattr(
        app_module, "_find_user_by_id",
        lambda uid: {"id": 6, "username": "admin_netis", "display_name": "a", "is_admin": True},
    )
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=6, username="admin_netis", display_name="a", is_admin=True)
    assert client.get("/secours").status_code == 302


def test_une_erreur_de_requete_n_est_pas_une_panne(monkeypatch):
    # Un verrou ou un délai dépassé : la base répond encore, pas de mode secours.
    monkeypatch.setattr(app_module, "_db_down", None)
    monkeypatch.setattr(app_module, "_reset_db_pool", lambda: None)
    monkeypatch.setattr(app_module, "_db_reachable", lambda: True)
    with app_module.app.test_request_context("/dashboard"):
        reponse = app_module._handle_db_error(psycopg2.OperationalError("deadlock detected"))
    assert reponse.code == 500
    assert app_module._db_down is None


def test_une_base_qui_ne_repond_plus_declenche_le_secours(monkeypatch):
    monkeypatch.setattr(app_module, "_db_down", None)
    monkeypatch.setattr(app_module, "_reset_db_pool", lambda: None)
    monkeypatch.setattr(app_module, "_db_reachable", lambda: False)
    with app_module.app.test_request_context("/dashboard"):
        app_module._handle_db_error(psycopg2.OperationalError("server closed the connection"))
    assert app_module._db_down is not None


def test_un_demarrage_sans_base_passe_en_secours_au_lieu_de_s_arreter(monkeypatch):
    monkeypatch.setattr(app_module, "_db_down", None)
    monkeypatch.setattr(app_module, "_reset_db_pool", lambda: None)

    def base_absente():
        raise app_module.DatabaseUnavailable("Serveur introuvable")

    monkeypatch.setattr(app_module, "_init_db", base_absente)
    assert app_module._start_database() is False
    assert app_module._db_down["reason"] == "Serveur introuvable"


# --- Vérification du compte dans la base du .env ---------------------------

class _Curseur:
    def __init__(self, ligne):
        self.ligne = ligne

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args):
        pass

    def fetchone(self):
        return self.ligne


class _Connexion:
    def __init__(self, ligne):
        self.ligne = ligne

    def cursor(self):
        return _Curseur(self.ligne)

    def close(self):
        pass


@pytest.mark.parametrize(
    "ligne, mot_de_passe, accepte",
    [
        ((generate_password_hash("bon"), True), "bon", True),
        ((generate_password_hash("bon"), True), "mauvais", False),
        ((generate_password_hash("bon"), False), "bon", False),  # compte non admin
        (None, "bon", False),  # compte inconnu
    ],
)
def test_seul_un_admin_du_env_peut_revenir(monkeypatch, ligne, mot_de_passe, accepte):
    monkeypatch.setattr(app_module.db, "connect", lambda *a, **k: _Connexion(ligne))
    refus = app_module._verify_env_admin("admin_netis", mot_de_passe)
    assert (refus is None) is accepte
