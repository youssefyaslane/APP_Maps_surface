"""Classification (entreprise ou non) et outil 2 (écriture des seules
entreprises nouvelles).

Ce qui casserait en silence : un stade ou une mosquée écrits en base, une
réponse incertaine du modèle prise pour un oui, une entreprise déjà connue
réécrite (ou recréée sous un autre place_id, comme IMCE), une écriture sans
trace dans le journal, ou une clé OpenAI expirée qui ferait passer toutes les
recherches « à vérifier » sans le dire. OpenAI, Apify et la base sont simulés.
"""
import json

import pytest

pytest.importorskip("langgraph")

from agent_chatbot_worflow import chatbot as cb  # noqa: E402
from agent_chatbot_worflow import classification as cl  # noqa: E402
from agent_chatbot_worflow.graphe import construire_graphe, decider, repondre  # noqa: E402
from agent_chatbot_worflow.outils import ecriture  # noqa: E402


def _lieu(place_id, nom="Lieu", **autres):
    return {"place_id": place_id, "nom": nom, "categorie": "Fabricant", "adresse": "Casablanca",
            "ville": "Casablanca", "telephone": "+212 5 22 00 00 00", "site": None, "note": 4.2,
            "lat": 33.59, "lon": -7.6, **autres}


class FauxClassement:
    """Répond selon une table {place_id: (entreprise, confiance)} ; un
    identifiant absent de la table est « oublié » par le modèle."""

    def __init__(self, table, erreur=None):
        self.table, self.erreur, self.appels = table, erreur, 0

    def invoke(self, messages):
        self.appels += 1
        if self.erreur:
            raise self.erreur
        lieux = json.loads(messages[-1].content.split("\n", 1)[1])
        return cl.Lot(decisions=[
            cl.Decision(place_id=l["place_id"], entreprise=self.table[l["place_id"]][0],
                        confiance=self.table[l["place_id"]][1], raison="test")
            for l in lieux if l["place_id"] in self.table
        ] + [cl.Decision(place_id="inconnu", entreprise=True, confiance=1, raison="pas envoyé")])


# ---------- classification ----------

def test_seule_une_reponse_sure_tranche():
    assert cl.statut_de(cl.Decision(place_id="p", entreprise=True, confiance=0.8, raison="")) == cl.ENTREPRISE
    assert cl.statut_de(cl.Decision(place_id="p", entreprise=False, confiance=0.95, raison="")) == cl.ECARTEE
    assert cl.statut_de(cl.Decision(place_id="p", entreprise=True, confiance=0.79, raison="")) == cl.A_VERIFIER
    assert cl.statut_de(None) == cl.A_VERIFIER


def test_classer_par_lots_et_lieu_oublie_a_verifier():
    lieux = [_lieu(f"p{i}") for i in range(25)]
    table = {f"p{i}": (True, 0.9) for i in range(24)}           # p24 oublié par le modèle
    modele = FauxClassement(table)
    classes = cl.classer(lieux, modele)
    assert modele.appels == 2                                    # 20 + 5
    assert [c["classe"] for c in classes].count(cl.ENTREPRISE) == 24
    assert classes[-1]["classe"] == cl.A_VERIFIER
    assert all(c["place_id"] != "inconnu" for c in classes)     # réponse hors liste ignorée


def test_une_reponse_illisible_met_le_lot_a_verifier():
    classes = cl.classer([_lieu("p1")], FauxClassement({}, erreur=ValueError("JSON invalide")))
    assert classes[0]["classe"] == cl.A_VERIFIER


def test_une_cle_openai_refusee_n_est_pas_cachee():
    class AuthenticationError(Exception):
        pass

    with pytest.raises(AuthenticationError):
        cl.classer([_lieu("p1")], FauxClassement({}, erreur=AuthenticationError("expired")))


# ---------- outil 2 ----------

