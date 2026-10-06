"""Outil 1 : lance la recherche Google Maps sur Apify.

Prend les requêtes et la ville préparées par le chatbot, lance l'acteur
compass/crawler-google-places et renvoie les lieux trouvés, nettoyés. Mêmes
réglages d'économie que scraping_client/Scraper_toits.py : ni avis, ni images,
ni enrichissement, qui gonflent le coût.
"""
import os

ACTEUR = "compass/crawler-google-places"
# « Number of places to extract (per each search term or URL) » dans Apify :
# 3 requêtes au plus (voir chatbot.MAX_REQUETES), donc 60 lieux au plus.
MAX_PAR_REQUETE = 20


class ApifyIndisponible(RuntimeError):
    """La recherche ne peut pas partir (jeton absent) ou a échoué."""


def construire_entree(requetes, ville, max_par_requete=MAX_PAR_REQUETE):
    return {
        "searchStringsArray": list(requetes),
        "locationQuery": f"{ville}, Maroc",
        "maxCrawledPlacesPerSearch": max_par_requete,
        "language": "fr",
        "maxImages": 0,
        "maxReviews": 0,
        "includeOpeningHours": False,
        "additionalInfo": False,
        "maximumLeadsEnrichmentRecords": 0,
    }


def normaliser(items):
    """Lieux Apify → dictionnaires simples. Sans nom, sans GPS ou sans
    place_id, un lieu ne peut ni être rattaché à un toit ni dédoublonné :
    il est écarté. Un même lieu trouvé par deux requêtes n'est gardé qu'une fois."""
    lieux, vus = [], set()
    for it in items:
        loc = it.get("location") or {}
        lat, lon, place_id = loc.get("lat"), loc.get("lng"), it.get("placeId")
        if not it.get("title") or lat is None or lon is None or not place_id or place_id in vus:
            continue
        vus.add(place_id)
        lieux.append({
            "nom": it.get("title"),
            "categorie": (it.get("categoryName") or "").strip() or None,
            "adresse": it.get("address"),
            "ville": it.get("city"),
            "telephone": it.get("phone") or it.get("phoneUnformatted"),
            "site": it.get("website"),
            "note": it.get("totalScore"),
            "lat": lat,
            "lon": lon,
            "place_id": place_id,
            "url": it.get("url"),
        })
    return lieux


def _champ(objet, *noms):
    """L'API Apify renvoie des dictionnaires ou des objets selon la version."""
    for nom in noms:
        valeur = objet.get(nom) if isinstance(objet, dict) else getattr(objet, nom, None)
        if valeur is not None:
            return valeur
    return None


def rechercher(requetes, ville, client=None):
    """(lieux, coût en dollars ou None). `client` se remplace dans les tests."""
    if client is None:
        jeton = os.environ.get("APIFY_API_TOKEN", "").strip()
        if not jeton:
            raise ApifyIndisponible("Jeton Apify absent : renseigner APIFY_API_TOKEN dans .env.")
        from apify_client import ApifyClient

        client = ApifyClient(jeton)
    run = client.actor(ACTEUR).call(run_input=construire_entree(requetes, ville))
    dataset = _champ(run, "default_dataset_id", "defaultDatasetId") if run else None
    if not dataset:
        raise ApifyIndisponible("La recherche Apify n'a rien renvoyé (exécution échouée ou interrompue).")
    items = list(client.dataset(dataset).iterate_items())
    return normaliser(items), _cout(client, run)


def _cout(client, run):
    """Coût final de l'exécution, relu une fois celle-ci terminée : le chiffre
    porté par la réponse de call() n'inclut pas encore tout ce qu'Apify
    facture à l'événement (0,0002 $ affiché pour 0,08 $ réels). Indicatif :
    None si Apify ne le donne pas."""
    cout = None
    try:
        detail = client.run(_champ(run, "id")).get()
        cout = _champ(detail, "usage_total_usd", "usageTotalUsd") if detail else None
    except Exception:  # noqa: BLE001 — le coût n'est qu'indicatif
        cout = None
    if not cout:
        cout = _champ(run, "usage_total_usd", "usageTotalUsd")
    return float(cout) if cout else None
