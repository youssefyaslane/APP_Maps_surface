"""Détection automatique des panneaux déjà posés.

Ce qui casserait en silence : un toit jamais analysé affiché « Non » (on le
croirait vérifié), ou un toit sans image satellite compté comme sans panneaux.
"""
import json

import app
from scripts.panneaux import importer_panneaux


def test_un_toit_pas_encore_analyse_n_a_pas_de_verdict():
    assert app._pv_detection(None, None) is None


def test_sans_image_ce_n_est_ni_oui_ni_non():
    assert app._pv_detection(False, [])["verdict"] == "pas d'image"


def test_le_seuil_decide_du_oui(monkeypatch):
    monkeypatch.setattr(app, "PV_SEUIL", 0.5)
    v = app._pv_detection(True, [0.83, 0.61, 0.2])
    assert v == {"verdict": "oui", "confiance": 0.83, "nb": 2}
    assert app._pv_detection(True, [0.3])["verdict"] == "non"
    assert app._pv_detection(True, [])["verdict"] == "non"


def test_l_import_ne_garde_que_les_toits_de_la_base(tmp_path):
    journal = tmp_path / "resultats.jsonl"
    journal.write_text("\n".join([
        json.dumps({"cle": "osm:12", "panneaux": "oui", "scores": [0.4, 0.9]}),
        json.dumps({"cle": "ms:7", "panneaux": "pas d'image", "scores": []}),
        json.dumps({"cle": "batifer_avec_panneaux.jpg", "panneaux": "oui", "scores": [0.8]}),
        '{"cle": "ia:3", "panneaux": "no',  # ligne coupée par un arrêt brutal
    ]), encoding="utf-8")
    toits = importer_panneaux.lire(journal)
    assert toits == {"osm:12": (True, [0.9, 0.4]), "ms:7": (False, [])}