class FauxCurseur:
    def __init__(self, base):
        self.base, self._resultat = base, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT DISTINCT trim(city)"):
            self._resultat = [("Casablanca",)]
        elif sql.startswith("SELECT place_id FROM companies"):
            self._resultat = [(p,) for p in params[0] if p in self.base["place_ids"]]
        elif sql.startswith("SELECT id, name, lat, lon, phone, website FROM companies"):
            self._resultat = list(self.base["existantes"])
        elif sql.startswith("INSERT INTO companies"):
            if params["place_id"] in self.base["conflits"]:
                self._resultat = []
            else:
                self.base["inserees"].append(params)
                self._resultat = [(100 + len(self.base["inserees"]),)]
        elif sql.startswith("INSERT INTO audit_log"):
            self.base["journal"].append(params)
            self._resultat = []
        else:
            raise AssertionError(f"requête inattendue : {sql}")

    def fetchall(self):
        return self._resultat

    def fetchone(self):
        return self._resultat[0] if self._resultat else None


class FausseConnexion:
    def __init__(self, base):
        self.base = base

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FauxCurseur(self.base)

    def close(self):
        self.base["fermee"] = True


def _base(**champs):
    base = {"place_ids": set(), "existantes": [], "conflits": set(), "inserees": [], "journal": []}
    base.update(champs)
    return base


def _statuts(resultat):
    return {p: statut for p, (statut, _raison) in resultat.items()}


def test_l_outil_2_n_ecrit_que_les_entreprises_nouvelles():
    base = _base(place_ids={"connu"}, existantes=[(319, "IMCE", 33.59, -7.6, None, None)])
    lieux = [
        _lieu("connu", "Univers Acier", classe="entreprise"),
        _lieu("p-imce", "IMCE", classe="entreprise", telephone=None),
        _lieu("neuf", "Nouvelle Usine", classe="entreprise", confiance=0.93, raison="usine"),
        _lieu("stade", "Stade Mohammed V", classe="ecartee"),
        _lieu("doute", "Siège social", classe="a_verifier"),
    ]
    statuts = ecriture.ecrire_nouvelles(lieux, ["usine"], "Casablanca", user_id=7, connecter=lambda: FausseConnexion(base))
    assert _statuts(statuts) == {"connu": "deja_en_base", "p-imce": "doublon", "neuf": "ajoutee"}
    assert "n° 319" in statuts["p-imce"][1]
    assert [l["place_id"] for l in base["inserees"]] == ["neuf"]
    assert base["inserees"][0]["city"] == "Casablanca" and base["inserees"][0]["rating"] == 4.2
    user_id, action, entite, entite_id, details = base["journal"][0]
    assert (user_id, action, entite) == (7, "company_created_by_chatbot", "companies")
    assert json.loads(details)["requetes"] == ["usine"]
    assert base["fermee"]


def test_une_entreprise_ecrite_entre_temps_n_est_pas_doublee():
    base = _base(conflits={"neuf"})
    statuts = ecriture.ecrire_nouvelles([_lieu("neuf", classe="entreprise")], ["usine"], "Casablanca",
                                        connecter=lambda: FausseConnexion(base))
    assert _statuts(statuts) == {"neuf": "deja_en_base"} and base["journal"] == []


def test_meme_telephone_ou_meme_site_est_un_doublon_meme_loin():
    # J.J.W (Bd Mohammed VI) et JJWASHING (Tit Mellil) : 7 km, même numéro.
    base = _base(existantes=[
        (6024, "JJWASHING usine", 33.5324, -7.4912, "+212 5 22 21 88 09", "http://www.jjwashing.ma/"),
        (12, "Atlas Plast", 34.0, -6.8, None, "https://atlasplast.ma/contact"),
        (13, "Café Facebook", 33.59, -7.6, None, "https://facebook.com/cafe"),
    ])
    lieux = [
        _lieu("jjw", "J.J.W", classe="entreprise", telephone="0522218809", lat=33.5263, lon=-7.5641),
        _lieu("atlas", "Atlas Plastique", classe="entreprise", telephone=None, site="atlasplast.ma"),
        _lieu("fb", "Autre Société", classe="entreprise", telephone=None, site="https://www.facebook.com/autre"),
    ]
    statuts = ecriture.ecrire_nouvelles(lieux, ["usine"], "Casablanca", connecter=lambda: FausseConnexion(base))
    assert _statuts(statuts) == {"jjw": "doublon", "atlas": "doublon", "fb": "ajoutee"}
    assert "même téléphone que n° 6024" in statuts["jjw"][1]
    assert "même site web" in statuts["atlas"][1]


