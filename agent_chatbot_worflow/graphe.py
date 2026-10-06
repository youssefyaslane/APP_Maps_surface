"""Le graphe LangGraph.

    START → chatbot ─(requête et ville ?)─ non → END (le chatbot a posé sa question)
                     └ oui → confirmation ─(clic ?)─ Annuler → END
                                           └ Lancer → outil_1_apify ─(lieux ?)─ non → bilan
                                                                    └ oui → classifier → outil_2_ecrire → bilan → END

La confirmation arrête le graphe (`interrupt`) jusqu'au clic : Apify est payé,
rien ne part sans l'accord de l'utilisateur. La classification (OpenAI) dit
entreprise ou non ; l'outil 2 n'écrit que les entreprises absentes de la base.
"""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_chatbot_worflow import classification
from agent_chatbot_worflow.chatbot import MAX_RESULTATS, creer_noeud_chatbot
from agent_chatbot_worflow.etat import EtatProspection
from agent_chatbot_worflow.outils import apify, ecriture

def confirmation(etat):
    # Le graphe s'arrête ici ; il reprend avec la réponse du clic (Command(resume=…)).
    lancer = interrupt({
        "requetes": etat["requetes"],
        "ville": etat["ville"],
        "max_par_requete": etat["max_resultats"],
    })
    if lancer is True:
        return {"confirme": True}
    return {
        "confirme": False,
        "pret": False,
        "messages": [AIMessage("Recherche annulée. Dites-moi ce que vous voulez chercher, et où.")],
    }


def creer_noeud_apify(recherche=None):
    """Outil 1. `recherche` se remplace dans les tests, pour tourner sans Apify."""
    recherche = recherche or apify.rechercher

    def outil_1_apify(etat):
        try:
            resultats, cout = recherche(etat["requetes"], etat["ville"])
        except Exception as exc:  # noqa: BLE001 — l'erreur est rendue à l'utilisateur par le bilan
            return {"resultats": [], "cout_usd": 0.0, "erreur": str(exc) or exc.__class__.__name__}
        return {"resultats": resultats, "cout_usd": cout, "erreur": None}

    return outil_1_apify


def creer_noeud_classifier(modele=None):
    """Nœud 3. Une erreur (clé OpenAI refusée…) est rendue par le bilan, et
    rien n'est écrit en base."""
    classer = classification.creer_noeud_classifier(modele)

    def classifier(etat):
        try:
            return classer(etat)
        except Exception as exc:  # noqa: BLE001
            return {"erreur": f"classification impossible ({exc.__class__.__name__})"}

    return classifier


def creer_noeud_ecriture(ecrire=None):
    """Outil 2. `ecrire` se remplace dans les tests, pour tourner sans base."""
    ecrire = ecrire or ecriture.ecrire_nouvelles

    def outil_2_ecrire(etat, config):
        user_id = (config or {}).get("configurable", {}).get("user_id")
        try:
            statuts = ecrire(etat["resultats"], etat["requetes"], etat["ville"], user_id)
        except Exception as exc:  # noqa: BLE001
            return {"erreur": f"écriture en base impossible ({exc.__class__.__name__})"}
        return {"resultats": [{**l, "classe": statuts.get(l["place_id"], l.get("classe"))} for l in etat["resultats"]]}

    return outil_2_ecrire


LIBELLES = (
    (ecriture.AJOUTEE, "nouvelle(s) entreprise(s) ajoutée(s) en base"),
    (ecriture.DEJA_EN_BASE, "déjà en base"),
    (ecriture.DOUBLON, "doublon(s) probable(s) d'une entreprise en base"),
    (classification.ECARTEE, "écartée(s) (pas des entreprises)"),
    (classification.A_VERIFIER, "à vérifier"),
    (classification.ENTREPRISE, "entreprise(s)"),
)


