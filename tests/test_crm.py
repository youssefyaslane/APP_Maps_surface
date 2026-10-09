"""CRM des commerciaux (services/crm.py, web/crm.py, filtres du tableau de bord).

Ce qui casserait en silence : un prospect « perdu » sans raison, une relance
à une date illisible, des économies « réelles » qui dépasseraient la
production, un filtre « Mes prospects » qui montrerait ceux des autres, ou
une route qui laisserait passer une action refusée. La base est simulée :
les règles sont vérifiées avant tout accès.
"""
import pytest

import app as app_module
from services import crm, prospects
from web import auth
from web import crm as route


# ---------- règles vérifiées avant la base ----------

@pytest.fixture
def sans_base(monkeypatch):
    monkeypatch.setattr(crm, "_executer", lambda company_id, action: pytest.fail("la base ne doit pas être touchée"))


def suivi(statut="contacte", raison=None, le=None, objet=None):
    crm.mettre_a_jour_suivi(1, statut, raison, le, objet, 7)


def test_une_etape_inconnue_est_refusee(sans_base):
    with pytest.raises(crm.ErreurCRM, match="Étape inconnue"):
        suivi("gagne")


def test_perdu_demande_une_raison(sans_base):
    with pytest.raises(crm.ErreurCRM, match="pourquoi"):
        suivi("perdu")
    with pytest.raises(crm.ErreurCRM, match="pourquoi"):
        suivi("perdu", "fâché")


def test_une_date_de_relance_illisible_est_refusee(sans_base):
    with pytest.raises(crm.ErreurCRM, match="Date de relance invalide"):
        suivi(le="12/10/2026", objet="rappeler")


def test_pas_de_relance_dans_le_passe_ni_apres_la_cloture(sans_base):
    with pytest.raises(crm.ErreurCRM, match="aujourd'hui ou plus tard"):
        suivi(le="2020-01-01")
    with pytest.raises(crm.ErreurCRM, match="signé ou perdu"):
        suivi("signe", le="2099-01-01")


def test_un_email_de_decideur_sans_arobase_est_refuse(sans_base):
    with pytest.raises(crm.ErreurCRM, match="e-mail"):
        crm.enregistrer_decideur(1, {"nom": "M. Alami", "email": "alami.netis.ma"}, 7)


@pytest.mark.parametrize("donnees, message", [
    ({"conso_kwh_an": "beaucoup"}, "Consommation annuelle : nombre attendu"),
    ({"inclinaison": "95"}, "Inclinaison : entre 0 et 90"),
    ({"orientation": "-200"}, "Orientation : entre -180 et 180"),
    ({"etat_toiture": "rouillée"}, "État de la toiture inconnu"),
])
def test_les_releves_de_visite_sont_verifies(sans_base, donnees, message):
    with pytest.raises(crm.ErreurCRM, match=message):
        crm.enregistrer_visite(1, donnees, 7)


def test_une_note_vide_est_refusee(sans_base):
    with pytest.raises(crm.ErreurCRM, match="Note vide"):
        crm.ajouter_note(1, "   ", 7)


def test_les_nombres_acceptent_la_virgule_et_les_espaces():
    assert crm._nombre("1 200 000,5", 0, 1e9, "x") == 1200000.5
    assert crm._nombre("", 0, 1, "x") is None


# ---------- calculs de la fiche ----------

def test_sans_visite_les_economies_restent_maximales():
    c = crm.calculs(775.0, 1447.0, None, None, None)
    assert c["production_mwh"] == 1121.4 and c["productible_source"] == "hypothese"
    assert c["economies_max_dh"] == 1132614 and c["economies_reelles_dh"] is None


def test_une_conso_plus_faible_que_la_production_limite_les_economies():
    # 1 121 400 kWh produits, 600 000 kWh consommés : seuls ceux-là sont économisés.
    c = crm.calculs(775.0, 1447.0, None, 600000, "bon")
    assert c["economies_reelles_dh"] == round(600000 * 1.01)
    assert c["part_consommee"] == pytest.approx(0.535, abs=0.001)
    assert c["couverture_conso"] == 1.0


def test_une_grosse_conso_absorbe_toute_la_production():
    c = crm.calculs(775.0, 1447.0, None, 5_000_000, None)
    assert c["economies_reelles_dh"] == c["economies_max_dh"]
    assert c["part_consommee"] == 1.0 and c["couverture_conso"] == pytest.approx(0.224, abs=0.001)


def test_la_pente_relevee_en_visite_prime():
    c = crm.calculs(100.0, 1455.0, 1555.0, None, "inutilisable")
    assert c["productible"] == 1555.0 and c["productible_source"] == "visite"
    assert c["production_mwh"] == 155.5 and c["toiture_inutilisable"]


