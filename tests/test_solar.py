"""Estimation du potentiel photovoltaïque (domain/solar.py).

Ces valeurs finissent dans un devis : c'est le seul endroit du projet où une
erreur se traduit directement en dirhams annoncés à un client.
"""
import importlib

import pytest

from domain import solar


def test_surface_absente_ou_nulle_ne_donne_aucun_panneau():
    # Un toit introuvable et un toit vide doivent se ressembler côté appelant :
    # le tableau de bord affiche « 0 » et non une case vide ou une exception.
    assert solar.estimate_solar(None) == (0, 0.0)
    assert solar.estimate_solar(0) == (0, 0.0)
    assert solar.estimate_solar(-50) == (0, 0.0)


def test_puissance_d_un_toit_de_mille_metres():
    # 1000 m² x 0,7 = 700 m² exploitables, / 1,7 m² = 411 panneaux (tronqués),
    # x 400 W = 164,4 kWc.
    n_panels, kwc = solar.estimate_solar(1000)
    assert n_panels == 411
    assert kwc == pytest.approx(164.4)


def test_le_nombre_de_panneaux_est_tronque_jamais_arrondi_au_superieur():
    # Un demi-panneau ne s'installe pas : mieux vaut annoncer moins que promettre
    # une puissance que la toiture ne portera pas.
    small, _ = solar.estimate_solar(2.5)  # 2,5 x 0,7 / 1,7 = 1,029
    assert small == 1


def test_la_puissance_croit_avec_la_surface():
    surfaces = [500, 1000, 5000, 20000]
    puissances = [solar.estimate_solar(s)[1] for s in surfaces]
    assert puissances == sorted(puissances)
    # Doubler la surface double la puissance, à l'arrondi près du dernier panneau.
    assert solar.estimate_solar(2000)[1] == pytest.approx(2 * solar.estimate_solar(1000)[1], rel=0.01)


def test_config_expose_les_trois_hypotheses_a_la_carte():
    # La carte calcule avec ces valeurs : un nom de clé qui change sans que
    # static/app.js suive ferait réapparaître silencieusement deux estimations
    # différentes pour le même toit.
    cfg = solar.config()
    assert set(cfg) == {"panel_area_m2", "panel_power_w", "usable_roof_fraction"}
    assert all(v > 0 for v in cfg.values())


def test_les_hypotheses_sont_surchargeables_par_l_environnement(monkeypatch):
    # Le coefficient de pose dépend du chantier (pose à plat ou sur châssis
    # incliné) : il doit se régler au déploiement, sans toucher au code.
    monkeypatch.setenv("SOLAR_USABLE_ROOF_FRACTION", "1.0")
    monkeypatch.setenv("SOLAR_PANEL_POWER_W", "500")
    rechargé = importlib.reload(solar)
    try:
        n_panels, kwc = rechargé.estimate_solar(1700)
        assert n_panels == 1000  # 1700 m² x 1,0 / 1,7
        assert kwc == pytest.approx(500.0)
    finally:
        monkeypatch.undo()
        importlib.reload(solar)


def test_une_variable_d_environnement_illisible_ne_casse_pas_le_demarrage(monkeypatch):
    # Une faute de frappe dans la configuration doit dégrader vers le défaut,
    # pas empêcher l'application de démarrer.
    monkeypatch.setenv("SOLAR_PANEL_POWER_W", "quatre cents")
    rechargé = importlib.reload(solar)
    try:
        assert rechargé.SOLAR_PANEL_POWER_W == 400.0
    finally:
        monkeypatch.undo()
        importlib.reload(solar)
