"""Outil 1 (recherche Apify) et confirmation avant de la lancer.

Ce qui casserait en silence : une recherche Apify partie sans le clic
« Lancer » (elle est payée), plus de 20 lieux demandés par requête, un lieu
sans GPS ou sans place_id gardé (ni toit ni doublon possibles), ou un même
lieu compté deux fois quand deux requêtes le trouvent. Apify, OpenAI et la
base sont simulés.
"""
import pytest

pytest.importorskip("langgraph")

import app as app_module  # noqa: E402
from agent_chatbot_workflow import chatbot as cb  # noqa: E402
from agent_chatbot_workflow.graphe import attend_confirmation, construire_graphe, decider, repondre  # noqa: E402
from agent_chatbot_workflow.outils import apify  # noqa: E402
from web import auth  # noqa: E402
from web import chatbot as route  # noqa: E402

VILLES = {"casablanca": "Casablanca"}


def _item(nom, place_id, lat=33.59, lng=-7.6, **autres):
    return {"title": nom, "placeId": place_id, "location": {"lat": lat, "lng": lng}, **autres}


LIEUX = [
    {"nom": "Univers Acier", "categorie": "Aciériste", "place_id": "p1"},
    {"nom": "Câblerie du Maroc", "categorie": "Fabricant", "place_id": "p2"},
]


class FauxModele:
    def __init__(self, *extractions):
        self.extractions = list(extractions)

    def invoke(self, messages):
        return self.extractions.pop(0)


class FausseRecherche:
    def __init__(self, resultats=LIEUX, erreur=None):
        self.resultats, self.erreur, self.appels = resultats, erreur, []

    def __call__(self, requetes, ville):
        self.appels.append((requetes, ville))
        if self.erreur:
            raise self.erreur
        return self.resultats, 0.08


class FauxClassement:
    """Classe tout « entreprise », sûr : ces tests portent sur l'outil 1."""

    def invoke(self, messages):
        import json
        from agent_chatbot_workflow.classification import Decision, Lot

        lieux = json.loads(messages[-1].content.split("\n", 1)[1])
        return Lot(decisions=[Decision(place_id=l["place_id"], entreprise=True, confiance=0.9, raison="test")
                              for l in lieux])


def _ecrire_rien(lieux, requetes, ville, user_id):
    return {}


def _graphe(recherche, *extractions):
    extractions = extractions or (cb.Demande(requetes=["usine"], ville="casablanca"),)
    return construire_graphe(
        cb.creer_noeud_chatbot(modele=FauxModele(*extractions), villes=VILLES),
        recherche=recherche, modele_classement=FauxClassement(), ecrire=_ecrire_rien,
    )


# ---------- outil 1 ----------

def test_l_entree_apify_plafonne_a_20_lieux_par_requete_sans_options_payantes():
    entree = apify.construire_entree(["usine", "entrepôt"], "Casablanca")
    assert entree["searchStringsArray"] == ["usine", "entrepôt"]
    assert entree["locationQuery"] == "Casablanca, Maroc"
    assert entree["maxCrawledPlacesPerSearch"] == 20
    assert entree["maxImages"] == 0 and entree["maxReviews"] == 0
    assert entree["maximumLeadsEnrichmentRecords"] == 0


def test_les_lieux_sans_gps_ou_place_id_sont_ecartes_et_les_doublons_fusionnes():
    lieux = apify.normaliser([
        _item("Univers Acier", "p1", categoryName="Aciériste", phone="+212 522 88 75 00"),
        _item("Univers Acier", "p1"),               # trouvé par une 2e requête
        _item("Sans GPS", "p2", lat=None),
        _item("Sans identifiant", None),
        {"placeId": "p3", "location": {"lat": 1, "lng": 2}},   # sans nom
    ])
    assert [l["place_id"] for l in lieux] == ["p1"]
    assert lieux[0]["categorie"] == "Aciériste" and lieux[0]["telephone"] == "+212 522 88 75 00"


def test_rechercher_lit_le_jeu_de_donnees_de_l_execution():
    class Client:
        def __init__(self):
            self.entree = None

        def actor(self, nom):
            assert nom == apify.ACTEUR
            client = self

            class Acteur:
                def call(self, run_input):
                    client.entree = run_input
                    return {"id": "r1", "defaultDatasetId": "ds1", "usageTotalUsd": 0.0002}
            return Acteur()

        def run(self, identifiant):
            class Execution:
                def get(self):
                    return {"usageTotalUsd": 0.0802}   # coût final, relu après l'exécution
            return Execution()

        def dataset(self, identifiant):
            assert identifiant == "ds1"

            class Jeu:
                def iterate_items(self):
                    return iter([_item("Univers Acier", "p1")])
            return Jeu()

    client = Client()
    lieux, cout = apify.rechercher(["usine"], "Casablanca", client=client)
    assert [l["nom"] for l in lieux] == ["Univers Acier"]
    assert cout == 0.0802 and client.entree["maxCrawledPlacesPerSearch"] == 20


