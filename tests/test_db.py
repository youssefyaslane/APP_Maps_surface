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


def test_une_configuration_de_la_page_donne_tous_ses_parametres():
    # Tous explicites : libpq ne doit rien compléter avec les PG* du .env,
    # qui désignent une autre base.
    cible = {"host": "h", "port": 5433, "dbname": "b", "user": "u",
             "password": "p", "sslmode": "require", "schema": "mon schéma"}
    dsn, kwargs = db.connect_params(cible)
    assert dsn == ""
    assert kwargs == {
        "host": "h", "port": 5433, "dbname": "b", "user": "u", "password": "p",
        "sslmode": "require", "options": db.search_path_option("mon schéma"),
    }


def test_sans_configuration_active_c_est_le_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(db, "DATABASE_URL", "")
    monkeypatch.setattr(db, "DB_SCHEMA", "solar intelligence")
    assert db.connect_params() == ("", {"options": db.search_path_option("solar intelligence")})


def test_sans_schema_c_est_public():
    assert db.schema_of({"host": "h", "schema": ""}) == "public"


def test_la_description_du_env_ne_montre_jamais_le_mot_de_passe(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "ne-pas-afficher")
    assert "ne-pas-afficher" not in str(db.env_description())
