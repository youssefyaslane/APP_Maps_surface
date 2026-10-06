"""Nœud 1 : le chatbot récupère la requête et la localisation.

OpenAI lit la conversation et en tire ce qu'il faut chercher et où. C'est
ensuite le code, pas le modèle, qui décide si la demande est complète : au
moins une requête ET une ville, sinon le chatbot pose la question au lieu de
deviner. Le nombre de résultats est fixé à MAX_RESULTATS par requête pour
l'instant, quoi que demande l'utilisateur : chaque résultat Apify est payé.
"""
import os

from langchain_core.messages import AIMessage, SystemMessage
from pydantic import BaseModel, Field

from scripts.import_companies import load_known_cities, resolve_city
from services import db

MAX_RESULTATS = 20       # par requête : « Number of places to extract » d'Apify
MAX_REQUETES = 3          # au-delà, une seule demande lancerait trop de recherches Apify
LONGUEUR_REQUETE_MAX = 60
MODELE_PAR_DEFAUT = "gpt-5-mini"

CONSIGNES = """Tu aides des commerciaux à chercher des entreprises au Maroc sur Google Maps.
Lis toute la conversation et extrais :
- requetes : le ou les types d'entreprises cherchés, en mots-clés courts, sans la ville
  (par exemple « usine », « entrepôt frigorifique », « laboratoire pharmaceutique ») ;
- ville : la ville ou la localisation donnée par l'utilisateur.
Si l'utilisateur n'a pas donné de ville, ville vaut null : ne la devine jamais.
Si l'utilisateur n'a pas dit quoi chercher, requetes est une liste vide.
Le message peut être long : ignore ce qui ne dit ni quoi chercher ni où
(présentation, contexte, politesse).
Si l'utilisateur corrige sa demande, garde la dernière version."""


class Demande(BaseModel):
    """Ce que le modèle doit rendre, toujours dans ce format."""

    requetes: list[str] = Field(
        default_factory=list,
        description="Types d'entreprises cherchés, en mots-clés courts, sans la ville. Vide si non précisé.",
    )
    ville: str | None = Field(
        default=None,
        description="Ville ou localisation donnée par l'utilisateur. null si aucune : ne jamais deviner.",
    )


class ChatbotIndisponible(RuntimeError):
    """Le modèle ne peut pas être appelé (clé absente)."""


def _modele_openai():
    if not os.environ.get("OPENAI_API_KEY"):
        raise ChatbotIndisponible("Clé OpenAI absente : renseigner OPENAI_API_KEY dans .env.")
    # Import ici : l'application démarre même si la clé ou le paquet manquent.
    from langchain_openai import ChatOpenAI

    nom = os.environ.get("OPENAI_MODEL", "").strip() or MODELE_PAR_DEFAUT
    return ChatOpenAI(model=nom, timeout=60, max_retries=2).with_structured_output(Demande)


def villes_connues():
    """Villes déjà en base, pour reprendre leur orthographe (« casa blanca »
    ne doit pas créer une seconde Casablanca)."""
    conn = db.connect()
    try:
        return load_known_cities(conn.cursor())
    finally:
        conn.close()


def verifier(demande, villes):
    """Nettoie ce que le modèle a extrait et dit si la recherche peut partir."""
    requetes, vues = [], set()
    for brute in demande.requetes:
        requete = " ".join(str(brute).split())[:LONGUEUR_REQUETE_MAX]
        if requete and requete.lower() not in vues:
            vues.add(requete.lower())
            requetes.append(requete)
    requetes = requetes[:MAX_REQUETES]
    # Une ville inconnue de la base est gardée telle quelle (nettoyée) : on
    # doit pouvoir chercher dans une ville où l'on n'a encore personne.
    ville, _connue = resolve_city(demande.ville, villes)
    return {
        "requetes": requetes,
        "ville": ville,
        "max_resultats": MAX_RESULTATS,
        "pret": bool(requetes and ville),
    }


def _liste(requetes):
    return ", ".join(f"« {r} »" for r in requetes)


def message_pour(resultat):
    """Texte du chatbot : la question qui manque, ou le récapitulatif."""
    requetes, ville = resultat["requetes"], resultat["ville"]
    if resultat["pret"]:
        return (f"Recherche prête : {_liste(requetes)} à {ville}, {resultat['max_resultats']} résultats "
                f"maximum par requête. Cliquez sur « Lancer » pour démarrer la recherche Apify.")
    if not requetes and not ville:
        return "Que voulez-vous chercher, et où ? Par exemple : « usine à Casablanca »."
    if not ville:
        return f"Dans quelle ville faut-il chercher {_liste(requetes)} ?"
    return f"Quel type d'entreprise faut-il chercher à {ville} ? Par exemple : usine, entrepôt, laboratoire."


def creer_noeud_chatbot(modele=None, villes=None):
    """Nœud du graphe. `modele` et `villes` se remplacent dans les tests, pour
    tourner sans OpenAI ni base."""

    def chatbot(etat):
        llm = modele if modele is not None else _modele_openai()
        demande = llm.invoke([SystemMessage(CONSIGNES), *etat["messages"]])
        resultat = verifier(demande, villes if villes is not None else villes_connues())
        return {**resultat, "messages": [AIMessage(message_pour(resultat))]}

    return chatbot
