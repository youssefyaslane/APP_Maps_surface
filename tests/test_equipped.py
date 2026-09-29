"""Entreprises déjà équipées de panneaux.

Une entreprise qui a déjà des panneaux n'est plus un prospect : un commercial
la marque « déjà équipée » et elle sort de la liste. Ce qui casserait en
silence : un corps de requête vide qui marquerait quand même, ou un geste
réservé aux administrateurs alors que ce sont les commerciaux qui constatent.
La base est simulée.
"""
import app as app_module


def _client(monkeypatch, is_admin=False):
    monkeypatch.setattr(
        app_module, "_find_user_by_id",
        lambda uid: {"id": 7, "username": "commercial", "display_name": "c", "is_admin": is_admin},
    )
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=7, username="commercial", display_name="c", is_admin=is_admin)
    return client


def _espionner(monkeypatch, existe=True):
    appels = []
    monkeypatch.setattr(
        app_module, "_set_company_equipped",
        lambda company_id, equipped, user_id: appels.append((company_id, equipped, user_id)) or existe,
    )
    return appels


def test_un_commercial_peut_marquer_une_entreprise(monkeypatch):
    appels = _espionner(monkeypatch)
    reponse = _client(monkeypatch).post("/api/companies/12/equipped", json={"equipped": True})
    assert reponse.status_code == 200
    assert appels == [(12, True, 7)]


def test_on_peut_la_retablir(monkeypatch):
    appels = _espionner(monkeypatch)
    reponse = _client(monkeypatch).post("/api/companies/12/equipped", json={"equipped": False})
    assert reponse.status_code == 200
    assert appels == [(12, False, 7)]


def test_sans_valeur_explicite_rien_n_est_marque(monkeypatch):
    appels = _espionner(monkeypatch)
    client = _client(monkeypatch)
    assert client.post("/api/companies/12/equipped", json={}).status_code == 400
    assert client.post("/api/companies/12/equipped", json={"equipped": "oui"}).status_code == 400
    assert client.post("/api/companies/12/equipped", data="equipped=true").status_code == 400
    assert appels == []


def test_une_entreprise_inconnue_repond_404(monkeypatch):
    _espionner(monkeypatch, existe=False)
    reponse = _client(monkeypatch).post("/api/companies/999/equipped", json={"equipped": True})
    assert reponse.status_code == 404
