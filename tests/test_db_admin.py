"""Garde-fous de l'activation d'une autre base, et accès à la page.

L'activation fait passer toute l'application sur une autre base. Les refus
non forçables évitent une situation dont on ne sort qu'en ligne de commande :
une base sans les tables, ou une base où votre compte n'existe pas — la
relecture du compte à chaque requête vous déconnecterait sur-le-champ.

Le refus forçable protège le travail récent : une base en retard sur la base
active ne s'active pas sans avertissement, sinon ce travail resterait derrière
et une migration dans l'autre sens l'effacerait. Les bases sont simulées.
"""
import app as app_module
import db_configs

ADMIN = {"id": 6, "username": "admin_netis"}
TABLES = ("users", "companies", "ia_segments", "ms_buildings", "osm_buildings", "audit_log")
PRETE = {
    "ok": True,
    "error": None,
    "schema": "cible",
    "schema_exists": True,
    "can_create": True,
    "tables": {t: 1 for t in TABLES},
    "admin_present": True,
}
IDENTIQUES = {"diff": {t: {"added": 0, "changed": 0, "removed": 0} for t in TABLES}, "blocked": None}
EN_RETARD = {
    "diff": {**IDENTIQUES["diff"], "ia_segments": {"added": 12, "changed": 0, "removed": 0}},
    "blocked": None,
}


def _preparer(monkeypatch, rapport, active=None, comparaison=IDENTIQUES):
    bascules = []
    monkeypatch.setattr(app_module.db_migration, "inspect", lambda target, admin=None: rapport)
    monkeypatch.setattr(app_module.db_migration, "sync", lambda target, **kwargs: comparaison)
    monkeypatch.setattr(app_module.db_migration, "job_status", lambda: {"state": "idle"})
    monkeypatch.setattr(app_module.db_configs, "active_id", lambda: active)
    monkeypatch.setattr(app_module.db_configs, "target", lambda config_id: {"id": config_id})
    monkeypatch.setattr(app_module.db_configs, "set_active", bascules.append)
    monkeypatch.setattr(app_module, "_reset_db_pool", lambda: None)
    monkeypatch.setattr(app_module, "_log_database_event", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "_db_label", lambda config_id: config_id)
    return bascules


def _refus(resultat):
    assert resultat is not None, "l'activation aurait dû être refusée"
    return resultat


def test_une_base_a_jour_est_activee(monkeypatch):
    bascules = _preparer(monkeypatch, PRETE)
    assert app_module._activate_database("abc", ADMIN) is None
    assert bascules == ["abc"]


def test_refus_si_votre_compte_n_existe_pas_sur_la_cible(monkeypatch):
    bascules = _preparer(monkeypatch, {**PRETE, "admin_present": False})
    message, forcable = _refus(app_module._activate_database("abc", ADMIN))
    assert "compte administrateur" in message and not forcable
    assert bascules == []


def test_refus_si_des_tables_manquent(monkeypatch):
    tables = {**PRETE["tables"], "companies": None}
    bascules = _preparer(monkeypatch, {**PRETE, "tables": tables})
    message, forcable = _refus(app_module._activate_database("abc", ADMIN))
    assert "companies" in message and not forcable
    assert bascules == []


def test_refus_si_le_schema_n_existe_pas(monkeypatch):
    bascules = _preparer(monkeypatch, {**PRETE, "schema_exists": False})
    message, _ = _refus(app_module._activate_database("abc", ADMIN))
    assert "schéma" in message
    assert bascules == []


def test_refus_pendant_une_migration(monkeypatch):
    bascules = _preparer(monkeypatch, PRETE)
    monkeypatch.setattr(app_module.db_migration, "job_status", lambda: {"state": "running"})
    message, _ = _refus(app_module._activate_database("abc", ADMIN))
    assert "migration" in message
    assert bascules == []


