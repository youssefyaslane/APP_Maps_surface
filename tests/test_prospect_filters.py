"""Clauses de filtrage du tableau de bord.

Fonction pure produisant du SQL : elle se teste sans base, et c'est elle qui
décide ce que voit un commercial quand il choisit une ville ou un secteur.
"""
import app


def clauses_de(**kwargs):
    clauses, params = app._prospects_filter_clauses(**kwargs)
    return " AND ".join(clauses), params


def test_sans_filtre_on_ne_garde_que_les_prospects_calcules():
    sql, params = clauses_de()
    assert "solar_computed_at IS NOT NULL" in sql
    assert "roof_area_m2 IS NOT NULL" in sql
    assert params == []


def test_ville_et_categorie_sont_compares_exactement():
    # Ces deux champs viennent de listes déroulantes alimentées par la base.
    # Un ILIKE '%...%' ferait remonter les valeurs englobantes — choisir
    # « Fabricant » ramenait aussi « Fabricant de meubles » — et le nombre
    # annoncé en face du choix ne correspondrait plus aux lignes obtenues.
    sql, params = clauses_de(city="Casablanca", category="Entrepôt")
    assert "lower(trim(city)) = lower(%s)" in sql
    assert "lower(trim(category)) = lower(%s)" in sql
    assert "ILIKE" not in sql
    assert params == ["Casablanca", "Entrepôt"]


def test_la_recherche_libre_reste_floue_sur_le_nom_et_l_adresse():
    sql, params = clauses_de(search="steel")
    assert "name ILIKE %s OR" in sql
    assert params == ["%steel%", "%steel%"]


def test_l_alias_prefixe_toutes_les_colonnes():
    # La requête principale joint le décompte des toits partagés : sans préfixe,
    # PostgreSQL rejette les colonnes ambiguës.
    sql, _ = clauses_de(city="Casablanca", min_kwc=100, alias="c")
    assert "c.solar_kwc >= %s" in sql
    assert "lower(trim(c.city))" in sql
    assert "c.solar_computed_at IS NOT NULL" in sql


def test_l_ordre_des_parametres_suit_celui_des_clauses():
    # psycopg2 substitue par position : une clause ajoutée sans son paramètre au
    # bon rang appliquerait le filtre d'une colonne à une autre.
    sql, params = clauses_de(min_kwc=100, city="Rabat", category="Entrepôt", search="acier")
    assert sql.count("%s") == len(params)
    assert params == [100, "Rabat", "Entrepôt", "%acier%", "%acier%"]


def test_un_filtre_vide_est_ignore():
    # Le « — Toutes les villes — » de la liste envoie une chaîne vide : elle ne
    # doit pas devenir une clause qui ne remonterait aucun prospect.
    _, params = clauses_de(city="", category="", search="")
    assert params == []


def test_une_puissance_minimale_nulle_reste_un_filtre():
    # 0 est une valeur, pas une absence : le code doit distinguer « aucun seuil »
    # (None) de « seuil à zéro ».
    sql, params = clauses_de(min_kwc=0)
    assert "solar_kwc >= %s" in sql
    assert params == [0]
