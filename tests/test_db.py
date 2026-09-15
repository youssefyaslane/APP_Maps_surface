"""Échappement du schéma dans l'option de connexion libpq.

Le schéma de production s'appelle `solar intelligence` : un espace mal
échappé fait découper l'option en deux par libpq, et la connexion échoue ou,
pire, le chemin de recherche retombe sur `public`, où vivent les tables d'un
autre projet.
"""
import db


def test_simple_schema_is_quoted():
    assert db.search_path_option("solar_intelligence") == '-csearch_path="solar_intelligence"'


def test_space_is_escaped_for_libpq():
    assert db.search_path_option("solar intelligence") == '-csearch_path="solar\\ intelligence"'


def test_double_quote_in_name_is_doubled():
    assert db.search_path_option('a"b') == '-csearch_path="a""b"'


def test_backslash_is_escaped_for_libpq():
    assert db.search_path_option("a\\b") == '-csearch_path="a\\\\b"'


def test_no_schema_means_no_option(monkeypatch):
    monkeypatch.setattr(db, "DB_SCHEMA", None)
    assert db.connect_kwargs() == {}
