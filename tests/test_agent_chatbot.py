"""Chatbot de recherche (agent_chatbot_workflow) et sa route /api/chatbot.

Ce qui casserait en silence : une recherche lancée sans ville (le modèle l'a
devinée), plus de 20 résultats demandés à Apify, une même ville écrite de deux
façons, ou une conversation qui oublie la requête donnée au message précédent.
Le modèle OpenAI et la base sont simulés.
"""
import pytest

pytest.importorskip("langgraph")

import app as app_module  # noqa: E402
from agent_chatbot_workflow import chatbot as cb  # noqa: E402
from agent_chatbot_workflow.graphe import construire_graphe, repondre  # noqa: E402
from web import auth  # noqa: E402
from web import chatbot as route  # noqa: E402

VILLES = {"casablanca": "Casablanca", "mohammedia": "Mohammédia"}


class FauxModele:
    """Rend les extractions données, une par appel, comme le ferait OpenAI."""

    def __init__(self, *extractions):
        self.extractions = list(extractions)
        self.appels = []

    def invoke(self, messages):
        self.appels.append(messages)
        return self.extractions.pop(0)


def _demande(requetes=(), ville=None):
    return cb.Demande(requetes=list(requetes), ville=ville)


def test_une_demande_complete_est_prete_avec_20_resultats_au_plus():
    r = cb.verifier(_demande(["usine"], "casablanca"), VILLES)
    assert r == {"requetes": ["usine"], "ville": "Casablanca", "max_resultats": 20, "pret": True}


def test_sans_ville_le_chatbot_la_demande_au_lieu_de_deviner():
    r = cb.verifier(_demande(["usine"], None), VILLES)
    assert not r["pret"]
    assert "Dans quelle ville" in cb.message_pour(r)


def test_sans_requete_le_chatbot_demande_quoi_chercher():
    r = cb.verifier(_demande([], "Casablanca"), VILLES)
    assert not r["pret"]
    assert "Quel type d'entreprise" in cb.message_pour(r)
    assert "Que voulez-vous chercher" in cb.message_pour(cb.verifier(_demande(), VILLES))


def test_les_requetes_sont_nettoyees_et_limitees():
    r = cb.verifier(_demande(["  usine ", "Usine", "", "entrepôt", "laboratoire", "atelier"], "Casablanca"), VILLES)
    assert r["requetes"] == ["usine", "entrepôt", "laboratoire"]


def test_une_ville_connue_reprend_l_orthographe_de_la_base():
    assert cb.verifier(_demande(["usine"], "MOHAMMEDIA"), VILLES)["ville"] == "Mohammédia"
    # Une ville où l'on n'a encore personne reste possible.
    assert cb.verifier(_demande(["usine"], "Tanger"), VILLES)["ville"] == "Tanger"


def test_la_conversation_garde_la_requete_d_un_message_a_l_autre():
    modele = FauxModele(_demande(["usine"], None), _demande(["usine"], "Casablanca"))
    graphe = construire_graphe(cb.creer_noeud_chatbot(modele=modele, villes=VILLES))
    premier = repondre(graphe, "t1", "usine")
    assert not premier["pret"] and "Dans quelle ville" in premier["reponse"]
    second = repondre(graphe, "t1", "à Casablanca")
    assert second["pret"] and second["ville"] == "Casablanca" and second["max_resultats"] == 20
    # Au second tour, le modèle a relu toute la conversation (consignes + 3 messages).
    assert len(modele.appels[1]) == 4


def _client(monkeypatch):
    monkeypatch.setattr(
        auth, "_find_user_by_id",
        lambda uid: {"id": 7, "username": "commercial", "display_name": "c", "is_admin": False},
    )
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=7, username="commercial", display_name="c", is_admin=False)
    return client


def test_la_route_repond_avec_la_demande(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "cle-de-test")
    graphe = construire_graphe(cb.creer_noeud_chatbot(modele=FauxModele(_demande(["usine"], "casablanca")), villes=VILLES))
    monkeypatch.setattr(route, "_obtenir_graphe", lambda: graphe)
    reponse = _client(monkeypatch).post("/api/chatbot", json={"message": "usine casablanca"})
    assert reponse.status_code == 200
    assert reponse.get_json()["pret"] is True
    assert reponse.get_json()["requetes"] == ["usine"]


def test_la_route_refuse_un_message_vide_ou_trop_long(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "cle-de-test")
    client = _client(monkeypatch)
    assert client.post("/api/chatbot", json={"message": "  "}).status_code == 400
    assert client.post("/api/chatbot", json={"message": "x" * 2001}).status_code == 400


def test_sans_cle_openai_le_chatbot_se_dit_indisponible(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reponse = _client(monkeypatch).post("/api/chatbot", json={"message": "usine casablanca"})
    assert reponse.status_code == 503


def test_une_cle_openai_expiree_donne_un_message_clair(monkeypatch):
    class AuthenticationError(Exception):
        pass

    def noeud(etat):
        raise AuthenticationError("Your API key has expired.")

    monkeypatch.setenv("OPENAI_API_KEY", "cle-expiree")
    monkeypatch.setattr(route, "_obtenir_graphe", lambda: construire_graphe(noeud))
    reponse = _client(monkeypatch).post("/api/chatbot", json={"message": "usine casablanca"})
    assert reponse.status_code == 503
    assert "refusée" in reponse.get_json()["error"]