def bilan(etat):
    resultats = etat.get("resultats") or []
    quoi = ", ".join(f"« {r} »" for r in etat["requetes"])
    texte = f"{len(resultats)} lieux trouvés pour {quoi} à {etat['ville']}"
    if etat.get("cout_usd"):
        texte += f" (coût Apify : {etat['cout_usd']:.2f} $)"
    if etat.get("erreur"):
        texte = (f"{texte}. Rien n'a été écrit en base : {etat['erreur']}." if resultats
                 else f"La recherche Apify a échoué : {etat['erreur']}")
    elif resultats:
        comptes = [(sum(1 for r in resultats if r.get("classe") == cle), libelle) for cle, libelle in LIBELLES]
        detail = ", ".join(f"{n} {libelle}" for n, libelle in comptes if n)
        texte += f" : {detail}." if detail else "."
        if any(r.get("classe") == ecriture.AJOUTEE for r in resultats):
            texte += " Leur toit et leur puissance seront calculés par le script habituel."
    else:
        texte += "."
    # La recherche est terminée : une nouvelle demande repart de zéro.
    return {"pret": False, "confirme": False, "messages": [AIMessage(texte)]}


def _apres_outil_1(etat):
    return "classifier" if etat.get("resultats") and not etat.get("erreur") else "bilan"


def _apres_classifier(etat):
    return "bilan" if etat.get("erreur") else "outil_2_ecrire"


def _apres_chatbot(etat):
    return "confirmation" if etat.get("pret") else END


def _apres_confirmation(etat):
    return "outil_1_apify" if etat.get("confirme") else END


def construire_graphe(noeud_chatbot=None, recherche=None, modele_classement=None, ecrire=None, checkpointer=None):
    graphe = StateGraph(EtatProspection)
    graphe.add_node("chatbot", noeud_chatbot or creer_noeud_chatbot())
    graphe.add_node("confirmation", confirmation)
    graphe.add_node("outil_1_apify", creer_noeud_apify(recherche))
    graphe.add_node("classifier", creer_noeud_classifier(modele_classement))
    graphe.add_node("outil_2_ecrire", creer_noeud_ecriture(ecrire))
    graphe.add_node("bilan", bilan)
    graphe.add_edge(START, "chatbot")
    graphe.add_conditional_edges("chatbot", _apres_chatbot, ["confirmation", END])
    graphe.add_conditional_edges("confirmation", _apres_confirmation, ["outil_1_apify", END])
    graphe.add_conditional_edges("outil_1_apify", _apres_outil_1, ["classifier", "bilan"])
    graphe.add_conditional_edges("classifier", _apres_classifier, ["outil_2_ecrire", "bilan"])
    graphe.add_edge("outil_2_ecrire", "bilan")
    graphe.add_edge("bilan", END)
    # La mémoire garde la conversation de chaque thread_id d'un message à
    # l'autre (« usine » puis « à Casablanca »), et l'arrêt sur la
    # confirmation. En mémoire du processus pour l'instant : un redémarrage
    # efface les conversations en cours.
    return graphe.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


def _config(thread_id, user_id=None):
    # user_id part dans le journal (audit_log) avec chaque entreprise écrite.
    return {"configurable": {"thread_id": thread_id, "user_id": user_id}}


def _sortie(etat, avec_resultats=False):
    attente = etat.get("__interrupt__")
    sortie = {
        "reponse": etat["messages"][-1].content,
        "pret": bool(etat.get("pret")),
        "requetes": etat.get("requetes", []),
        "ville": etat.get("ville"),
        "max_resultats": etat.get("max_resultats", MAX_RESULTATS),
        # Présent quand le graphe attend le clic « Lancer » ou « Annuler ».
        "confirmation": attente[0].value if attente else None,
    }
    if avec_resultats:
        sortie["resultats"] = etat.get("resultats") or []
        sortie["cout_usd"] = etat.get("cout_usd") or 0.0
        sortie["erreur"] = etat.get("erreur")
    return sortie


def repondre(graphe, thread_id, message):
    """Envoie un message de l'utilisateur. Un nouveau message alors qu'une
    confirmation est en attente repart du chatbot : l'utilisateur a changé
    de demande."""
    return _sortie(graphe.invoke({"messages": [HumanMessage(message)]}, _config(thread_id)))


def decider(graphe, thread_id, lancer, user_id=None):
    """Réponse au clic : Lancer (True) déclenche la recherche Apify, la
    classification et l'écriture ; Annuler (False) abandonne."""
    etat = graphe.invoke(Command(resume=bool(lancer)), _config(thread_id, user_id))
    return _sortie(etat, avec_resultats=bool(lancer))


def attend_confirmation(graphe, thread_id):
    return "confirmation" in (graphe.get_state(_config(thread_id)).next or ())