def test_sans_jeton_la_recherche_ne_part_pas(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    with pytest.raises(apify.ApifyIndisponible):
        apify.rechercher(["usine"], "Casablanca")


# ---------- graphe : confirmation, outil 1, bilan ----------

def test_une_demande_prete_attend_le_clic_sans_lancer_apify():
    recherche = FausseRecherche()
    graphe = _graphe(recherche)
    r = repondre(graphe, "t", "usine casablanca")
    assert r["confirmation"] == {"requetes": ["usine"], "ville": "Casablanca", "max_par_requete": 20}
    assert attend_confirmation(graphe, "t")
    assert recherche.appels == []


def test_annuler_ne_lance_rien():
    recherche = FausseRecherche()
    graphe = _graphe(recherche)
    repondre(graphe, "t", "usine casablanca")
    r = decider(graphe, "t", False)
    assert "annulée" in r["reponse"]
    assert recherche.appels == [] and not attend_confirmation(graphe, "t")


def test_lancer_cherche_puis_fait_le_bilan():
    recherche = FausseRecherche()
    graphe = _graphe(recherche)
    repondre(graphe, "t", "usine casablanca")
    r = decider(graphe, "t", True)
    assert recherche.appels == [(["usine"], "Casablanca")]
    assert r["reponse"].startswith("2 lieux trouvés pour « usine » à Casablanca")
    assert [l["nom"] for l in r["resultats"]] == ["Univers Acier", "Câblerie du Maroc"]
    assert r["cout_usd"] == 0.08 and not r["pret"]


def test_une_erreur_apify_est_rendue_dans_le_bilan():
    graphe = _graphe(FausseRecherche(erreur=apify.ApifyIndisponible("Jeton Apify absent")))
    repondre(graphe, "t", "usine casablanca")
    r = decider(graphe, "t", True)
    assert "a échoué" in r["reponse"] and r["resultats"] == []


def test_un_nouveau_message_pendant_la_confirmation_repart_du_chatbot():
    recherche = FausseRecherche()
    graphe = _graphe(
        recherche,
        cb.Demande(requetes=["usine"], ville="casablanca"),
        cb.Demande(requetes=["entrepôt"], ville="casablanca"),
    )
    repondre(graphe, "t", "usine casablanca")
    r = repondre(graphe, "t", "plutôt des entrepôts")
    assert r["confirmation"]["requetes"] == ["entrepôt"]
    decider(graphe, "t", True)
    assert recherche.appels == [(["entrepôt"], "Casablanca")]


# ---------- routes ----------

def _client(monkeypatch):
    monkeypatch.setattr(
        auth, "_find_user_by_id",
        lambda uid: {"id": 7, "username": "commercial", "display_name": "c", "is_admin": False},
    )
    monkeypatch.setenv("OPENAI_API_KEY", "cle-de-test")
    monkeypatch.setenv("APIFY_API_TOKEN", "jeton-de-test")
    monkeypatch.setattr(route, "_taches", {})
    # La recherche « en arrière-plan » tourne tout de suite, dans le test.
    monkeypatch.setattr(route, "_demarrer", lambda cible, *args: cible(*args))
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=7, username="commercial", display_name="c", is_admin=False)
    return client


def test_le_clic_lancer_demarre_la_recherche_et_l_etat_rend_les_lieux(monkeypatch):
    recherche = FausseRecherche()
    graphe = _graphe(recherche)
    monkeypatch.setattr(route, "_obtenir_graphe", lambda: graphe)
    client = _client(monkeypatch)
    assert client.post("/api/chatbot", json={"message": "usine casablanca"}).get_json()["confirmation"]
    assert client.post("/api/chatbot/lancer", json={"lancer": True}).get_json() == {"statut": "en_cours"}
    etat = client.get("/api/chatbot/etat").get_json()
    assert etat["statut"] == "fini" and len(etat["resultats"]) == 2
    assert recherche.appels == [(["usine"], "Casablanca")]


def test_annuler_par_la_route_et_refus_sans_recherche_a_confirmer(monkeypatch):
    recherche = FausseRecherche()
    graphe = _graphe(recherche)
    monkeypatch.setattr(route, "_obtenir_graphe", lambda: graphe)
    client = _client(monkeypatch)
    assert client.post("/api/chatbot/lancer", json={"lancer": True}).status_code == 409
    client.post("/api/chatbot", json={"message": "usine casablanca"})
    assert client.post("/api/chatbot/lancer", json={"lancer": "oui"}).status_code == 400
    r = client.post("/api/chatbot/lancer", json={"lancer": False}).get_json()
    assert r["statut"] == "annule" and recherche.appels == []


def test_sans_jeton_apify_le_clic_lancer_est_refuse(monkeypatch):
    graphe = _graphe(FausseRecherche())
    monkeypatch.setattr(route, "_obtenir_graphe", lambda: graphe)
    client = _client(monkeypatch)
    monkeypatch.delenv("APIFY_API_TOKEN")
    client.post("/api/chatbot", json={"message": "usine casablanca"})
    assert client.post("/api/chatbot/lancer", json={"lancer": True}).status_code == 503