def test_nom_proche_au_meme_endroit_est_un_doublon():
    base = _base(existantes=[(40, "BARDAHL MAGHREB SARL", 33.5333, -7.5834, None, None)])
    lieux = [_lieu("b", "Bardahl Maghreb (Usine)", classe="entreprise", telephone=None, lat=33.5334, lon=-7.5834)]
    statuts = ecriture.ecrire_nouvelles(lieux, ["usine"], "Casablanca", connecter=lambda: FausseConnexion(base))
    assert _statuts(statuts) == {"b": "doublon"}


def test_deux_fiches_de_la_meme_societe_dans_une_recherche():
    base = _base()
    lieux = [_lieu("a", "Usine Nord", classe="entreprise"), _lieu("b", "Usine Nord SARL", classe="entreprise")]
    statuts = ecriture.ecrire_nouvelles(lieux, ["usine"], "Casablanca", connecter=lambda: FausseConnexion(base))
    assert _statuts(statuts) == {"a": "ajoutee", "b": "doublon"}
    assert len(base["inserees"]) == 1


def test_normalisations():
    assert ecriture.nom_normalise("Ifplast Automobile S.A.R.L") == ecriture.nom_normalise("IFPLAST AUTOMOBILE")
    assert ecriture.nom_normalise("Société Générale") == "generale"
    assert ecriture.telephones("+212 5 22 21 88 09 / 06 61 00 00 00") == {"522218809", "661000000"}
    assert ecriture.domaine("http://www.jjwashing.ma/") == "jjwashing.ma"
    assert ecriture.domaine("https://facebook.com/x") is None


def test_sans_entreprise_aucune_connexion():
    assert ecriture.ecrire_nouvelles([_lieu("s", classe="ecartee")], ["usine"], "Casablanca",
                                     connecter=lambda: pytest.fail("connexion inutile")) == {}


# ---------- graphe complet ----------

LIEUX_APIFY = [_lieu("usine1", "Usine Atlas"), _lieu("stade", "Stade Mohammed V"), _lieu("doute", "Siège social")]
TABLE = {"usine1": (True, 0.95), "stade": (False, 0.97), "doute": (True, 0.5)}


def _graphe(modele_classement, ecrire):
    return construire_graphe(
        cb.creer_noeud_chatbot(modele=type("M", (), {"invoke": lambda self, m: cb.Demande(requetes=["usine"], ville="Casablanca")})(),
                               villes={"casablanca": "Casablanca"}),
        recherche=lambda requetes, ville: (LIEUX_APIFY, 0.05),
        modele_classement=modele_classement,
        ecrire=ecrire,
    )


def test_lancer_classe_ecrit_et_fait_le_bilan():
    appels = []

    def ecrire(lieux, requetes, ville, user_id):
        appels.append(([l["place_id"] for l in lieux if l["classe"] == "entreprise"], user_id))
        return {"usine1": ("ajoutee", "usine")}

    graphe = _graphe(FauxClassement(TABLE), ecrire)
    repondre(graphe, "t", "usine casablanca")
    r = decider(graphe, "t", True, user_id=7)
    assert appels == [(["usine1"], 7)]
    statuts = {l["place_id"]: l["classe"] for l in r["resultats"]}
    assert statuts == {"usine1": "ajoutee", "stade": "ecartee", "doute": "a_verifier"}
    assert "1 nouvelle(s) entreprise(s) ajoutée(s) en base" in r["reponse"]
    assert "1 écartée(s)" in r["reponse"] and "1 à vérifier" in r["reponse"]


def test_si_la_classification_echoue_rien_n_est_ecrit():
    graphe = _graphe(FauxClassement({}, erreur=type("AuthenticationError", (Exception,), {})("expired")),
                     lambda *a: pytest.fail("rien ne doit être écrit"))
    repondre(graphe, "t", "usine casablanca")
    r = decider(graphe, "t", True)
    assert "Rien n'a été écrit en base" in r["reponse"]
