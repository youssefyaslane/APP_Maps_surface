"""Lecture des réponses Gemini et repérage des tuiles Esri vides.

Ce qui casserait en silence : une réponse mal formée prise pour un « oui » (le
toit sortirait à tort de la liste), ou une tuile grise « Map data not yet
available » envoyée au modèle comme si c'était un vrai toit.
"""
import json

from PIL import Image, ImageDraw

from scripts.detect_panels_gemini import is_placeholder, parse_verdict, verdict_from_text


def _reponse(texte):
    return {"candidates": [{"content": {"parts": [{"text": texte}]}}]}


def test_un_verdict_bien_forme_est_repris_tel_quel():
    v = parse_verdict(_reponse(json.dumps({"panneaux": "oui", "confiance": 92, "raison": "rangées sombres"})))
    assert v == {"panneaux": "oui", "confiance": 92, "raison": "rangées sombres"}


def test_une_reponse_illisible_devient_incertain_jamais_oui():
    for payload in ({}, {"candidates": []}, _reponse("pas du json"), _reponse('{"panneaux": "peut-être"}')):
        assert parse_verdict(payload)["panneaux"] == "incertain"


def test_la_confiance_est_bornee_entre_0_et_100():
    assert parse_verdict(_reponse('{"panneaux": "non", "confiance": 250}'))["confiance"] == 100
    assert parse_verdict(_reponse('{"panneaux": "non", "confiance": "beaucoup"}'))["confiance"] == 0


def test_la_tuile_grise_d_esri_est_reconnue():
    tuile = Image.new("RGB", (256, 256), (204, 204, 204))
    ImageDraw.Draw(tuile).text((20, 120), "Map data not yet available", fill=(190, 190, 190))
    assert is_placeholder(tuile)


def test_un_vrai_toit_n_est_pas_pris_pour_une_tuile_vide():
    toit = Image.new("RGB", (256, 256), (200, 195, 185))
    draw = ImageDraw.Draw(toit)
    for x in range(0, 256, 16):  # lanterneaux et ombres : de la texture
        draw.rectangle((x, 40, x + 6, 200), fill=(60, 60, 70))
    assert not is_placeholder(toit)


def test_la_meme_regle_vaut_pour_une_reponse_openai():
    # OpenAI renvoie le JSON directement : même lecture, même prudence.
    assert verdict_from_text('{"panneaux": "oui", "confiance": 80, "raison": "r"}')["panneaux"] == "oui"
    for texte in (None, "", "[1, 2]", '"oui"', "oui"):
        assert verdict_from_text(texte)["panneaux"] == "incertain"
