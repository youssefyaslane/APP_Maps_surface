"""Règles de la migration entre bases qui se vérifient sans base.

La copie elle-même est éprouvée sur de vraies bases ; restent ici les règles
dont l'oubli ne se verrait qu'au pire moment : une colonne générée que COPY
refuse d'écrire, une table copiée avant celle qu'elle référence, un message
d'erreur PostgreSQL brut montré à un administrateur.
"""
import pytest

import db_migration
import schema


def test_les_colonnes_generees_ne_sont_pas_copiees():
    colonnes = [("id", "NEVER"), ("polygon", "NEVER"), ("geom_hash", "ALWAYS")]
    assert db_migration.copyable(colonnes) == ["id", "polygon"]


def test_une_table_arrive_apres_celles_qu_elle_reference():
    ordre = schema.COPY_ORDER
    assert ordre.index("users") < ordre.index("ia_segments")
    assert ordre.index("users") < ordre.index("audit_log")


def test_toutes_les_tables_obligatoires_et_a_sequence_sont_copiees():
    assert set(schema.REQUIRED_TABLES) <= set(schema.COPY_ORDER)
    assert set(schema.SERIAL_TABLES) <= set(schema.COPY_ORDER)


def test_chaque_table_a_sa_cle_de_rapprochement():
    assert schema.primary_key("osm_buildings") == "osm_id"
    assert schema.primary_key("ia_segments") == "id"


def _diff(**tables):
    base = {"added": 0, "changed": 0, "removed": 0}
    return {name: {**base, **counts} for name, counts in tables.items()}


def test_une_cible_qui_a_servi_plus_recemment_n_est_pas_ecrasee():
    # Le journal ne perd jamais de lignes : en trouver dans la cible qui
    # manquent à la base active, c'est que la cible a servi depuis.
    raison = db_migration.blocked_reason(_diff(audit_log={"removed": 3}))
    assert raison is not None and "3 action" in raison


def test_des_toits_supprimes_sur_la_base_active_ne_bloquent_pas():
    diff = _diff(ia_segments={"removed": 2}, audit_log={"added": 5})
    assert db_migration.blocked_reason(diff) is None


def test_une_synchronisation_refusee_n_ecrit_aucune_trace(monkeypatch):
    # La trace est écrite dans la base active juste avant la copie : elle ne
    # doit pas l'être si la synchronisation est refusée pour mauvais sens.
    def cible_plus_recente(target):
        raise db_migration.MigrationError("La base cible contient 2 action(s)…")

    traces = []
    monkeypatch.setattr(db_migration, "_check_not_stale", cible_plus_recente)
    with pytest.raises(db_migration.MigrationError):
        db_migration.sync({"host": "h"}, apply=True, before_apply=lambda: traces.append("trace"))
    assert traces == []


def test_deux_bases_identiques():
    assert db_migration.is_identical(_diff(users={}, audit_log={}))
    assert not db_migration.is_identical(_diff(users={"changed": 1}))


@pytest.mark.parametrize(
    "brut, attendu",
    [
        ('FATAL:  password authentication failed for user "x"', "mot de passe refusé"),
        ('FATAL:  database "mabase" does not exist', "La base « mabase » n'existe pas"),
        ('FATAL:  role "appli" does not exist', "L'utilisateur « appli » n'existe pas"),
        ('could not translate host name "srv" to address: Name or service not known', "Serveur introuvable"),
        ('connection to server at "10.0.0.1", port 5432 failed: Connection refused', "refuse la connexion"),
        ("timeout expired", "ne répond pas"),
    ],
)
def test_les_erreurs_postgresql_sont_traduites(brut, attendu):
    assert attendu in db_migration.explain(Exception(brut))