def test_une_base_en_retard_n_est_pas_activee_sans_avertissement(monkeypatch):
    # Même quand le travail récent vient d'un script d'import, qui n'écrit
    # rien au journal : c'est la comparaison des bases qui le révèle.
    bascules = _preparer(monkeypatch, PRETE, comparaison=EN_RETARD)
    message, forcable = _refus(app_module._activate_database("abc", ADMIN))
    assert "12 ligne(s) à ajouter" in message and forcable
    assert bascules == []


def test_une_base_en_retard_s_active_si_on_force(monkeypatch):
    bascules = _preparer(monkeypatch, PRETE, comparaison=EN_RETARD)
    assert app_module._activate_database("abc", ADMIN, force=True) is None
    assert bascules == ["abc"]


def test_retour_a_la_base_precedente_si_la_nouvelle_ne_repond_pas(monkeypatch):
    bascules = _preparer(monkeypatch, PRETE)

    def en_panne(*args, **kwargs):
        raise RuntimeError("injoignable")

    monkeypatch.setattr(app_module, "_log_database_event", en_panne)
    message, _ = _refus(app_module._activate_database("abc", ADMIN))
    assert "retour" in message
    # Bascule vers « abc », puis retour au .env, qui était la base active.
    assert bascules == ["abc", None]


def test_revenir_au_env_desactive_la_configuration(monkeypatch):
    bascules = _preparer(monkeypatch, PRETE, active="abc")
    assert app_module._activate_database(db_configs.ENV_ID, ADMIN) is None
    assert bascules == [None]


def _client_admin(monkeypatch):
    monkeypatch.setattr(
        app_module, "_find_user_by_id",
        lambda uid: {"id": 6, "username": "admin_netis", "display_name": "a", "is_admin": True},
    )
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=6, username="admin_netis", display_name="a", is_admin=True)
    return client


def test_on_peut_migrer_vers_la_base_du_env(monkeypatch):
    # Travail fait sur une autre base : il doit pouvoir revenir dans celle du .env.
    lancements = []
    monkeypatch.setattr(app_module.db_configs, "active_id", lambda: "abc")
    monkeypatch.setattr(
        app_module.db_migration, "start",
        lambda target, label, apply=False, before_apply=None: lancements.append((target, apply, before_apply)),
    )
    client = _client_admin(monkeypatch)
    reponse = client.post("/admin/database/configs/env/migrate", data={"mode": "preview"})
    assert reponse.status_code == 200
    assert lancements == [(app_module.db.ENV, False, None)]


def test_seule_la_mise_a_jour_ecrit_une_trace(monkeypatch):
    lancements = []
    monkeypatch.setattr(app_module.db_configs, "active_id", lambda: "abc")
    monkeypatch.setattr(
        app_module.db_migration, "start",
        lambda target, label, apply=False, before_apply=None: lancements.append((apply, before_apply)),
    )
    client = _client_admin(monkeypatch)
    client.post("/admin/database/configs/env/migrate", data={"mode": "apply"})
    apply, before_apply = lancements[0]
    assert apply is True and callable(before_apply)


def test_renommer_la_base_du_env(monkeypatch):
    noms = []
    monkeypatch.setattr(app_module.db_configs, "set_env_label", noms.append)
    client = _client_admin(monkeypatch)
    reponse = client.post("/admin/database/configs/env/edit", data={"label": "Production"})
    assert reponse.status_code == 302
    assert noms == ["Production"]


def test_seul_un_administrateur_entre_sur_la_page(monkeypatch):
    monkeypatch.setattr(
        app_module, "_find_user_by_id",
        lambda uid: {"id": 7, "username": "commercial", "display_name": "c", "is_admin": False},
    )
    client = app_module.app.test_client()
    with client.session_transaction() as s:
        s.update(user_id=7, username="commercial", display_name="c", is_admin=False)
    assert client.get("/admin/database").status_code == 403
    assert client.post("/admin/database/configs/env/test").status_code == 403
    assert client.post("/admin/database/configs/env/activate").status_code == 403