def test_l_historique_se_lit_en_francais():
    assert crm._historique({"de": "a_contacter", "vers": "perdu", "raison": "trop_cher"}, "crm_statut") \
        == "À contacter → Perdu (Trop cher)"
    assert crm._historique({"date": "2026-10-12", "objet": "rappeler le DAF"}, "crm_relance") \
        == "le 2026-10-12 : rappeler le DAF"


# ---------- filtres du tableau de bord ----------

def clauses_de(**kwargs):
    clauses, params = prospects._prospects_filter_clauses(**kwargs)
    return " AND ".join(clauses), params


def test_mes_prospects_ne_montre_que_les_miens():
    sql, params = clauses_de(commercial="moi", user_id=7)
    assert "crm_commercial_id = %s" in sql and params == [7]


def test_non_pris_et_commercial_precis():
    assert "crm_commercial_id IS NULL" in clauses_de(commercial="libres")[0]
    sql, params = clauses_de(commercial="12")
    assert "crm_commercial_id = %s" in sql and params == [12]
    # Une valeur bricolée n'ajoute rien, et surtout pas de SQL.
    assert "crm_commercial_id" not in clauses_de(commercial="1 OR 1=1")[0]


def test_etape_en_cours_et_relances_dues():
    sql, params = clauses_de(statut="en_cours", relances=True, alias="c")
    assert "COALESCE(c.crm_statut, 'a_contacter') NOT IN ('signe', 'perdu')" in sql
    assert "c.crm_relance_le <= current_date" in sql
    sql, params = clauses_de(statut="rdv_fixe")
    assert "COALESCE(crm_statut, 'a_contacter') = %s" in sql and params == ["rdv_fixe"]


# ---------- routes ----------

def _client(monkeypatch, is_admin=False):
    monkeypatch.setattr(
        auth, "_find_user_by_id",
        lambda uid: {"id": 7, "username": "commercial", "display_name": "c", "is_admin": is_admin},
    )
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=7, username="commercial", display_name="c", is_admin=is_admin)
    return client


def test_prendre_renvoie_la_fiche_a_jour(monkeypatch):
    appels = []
    monkeypatch.setattr(crm, "prendre", lambda cid, uid, admin: appels.append((cid, uid)))
    monkeypatch.setattr(crm, "fiche", lambda cid, *qui: {"id": cid, "commercial": {"id": 7}})
    r = _client(monkeypatch).post("/api/crm/42/prendre")
    assert r.status_code == 200 and r.get_json()["commercial"]["id"] == 7 and appels == [(42, 7)]


def test_un_refus_est_rendu_avec_son_code(monkeypatch):
    def deja_pris(cid, uid, admin):
        raise crm.ErreurCRM("Ce prospect est déjà suivi par un autre commercial.", 409)

    monkeypatch.setattr(crm, "prendre", deja_pris)
    r = _client(monkeypatch).post("/api/crm/42/prendre")
    assert r.status_code == 409 and "déjà suivi" in r.get_json()["error"]


def test_liberer_transmet_les_droits_admin(monkeypatch):
    appels = []
    monkeypatch.setattr(crm, "liberer", lambda cid, uid, admin: appels.append((cid, uid, admin)))
    monkeypatch.setattr(crm, "fiche", lambda cid, *qui: {"id": cid})
    _client(monkeypatch, is_admin=True).post("/api/crm/42/liberer")
    assert appels == [(42, 7, True)]


def test_une_fiche_inconnue_repond_404(monkeypatch):
    monkeypatch.setattr(crm, "fiche", lambda cid, *qui: None)
    assert _client(monkeypatch).get("/api/crm/999").status_code == 404


def test_sans_session_le_crm_est_ferme(monkeypatch):
    client = app_module.app.test_client()
    assert client.get("/api/crm/42").status_code == 401
    assert client.post("/api/crm/42/prendre").status_code == 401


# ---------- page « Mes opportunités » ----------

def test_la_page_de_suivi_s_affiche(monkeypatch):
    r = _client(monkeypatch).get("/suivi")
    assert r.status_code == 200 and "Mes opportunités" in r.get_data(as_text=True)


def test_les_opportunites_sont_les_miennes_par_defaut(monkeypatch):
    appels = []
    monkeypatch.setattr(crm, "opportunites", lambda uid, **f: appels.append((uid, f)) or {"opportunites": []})
    client = _client(monkeypatch)
    client.get("/api/crm/opportunites")
    client.get("/api/crm/opportunites?commercial=tous&statut=rdv_fixe&relances=1&search=%20acier%20")
    assert appels == [
        (7, {"is_admin": False, "commercial": "moi", "statut": None, "relances": False, "search": None}),
        (7, {"is_admin": False, "commercial": "tous", "statut": "rdv_fixe", "relances": True, "search": "acier"}),
    ]



# ---------- un commercial ne touche qu'à ses prospects ; un admin, à tous ----------

