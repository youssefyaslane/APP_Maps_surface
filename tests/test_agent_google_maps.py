"""Outil 1 gratuit : lecture directe de Google Maps (outils/google_maps.py).

Le navigateur est simulé : ces tests portent sur ce qui casserait en silence,
la lecture du lien d'une fiche (coordonnées, identifiant Google commun avec
Apify) et la mise au format d'Apify, dont dépendent les doublons et l'écriture.
"""
import pytest

from agent_chatbot_workflow.outils import google_maps as g

LIEN = ("https://www.google.com/maps/place/Sofadex+Puratos/data=!4m7!3m6"
        "!1s0xda62d3c152fb11b:0x38eed7ca9e30c3f!8m2!3d33.5506057!4d-7.6350665"
        "!16s%2Fg%2F1tg6w9_l!19sChIJG7EvFTwtpg0RPwzjqXztjgM?authuser=0&hl=fr")


def test_lire_le_lien_d_une_fiche():
    r = g.lire_lien(LIEN)
    assert r["lat"] == 33.5506057 and r["lon"] == -7.6350665
    assert r["place_id"] == "ChIJG7EvFTwtpg0RPwzjqXztjgM"   # même identifiant qu'Apify
    assert r["cid"] == str(0x38eed7ca9e30c3f)


def test_normaliser_au_format_apify():
    fiches = [
        {"nom": "Sofadex Puratos", "lien": LIEN, "categorie": "Producteur agroalimentaire",
         "adresse": "Casablanca", "telephone": "05 22 25 11 35", "note": "4,6",
         "site": "http://www.sofadex-puratos.ma/fr"},
        {"nom": "Sofadex Puratos", "lien": LIEN},                          # doublon
        {"nom": "Sans GPS", "lien": "https://www.google.com/maps/place/x/data=!19sChIJabc"},
        {"nom": "", "lien": LIEN.replace("ChIJG7", "ChIJH8")},              # sans nom
        {"nom": "Sans place_id", "lien": "https://www.google.com/maps/place/y/data=!1s0x1:0xff!3d33.5!4d-7.6"},
    ]
    lieux = g.normaliser(fiches)
    assert [l["nom"] for l in lieux] == ["Sofadex Puratos", "Sans place_id"]
    sofadex, sans_id = lieux
    assert sofadex["note"] == 4.6 and sofadex["ville"] is None and sofadex["telephone"] == "05 22 25 11 35"
    assert sofadex["url"].endswith("!19sChIJG7EvFTwtpg0RPwzjqXztjgM")
    assert sans_id["place_id"] == "cid:255"   # repli sur le CID


def test_rechercher_est_gratuite_et_reprend_le_maximum():
    appels = []

    def extraire(requetes, ville, maximum):
        appels.append((requetes, ville, maximum))
        return [{"nom": "Sofadex Puratos", "lien": LIEN}]

    lieux, cout = g.rechercher(["usine"], "Casablanca", extraire=extraire)
    assert cout == 0.0 and len(lieux) == 1
    assert appels == [(["usine"], "Casablanca", 20)]


def test_des_fiches_toutes_illisibles_annoncent_un_blocage():
    with pytest.raises(g.GoogleMapsIndisponible, match="aucune fiche lisible"):
        g.rechercher(["usine"], "Casablanca", extraire=lambda *a: [{"nom": "", "lien": ""}])


def test_aucun_resultat_n_est_pas_une_erreur():
    assert g.rechercher(["usine"], "Ifrane", extraire=lambda *a: []) == ([], 0.0)
