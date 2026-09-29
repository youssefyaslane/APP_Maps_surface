"""Session et suppression de compte.

Le cookie de session est signé et valable 30 jours, mais il ne sait pas si le
compte qu'il désigne existe encore. Sans relecture en base à chaque requête, un
compte supprimé depuis la page Comptes garderait tout son accès — droits
d'administration compris — jusqu'à l'expiration du cookie : la suppression
serait purement décorative.

La relecture passe par `_find_user_by_id`, remplacée ici : ces tests tournent
sans base.
"""
import app as app_module


def _client(monkeypatch, compte_en_base, is_admin_en_session=True):
    monkeypatch.setattr(app_module, "_find_user_by_id", lambda uid: compte_en_base)
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=7, username="compte", display_name="compte", is_admin=is_admin_en_session)
    return client


def _compte(is_admin):
    return {"id": 7, "username": "compte", "display_name": "compte", "is_admin": is_admin}


def test_un_compte_existant_passe(monkeypatch):
    client = _client(monkeypatch, _compte(is_admin=False))
    assert client.get("/api/cities").status_code == 200


def test_un_compte_supprime_est_rejete_par_l_api(monkeypatch):
    client = _client(monkeypatch, None)
    assert client.get("/api/cities").status_code == 401


def test_un_compte_supprime_est_renvoye_a_la_connexion(monkeypatch):
    client = _client(monkeypatch, None)
    reponse = client.get("/dashboard")
    assert reponse.status_code == 302
    assert "/login" in reponse.headers["Location"]


def test_la_session_d_un_compte_supprime_est_videe(monkeypatch):
    # Vider la session, et pas seulement refuser la requête : sinon chaque
    # requête suivante relirait la base pour un compte qui n'existe plus.
    client = _client(monkeypatch, None)
    client.get("/api/cities")
    with client.session_transaction() as s:
        assert "user_id" not in s


def test_un_admin_retrograde_en_base_perd_ses_droits_sans_se_reconnecter(monkeypatch):
    # La session dit encore « admin », la base ne le dit plus : la base gagne.
    client = _client(monkeypatch, _compte(is_admin=False), is_admin_en_session=True)
    assert client.get("/admin/users").status_code == 403


def test_on_ne_peut_pas_supprimer_son_propre_compte():
    # Refus décidé avant tout accès à la base : il ne dépend d'aucun état.
    assert app_module._delete_user(7, deleted_by=7) is not None


def test_le_cookie_ne_part_pas_sur_un_post_venu_d_un_autre_site():
    assert app_module.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
