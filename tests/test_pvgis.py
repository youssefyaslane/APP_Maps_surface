"""Productible PVGIS (services/pvgis.py) et production annuelle.

Ce qui casserait en silence : une production annoncée avec d'autres réglages
que ceux choisis (inclinaison à plat), une zone redemandée à chaque calcul, un
point en mer qui ferait échouer tout le calcul, ou un PVGIS indisponible pris
pour un productible nul. PVGIS et la base sont simulés.
"""
import pytest

from services import pvgis, solar


class Reponse:
    def __init__(self, statut, corps=None):
        self.status_code, self._corps = statut, corps

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._corps


def corps_pvgis(productible=1455.2):
    return {"outputs": {
        "totals": {"fixed": {"E_y": productible, "H(i)_y": 1971.4}},
        "monthly": {"fixed": [{"E_m": productible / 12} for _ in range(12)]},
    }}


class FausseSession:
    def __init__(self, *reponses):
        self.reponses, self.appels = list(reponses), []

    def get(self, url, params, timeout):
        self.appels.append(params)
        return self.reponses.pop(0)


@pytest.fixture(autouse=True)
def sans_attente(monkeypatch):
    monkeypatch.setattr(pvgis.time, "sleep", lambda s: None)


def test_les_reglages_par_defaut_sont_le_cas_le_plus_prudent():
    assert pvgis.reglages() == {"angle": 0.0, "aspect": 0.0, "loss": 14.0, "mountingplace": "building"}


def test_demander_envoie_1_kwc_et_les_reglages():
    session = FausseSession(Reponse(200, corps_pvgis()))
    r = pvgis.demander(33.6, -7.6, session)
    assert r["productible"] == 1455.2 and len(r["mensuel"]) == 12 and r["ensoleillement"] == 1971.4
    params = session.appels[0]
    assert params["peakpower"] == 1 and params["angle"] == 0.0 and params["mountingplace"] == "building"


def test_un_point_en_mer_n_a_pas_de_productible():
    assert pvgis.demander(34.5, -9.0, FausseSession(Reponse(400))) is None


def test_pvgis_indisponible_est_retente_puis_signale():
    session = FausseSession(Reponse(503), Reponse(503), Reponse(503))
    with pytest.raises(pvgis.PvgisIndisponible):
        pvgis.demander(33.6, -7.6, session)
    assert len(session.appels) == pvgis.ESSAIS


def test_une_panne_passagere_est_rattrapee():
    session = FausseSession(Reponse(503), Reponse(200, corps_pvgis(1500)))
    assert pvgis.demander(33.6, -7.6, session)["productible"] == 1500


def test_les_entreprises_proches_partagent_une_case():
    assert pvgis.case(33.5833, -7.5834) == pvgis.case(33.5512, -7.6249) == (33.6, -7.6)


class FauxCurseur:
    def __init__(self, base):
        self.base, self._resultat = base, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT id, lat, lon FROM companies"):
            sans = "solar_yield_kwh_kwc IS NULL" in sql
            ids = params[0] if "ANY" in sql else None
            self._resultat = [(i, la, lo) for i, la, lo, y in self.base["entreprises"]
                              if not (sans and y) and (ids is None or i in ids)]
        elif sql.startswith("SELECT productible FROM pvgis_cache"):
            self._resultat = [(self.base["cache"][params],)] if params in self.base["cache"] else []
        elif sql.startswith("INSERT INTO pvgis_cache"):
            self.base["cache"][tuple(params[:6])] = params[6]
        elif sql.startswith("UPDATE companies SET solar_yield_kwh_kwc"):
            for i in params[1]:
                self.base["maj"][i] = params[0]
        else:
            raise AssertionError(sql)

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


def test_remplir_un_appel_par_zone_et_cache_reutilise():
    base = {"entreprises": [
        (1, 33.58, -7.58, None), (2, 33.61, -7.62, None),   # même case (33.6, -7.6)
        (3, 31.63, -8.01, None),                             # Marrakech
        (4, 33.59, -7.60, 1400.0),                           # déjà calculée : non retouchée
    ], "cache": {}, "maj": {}}
    # Les zones sont traitées dans l'ordre : Marrakech (31,6) puis Casablanca (33,6).
    session = FausseSession(Reponse(200, corps_pvgis(1541)), Reponse(200, corps_pvgis(1455)))
    assert pvgis.remplir(FausseConnexion(base), session=session) == 3
    assert len(session.appels) == 2
    assert base["maj"] == {3: 1541, 1: 1455, 2: 1455}

    # Relancé avec --production : tout est recalculé, mais depuis le cache.
    assert pvgis.remplir(FausseConnexion(base), tout=True, session=FausseSession()) == 4
    assert base["maj"][4] == 1455


def test_remplir_seulement_les_entreprises_demandees():
    base = {"entreprises": [(1, 33.58, -7.58, None), (2, 31.63, -8.01, None)], "cache": {}, "maj": {}}
    session = FausseSession(Reponse(200, corps_pvgis(1455)))
    assert pvgis.remplir(FausseConnexion(base), session=session, ids=[1], bavard=False) == 1
    assert base["maj"] == {1: 1455} and len(session.appels) == 1


def test_production_annuelle():
    # J.J.W : 775 kWc × 1 455 kWh/kWc = 1 127,6 MWh par an.
    assert solar.production_mwh(775, 1455) == pytest.approx(1127.6)
    assert solar.production_mwh(775, None) is None
    assert solar.production_mwh(0, 1455) is None


def test_co2_evite():
    # J.J.W : 1 121,4 MWh × 0,596 t/MWh = 668,4 t de CO₂ évitées par an.
    assert solar.co2_evite_t(1121.4) == pytest.approx(668.4)
    assert solar.co2_evite_t(None) is None


def test_economies_maximales():
    # J.J.W : 1 121,4 MWh = 1 121 400 kWh × 100 % × 1,01 DH = 1 132 614 DH par an.
    assert solar.economies_dh(1121.4) == 1132614
    assert solar.economies_dh(None) is None
