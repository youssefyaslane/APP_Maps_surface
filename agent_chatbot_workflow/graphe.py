"""Le graphe LangGraph.

    START → chatbot ─(requête et ville ?)─ non → END (le chatbot a posé sa question)
                     └ oui → confirmation ─(clic ?)─ Annuler → END
                                           └ Lancer → outil_1_apify ─(lieux ?)─ non → bilan
                                                                    └ oui → classifier → outil_2_ecrire ─┐
                                              (entreprises ajoutées ?) oui → outil_3_potentiel → bilan → END
                                                                       non → bilan → END

La confirmation arrête le graphe (`interrupt`) jusqu'au clic : Apify est payé,
rien ne part sans l'accord de l'utilisateur. La classification (OpenAI) dit
entreprise ou non ; l'outil 2 n'écrit que les entreprises absentes de la base ;
l'outil 3 calcule aussitôt leur toit, leur puissance, leur production et le
CO₂ évité.
"""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent_chatbot_workflow import classification
from agent_chatbot_workflow.chatbot import MAX_RESULTATS, creer_noeud_chatbot
from agent_chatbot_workflow.etat import EtatProspection
from agent_chatbot_workflow.outils import apify, ecriture, potentiel

APERCU = 3   # prospects cités dans le bilan, par puissance décroissante

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
        resultats = []
        for lieu in etat["resultats"]:
            if lieu["place_id"] in statuts:
                classe, raison = statuts[lieu["place_id"]]
                lieu = {**lieu, "classe": classe, "raison": raison or lieu.get("raison")}
            resultats.append(lieu)
        return {"resultats": resultats}

    return outil_2_ecrire


def creer_noeud_potentiel(calculer=None):
    """Outil 3. `calculer` se remplace dans les tests, pour tourner sans base
    ni réseau. Un échec (Overpass, PVGIS injoignables) n'annule rien : les
    entreprises sont écrites, le calcul habituel les rattrapera."""
    calculer = calculer or potentiel.calculer

    def outil_3_potentiel(etat):
        ajoutees = [l["place_id"] for l in etat["resultats"] if l.get("classe") == ecriture.AJOUTEE]
        try:
            calcul = calculer(ajoutees)
        except Exception as exc:  # noqa: BLE001
            return {"avertissement": f"calcul du toit impossible pour l'instant ({exc.__class__.__name__})"}
        return {"resultats": [{**l, **calcul.get(l["place_id"], {})} for l in etat["resultats"]]}

    return outil_3_potentiel


def _fr(n):
    return f"{n:,.0f}".replace(",", "\u202f")


def resume_potentiel(resultats):
    """Phrase du bilan sur le potentiel des entreprises ajoutées, ou "" sans calcul."""
    ajoutees = [r for r in resultats if r.get("classe") == ecriture.AJOUTEE and "kwc" in r]
    if not ajoutees:
        return ""
    avec_toit = sorted((r for r in ajoutees if r["kwc"]), key=lambda r: -r["kwc"])
    kwc = sum(r["kwc"] for r in avec_toit)
    mwh = sum(r.get("production_mwh") or 0 for r in avec_toit)
    co2 = sum(r.get("co2_t") or 0 for r in avec_toit)
    if not avec_toit:
        return " Aucun toit n'a été trouvé sous les nouvelles entreprises : à tracer sur la carte."
    texte = f" Potentiel des nouvelles entreprises : {_fr(kwc)} kWc"
    if mwh:
        texte += f", {_fr(mwh)} MWh par an, {_fr(co2)} t de CO₂ évitées par an"
    texte += ". À appeler en premier : " + ", ".join(
        f"{r['nom']} ({_fr(r['kwc'])} kWc)" for r in avec_toit[:APERCU]) + "."
    sans = len(ajoutees) - len(avec_toit)
    if sans:
        texte += f" {sans} sans toit trouvé, à tracer sur la carte."
    return texte


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
        if etat.get("avertissement"):
            texte += f" Entreprises bien écrites, mais {etat['avertissement']} : lancer le calcul habituel."
        else:
            texte += resume_potentiel(resultats)
    else:
        texte += "."
    # La recherche est terminée : une nouvelle demande repart de zéro.
    return {"pret": False, "confirme": False, "avertissement": None, "messages": [AIMessage(texte)]}


def _apres_outil_1(etat):
    return "classifier" if etat.get("resultats") and not etat.get("erreur") else "bilan"


def _apres_ecriture(etat):
    if etat.get("erreur"):
        return "bilan"
    ajoutees = any(r.get("classe") == ecriture.AJOUTEE for r in etat.get("resultats") or [])
    return "outil_3_potentiel" if ajoutees else "bilan"


def _apres_classifier(etat):
    return "bilan" if etat.get("erreur") else "outil_2_ecrire"


def _apres_chatbot(etat):
    return "confirmation" if etat.get("pret") else END


def _apres_confirmation(etat):
    return "outil_1_apify" if etat.get("confirme") else END


def construire_graphe(noeud_chatbot=None, recherche=None, modele_classement=None, ecrire=None,
                      calculer=None, checkpointer=None):
    graphe = StateGraph(EtatProspection)
    graphe.add_node("chatbot", noeud_chatbot or creer_noeud_chatbot())
    graphe.add_node("confirmation", confirmation)
    graphe.add_node("outil_1_apify", creer_noeud_apify(recherche))
    graphe.add_node("classifier", creer_noeud_classifier(modele_classement))
    graphe.add_node("outil_2_ecrire", creer_noeud_ecriture(ecrire))
    graphe.add_node("outil_3_potentiel", creer_noeud_potentiel(calculer))
    graphe.add_node("bilan", bilan)
    graphe.add_edge(START, "chatbot")
    graphe.add_conditional_edges("chatbot", _apres_chatbot, ["confirmation", END])
    graphe.add_conditional_edges("confirmation", _apres_confirmation, ["outil_1_apify", END])
    graphe.add_conditional_edges("outil_1_apify", _apres_outil_1, ["classifier", "bilan"])
    graphe.add_conditional_edges("classifier", _apres_classifier, ["outil_2_ecrire", "bilan"])
    graphe.add_conditional_edges("outil_2_ecrire", _apres_ecriture, ["outil_3_potentiel", "bilan"])
    graphe.add_edge("outil_3_potentiel", "bilan")
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
