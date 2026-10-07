"""État partagé du graphe : chaque nœud lit ce dont il a besoin et ajoute
son résultat."""
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class EtatProspection(TypedDict, total=False):
    # La conversation : add_messages ajoute les nouveaux messages au lieu de
    # remplacer la liste.
    messages: Annotated[list[AnyMessage], add_messages]
    # Remplis par le chatbot.
    requetes: list[str]
    ville: str | None
    max_resultats: int
    # Vrai quand la requête et la ville sont connues : la recherche peut partir.
    pret: bool
    # Rempli par la confirmation (clic « Lancer » ou « Annuler »).
    confirme: bool
    # Remplis par l'outil 1 (Apify).
    resultats: list[dict]
    cout_usd: float
    erreur: str | None
    # Rempli par l'outil 3 quand le calcul du toit échoue : les entreprises
    # sont écrites, seul leur potentiel reste à calculer.
    avertissement: str | None
