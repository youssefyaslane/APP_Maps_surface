"""Productible solaire (kWh produits par kWc installé et par an) d'un lieu,
demandé à PVGIS, l'outil de la Commission européenne
(https://re.jrc.ec.europa.eu/pvg_tools/fr/) : même moteur et mêmes chiffres
que son site, interrogé par son API publique, gratuite et sans clé.

L'ensoleillement change peu sur quelques kilomètres : les entreprises sont
regroupées par case de 0,1° (environ 10 km) et chaque case n'est demandée
qu'une fois, puis gardée dans la table `pvgis_cache`. Toute la région de
Casablanca tient en quelques appels.
"""
import json
import time

import requests

from services import solar

API = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
CASE_DEG = 0.1
TIMEOUT_S = 60
ESSAIS = 3


class PvgisIndisponible(Exception):
    pass


def case(lat, lon):
    return round(float(lat), 1), round(float(lon), 1)


def reglages(inclinaison=None, orientation=None):
    """Réglages de pose envoyés à PVGIS ; ils font partie de la clé du cache,
    si bien qu'en changer redemande les cases au lieu de servir l'ancien chiffre.
    `inclinaison` et `orientation` remplacent les réglages par défaut, avec les
    valeurs relevées lors d'une visite."""
    return {
        "angle": solar.SOLAR_TILT_DEG if inclinaison is None else float(inclinaison),
        "aspect": solar.SOLAR_AZIMUTH_DEG if orientation is None else float(orientation),
        "loss": solar.SOLAR_SYSTEM_LOSS_PCT,
        "mountingplace": solar.SOLAR_MOUNTING,
    }


def demander(lat, lon, session=None, inclinaison=None, orientation=None):
    """Interroge PVGIS pour 1 kWc à ce point. Renvoie {productible, mensuel,
    ensoleillement}, ou None pour un point que PVGIS ne couvre pas (en mer).
    `session` se remplace dans les tests."""
    params = {"lat": lat, "lon": lon, "peakpower": 1, "outputformat": "json",
              **reglages(inclinaison, orientation)}
    http = session or requests
    for essai in range(ESSAIS):
        try:
            reponse = http.get(API, params=params, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            erreur = exc
        else:
            if reponse.status_code == 400:
                return None   # « Location over the sea » et autres points hors couverture
            if reponse.ok:
                sortie = reponse.json()["outputs"]
                total = sortie["totals"]["fixed"]
                return {
                    "productible": round(total["E_y"], 1),
                    "mensuel": [round(m["E_m"], 1) for m in sortie["monthly"]["fixed"]],
                    "ensoleillement": round(total["H(i)_y"], 1),
                }
            erreur = PvgisIndisponible(f"PVGIS a répondu {reponse.status_code}")
        time.sleep(2 ** essai)
    raise PvgisIndisponible(str(erreur))


def productible(cur, lat, lon, session=None, inclinaison=None, orientation=None):
    """Productible (kWh/kWc/an) de la case de ce point, depuis le cache ou PVGIS."""
    clat, clon = case(lat, lon)
    r = reglages(inclinaison, orientation)
    cle = (clat, clon, r["angle"], r["aspect"], r["loss"], r["mountingplace"])
    cur.execute(
        """
        SELECT productible FROM pvgis_cache
        WHERE case_lat = %s AND case_lon = %s AND inclinaison = %s AND orientation = %s
          AND pertes = %s AND montage = %s
        """,
        cle,
    )
    ligne = cur.fetchone()
    if ligne:
        return ligne[0]
    resultat = demander(clat, clon, session, inclinaison, orientation)
    if resultat is None:
        return None
    cur.execute(
        """
        INSERT INTO pvgis_cache (case_lat, case_lon, inclinaison, orientation, pertes, montage,
                                 productible, mensuel, ensoleillement)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (*cle, resultat["productible"], json.dumps(resultat["mensuel"]), resultat["ensoleillement"]),
    )
    return resultat["productible"]


def remplir(conn, tout=False, session=None, ids=None, bavard=True):
    """Enregistre le productible de chaque entreprise qui n'en a pas encore
    (toutes avec `tout`, après un changement de réglage ; seulement `ids` si
    donné, comme après une recherche du chatbot). Renvoie le nombre
    d'entreprises mises à jour."""
    conditions, params = [], []
    if not tout:
        conditions.append("solar_yield_kwh_kwc IS NULL")
    if ids is not None:
        conditions.append("id = ANY(%s)")
        params.append(list(ids))
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, lat, lon FROM companies"
            + (" WHERE " + " AND ".join(conditions) if conditions else ""),
            params,
        )
        par_case = {}
        for ident, lat, lon in cur.fetchall():
            par_case.setdefault(case(lat, lon), []).append(ident)

    afficher = print if bavard else (lambda *a: None)
    afficher(f"Productible PVGIS : {sum(map(len, par_case.values()))} entreprise(s), {len(par_case)} zone(s).")
    faites = 0
    for (clat, clon), ids in sorted(par_case.items()):
        # Une transaction par zone : interrompu, le calcul reprend là où il
        # s'est arrêté, et le cache garde les zones déjà demandées.
        with conn, conn.cursor() as cur:
            valeur = productible(cur, clat, clon, session)
            cur.execute(
                "UPDATE companies SET solar_yield_kwh_kwc = %s WHERE id = ANY(%s)",
                (valeur, ids),
            )
        faites += len(ids) if valeur else 0
        if valeur is None:
            afficher(f"  zone {clat}, {clon} hors couverture PVGIS : {len(ids)} entreprise(s) sans productible")
    afficher(f"  {faites} entreprise(s) avec leur productible.")
    return faites
