"""Configurations de base enregistrées depuis la page d'administration.

Ce qui casserait en silence : un mot de passe écrit en clair, une
configuration active modifiable ou supprimable sous l'application, une clé
changée qui ferait lire n'importe quoi au lieu de refuser, une configuration
active disparue qui renverrait sans prévenir vers une autre base.
"""
import json

import pytest

import db_configs

VALIDE = {
    "label": "Test",
    "host": "10.0.0.5",
    "port": "5432",
    "dbname": "base",
    "schema": "mon schéma",
    "user": "appli",
    "password": "s3cret-tres-long",
    "sslmode": "require",
}


@pytest.fixture(autouse=True)
def dossier(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("DB_CONFIG_KEY", "cle-de-test")
    return tmp_path


def test_sans_fichier_c_est_le_env_qui_s_applique():
    assert db_configs.active_target() is None
    assert db_configs.load() == {"active": None, "configs": []}


def test_le_mot_de_passe_n_est_jamais_ecrit_en_clair(dossier):
    db_configs.add(VALIDE)
    contenu = (dossier / db_configs.FILENAME).read_text(encoding="utf-8")
    assert "s3cret-tres-long" not in contenu


def test_la_configuration_active_est_relue_dechiffree():
    config_id = db_configs.add(VALIDE)
    db_configs.set_active(config_id)
    cible = db_configs.active_target()
    assert cible["password"] == "s3cret-tres-long"
    assert cible["schema"] == "mon schéma"
    assert cible["port"] == 5432


def test_l_affichage_ne_contient_aucun_mot_de_passe():
    db_configs.add(VALIDE)
    configs, _ = db_configs.list_public()
    assert all("password" not in c for c in configs)


def test_sans_cle_on_refuse_d_enregistrer(monkeypatch):
    monkeypatch.delenv("DB_CONFIG_KEY")
    with pytest.raises(db_configs.ConfigError):
        db_configs.add(VALIDE)


def test_une_cle_changee_refuse_de_relire_plutot_que_de_deviner(monkeypatch):
    db_configs.set_active(db_configs.add(VALIDE))
    monkeypatch.setenv("DB_CONFIG_KEY", "autre-cle")
    with pytest.raises(db_configs.ConfigError):
        db_configs.active_target()


def test_la_configuration_active_ne_peut_etre_ni_modifiee_ni_supprimee():
    config_id = db_configs.add(VALIDE)
    db_configs.set_active(config_id)
    with pytest.raises(db_configs.ConfigError):
        db_configs.delete(config_id)
    with pytest.raises(db_configs.ConfigError):
        db_configs.update(config_id, VALIDE)


def test_modifier_sans_mot_de_passe_conserve_l_ancien():
    config_id = db_configs.add(VALIDE)
    db_configs.update(config_id, {**VALIDE, "password": "", "host": "10.0.0.6"})
    cible = db_configs.target(config_id)
    assert cible["host"] == "10.0.0.6"
    assert cible["password"] == "s3cret-tres-long"


@pytest.mark.parametrize(
    "champ, valeur",
    [("host", ""), ("dbname", ""), ("port", "0"), ("port", "abc"), ("sslmode", "peut-etre"), ("password", "")],
)
def test_un_champ_invalide_est_refuse(champ, valeur):
    with pytest.raises(db_configs.ConfigError):
        db_configs.add({**VALIDE, champ: valeur})


def test_la_base_du_env_porte_un_nom_par_defaut():
    assert db_configs.env_label() == "Fichier .env"


def test_on_peut_renommer_la_base_du_env_sans_cle(monkeypatch):
    # Un nom n'a rien de secret : le renommage ne dépend pas de DB_CONFIG_KEY.
    monkeypatch.delenv("DB_CONFIG_KEY")
    db_configs.set_env_label("Production")
    assert db_configs.env_label() == "Production"


def test_un_nom_vide_rend_le_nom_par_defaut():
    db_configs.set_env_label("Production")
    db_configs.set_env_label("  ")
    assert db_configs.env_label() == "Fichier .env"


def test_revenir_au_env():
    db_configs.set_active(db_configs.add(VALIDE))
    db_configs.set_active(None)
    assert db_configs.active_target() is None


def test_une_active_disparue_bloque_au_lieu_de_basculer_en_silence(dossier):
    db_configs.set_active(db_configs.add(VALIDE))
    path = dossier / db_configs.FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    data["configs"] = []
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(db_configs.ConfigError):
        db_configs.active_target()