class Curseur:
    def __init__(self, base):
        self.base, self._r = base, None

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT id, name, crm_commercial_id"):
            self._r = (42, "Usine", self.base["commercial"], self.base.get("statut"), None,
                       None, self.base.get("relance_le"), self.base.get("relance_objet"))
        elif sql.startswith("UPDATE companies SET crm_commercial_id"):
            self.base["commercial"] = params[0]
            self.base["ecrit"].append("pris")
        elif sql.startswith("INSERT INTO audit_log"):
            self.base["journal"].append(params[1])
        else:
            self.base["ecrit"].append(sql.split()[0])

    def fetchone(self):
        return self._r


class Connexion:
    def __init__(self, base):
        self.base = base

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False

    def cursor(self):
        return Curseur(self.base)


@pytest.fixture
def base(monkeypatch):
    etat = {"commercial": None, "ecrit": [], "journal": []}
    pool = type("Pool", (), {"getconn": lambda self: Connexion(etat), "putconn": lambda self, c: None})()
    monkeypatch.setattr(crm, "_get_db_pool", lambda: pool)
    return etat


def test_le_prospect_d_un_collegue_est_refuse(base):
    base["commercial"] = 9
    with pytest.raises(crm.ErreurCRM, match="autre commercial") as exc:
        crm.ajouter_note(42, "Appel", 7)
    assert exc.value.code == 403 and base["ecrit"] == []


def test_agir_sur_un_prospect_libre_le_prend(base):
    crm.ajouter_note(42, "Appel", 7)
    assert base["commercial"] == 7 and base["journal"][:1] == ["crm_pris"]


def test_un_admin_consulte_sans_modifier(base):
    base["commercial"] = 9
    with pytest.raises(crm.ErreurCRM, match="sans le modifier") as exc:
        crm.ajouter_note(42, "Vu avec le client", 1, is_admin=True)
    assert exc.value.code == 403 and base["ecrit"] == [] and base["commercial"] == 9


def test_un_admin_ne_prend_pas_de_prospect(base):
    with pytest.raises(crm.ErreurCRM, match="ne prend pas") as exc:
        crm.prendre(42, 1, is_admin=True)
    assert exc.value.code == 403 and base["commercial"] is None


def test_un_admin_peut_liberer_le_prospect_d_un_commercial(base):
    base["commercial"] = 9
    crm.liberer(42, 1, is_admin=True)
    assert "crm_libere" in base["journal"]


def test_un_commercial_ne_voit_que_ses_opportunites(monkeypatch):
    requetes = []

    class Cur(Curseur):
        def execute(self, sql, params=None):
            requetes.append((" ".join(sql.split()), params))

        def fetchall(self):
            return []

    pool = type("Pool", (), {"getconn": lambda self: type("C", (Connexion,), {"cursor": lambda s: Cur({})})({}),
                             "putconn": lambda self, c: None})()
    monkeypatch.setattr(crm, "_get_db_pool", lambda: pool)
    crm.opportunites(7, commercial="tous")
    assert "c.crm_commercial_id = %s" in requetes[0][0] and requetes[0][1] == [7]
    requetes.clear()
    crm.opportunites(1, commercial="tous", is_admin=True)
    assert "c.crm_commercial_id = %s" not in requetes[0][0]



# ---------- étape et relance enregistrées ensemble ----------

class CurseurSuivi(Curseur):
    def execute(self, sql, params=None):
        sql_net = " ".join(sql.split())
        if sql_net.startswith("UPDATE companies SET crm_statut"):
            self.base["maj"] = params
        else:
            super().execute(sql, params)


@pytest.fixture
def base_suivi(monkeypatch, base):
    pool = type("Pool", (), {"getconn": lambda self: type("C", (Connexion,), {
        "cursor": lambda s: CurseurSuivi(base)})(base), "putconn": lambda self, c: None})()
    monkeypatch.setattr(crm, "_get_db_pool", lambda: pool)
    base.update(commercial=7, statut="contacte", relance_le=__import__("datetime").date(2099, 1, 1),
                relance_objet="Fixer un rendez-vous")
    return base


def test_changer_d_etape_sans_date_efface_l_ancienne_relance(base_suivi):
    crm.mettre_a_jour_suivi(42, "rdv_fixe", None, None, None, 7)
    statut, raison, relance, objet, _id = base_suivi["maj"]
    assert (statut, relance, objet) == ("rdv_fixe", None, None)
    assert base_suivi["journal"] == ["crm_statut", "crm_relance_annulee"]


def test_la_nouvelle_relance_remplace_l_ancienne(base_suivi):
    crm.mettre_a_jour_suivi(42, "rdv_fixe", None, "2099-02-01", "Visite du site", 7)
    _s, _r, relance, objet, _id = base_suivi["maj"]
    assert (relance.isoformat(), objet) == ("2099-02-01", "Visite du site")
    assert base_suivi["journal"] == ["crm_statut", "crm_relance"]


def test_rien_de_change_rien_au_journal(base_suivi):
    crm.mettre_a_jour_suivi(42, "contacte", None, "2099-01-01", "Fixer un rendez-vous", 7)
    assert base_suivi["journal"] == []
