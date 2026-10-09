"""Outil 3 : le potentiel solaire des entreprises que l'outil 2 vient d'écrire.

Même calcul que scripts/compute_solar_potential.py, limité à ces entreprises :
1. le toit sous chacune (OSM, toits IA ou tracés, Microsoft) et sa puissance ;
2. le productible PVGIS de leur zone (kWh par kWc et par an) ;
3. la production par an et le CO₂ évité, qui s'en déduisent.

Le bilan du chatbot donne ainsi directement les kWc, sans commande à lancer.
"""
from services import db, pvgis
from services.solar import co2_evite_t, economies_dh, production_mwh
from services.toits import _recompute_solar_for_companies

RECHERCHES_PARALLELES = 6   # comme le calcul en masse : l'appel Overpass domine


def calculer(place_ids, connecter=None, recalculer_toits=None):
    """{place_id: {id, kwc, production_mwh, co2_t}} pour ces entreprises ;
    {id, kwc: None, non_calcule: True} pour celles dont le toit n'a pas pu être
    cherché (réseau), que le calcul en masse reprendra.
    `connecter` et `recalculer_toits` se remplacent dans les tests."""
    if not place_ids:
        return {}
    conn = (connecter or db.connect)()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT id, place_id FROM companies WHERE place_id = ANY(%s)", (list(place_ids),))
            par_id = {ident: pid for ident, pid in cur.fetchall()}
        ids = list(par_id)
        (recalculer_toits or _recompute_solar_for_companies)(ids, workers=RECHERCHES_PARALLELES)
        try:
            pvgis.remplir(conn, ids=ids, bavard=False)
        except pvgis.PvgisIndisponible:
            pass   # la puissance est là ; la production attendra le prochain calcul
        with conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, solar_kwc, solar_yield_kwh_kwc, solar_computed_at IS NOT NULL "
                "FROM companies WHERE id = ANY(%s)", (ids,)
            )
            lignes = cur.fetchall()
    finally:
        conn.close()
    resultat = {}
    for ident, kwc, rendement, calcule in lignes:
        if not calcule:
            resultat[par_id[ident]] = {"id": ident, "kwc": None, "non_calcule": True}
            continue
        mwh = production_mwh(kwc, rendement)
        resultat[par_id[ident]] = {
            "id": ident,
            "kwc": kwc or 0.0,
            "production_mwh": mwh,
            "co2_t": co2_evite_t(mwh),
            "economies_dh": economies_dh(mwh),
        }
    return resultat
