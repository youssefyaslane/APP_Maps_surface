"""Chatbot de recherche (bouton en bas à droite de chaque page) : relaie les
messages vers le workflow agent_chatbot_worflow.

Une conversation par session navigateur ; « Nouvelle conversation » en ouvre
une autre. Quand la requête et la ville sont connues, le graphe attend le
clic « Lancer » ; la recherche Apify, la classification et l'écriture des
entreprises nouvelles (une à trois minutes) tournent alors en arrière-plan et
la page interroge /api/chatbot/etat jusqu'au résultat.
"""
import os
import threading
import uuid

from flask import Blueprint, current_app, jsonify, request, session

bp = Blueprint("chatbot", __name__)

# Assez pour un long message libre : le modèle en extrait la requête et la ville.
LONGUEUR_MESSAGE_MAX = 2000

_graphe = None
_verrou = threading.Lock()
# Recherches Apify en cours ou finies, par conversation : {"statut", ...sortie}.
# En mémoire du processus, comme les conversations.
_taches = {}


def _obtenir_graphe():
    """Graphe construit au premier message : langgraph, OpenAI et Apify ne
    sont pas chargés au démarrage de l'application."""
    global _graphe
    with _verrou:
        if _graphe is None:
            from agent_chatbot_worflow.graphe import construire_graphe

            _graphe = construire_graphe()
        return _graphe


def _thread_id():
    # Préfixé par le compte : deux utilisateurs ne partagent jamais une
    # conversation, même avec un identifiant de session identique.
    if "chatbot_thread" not in session:
        session["chatbot_thread"] = uuid.uuid4().hex
    return f"{session.get('user_id')}:{session['chatbot_thread']}"


def _cle_refusee(exc):
    """Erreur 401 d'OpenAI (clé expirée ou révoquée), reconnue par le nom de
    sa classe : openai et langchain-openai en ont chacun une."""
    return any(c.__name__.endswith("AuthenticationError") for c in type(exc).__mro__)


def _recherche_en_cours(thread_id):
    return _taches.get(thread_id, {}).get("statut") == "en_cours"


@bp.route("/api/chatbot", methods=["POST"])
def api_chatbot():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Message vide"}), 400
    if len(message) > LONGUEUR_MESSAGE_MAX:
        return jsonify({"error": f"Message trop long ({LONGUEUR_MESSAGE_MAX} caractères au plus)"}), 400
    if not os.environ.get("OPENAI_API_KEY"):
        return jsonify({"error": "Chatbot indisponible : la clé OpenAI (OPENAI_API_KEY) n'est pas configurée."}), 503
    thread_id = _thread_id()
    if _recherche_en_cours(thread_id):
        return jsonify({"error": "Une recherche Apify est en cours : attendez son résultat."}), 409
    try:
        from agent_chatbot_worflow.graphe import repondre

        resultat = repondre(_obtenir_graphe(), thread_id, message)
    except Exception as exc:
        if _cle_refusee(exc):
            return jsonify({"error": "Chatbot indisponible : la clé OpenAI est refusée (expirée ou invalide). "
                                     "Créer une nouvelle clé et la mettre dans .env (OPENAI_API_KEY)."}), 503
        current_app.logger.exception("Chatbot : échec de la réponse")
        return jsonify({"error": "Le chatbot n'a pas pu répondre. Réessayez dans un instant."}), 502
    return jsonify(resultat)


def _executer_recherche(app, graphe, thread_id, user_id):
    """Reprend le graphe après le clic « Lancer » : recherche Apify,
    classification, écriture des entreprises nouvelles, bilan."""
    from agent_chatbot_worflow.graphe import decider

    try:
        _taches[thread_id] = {"statut": "fini", **decider(graphe, thread_id, True, user_id)}
    except Exception:
        with app.app_context():
            current_app.logger.exception("Chatbot : échec de la recherche Apify")
        _taches[thread_id] = {"statut": "erreur", "error": "La recherche a échoué. Réessayez dans un instant."}


def _demarrer(cible, *args):
    """Lance la recherche en arrière-plan (remplacé dans les tests)."""
    threading.Thread(target=cible, args=args, daemon=True).start()


@bp.route("/api/chatbot/lancer", methods=["POST"])
def api_chatbot_lancer():
    data = request.get_json(silent=True) or {}
    lancer = data.get("lancer")
    if not isinstance(lancer, bool):
        return jsonify({"error": "Choix attendu : lancer vrai ou faux"}), 400
    thread_id = _thread_id()
    from agent_chatbot_worflow.graphe import attend_confirmation, decider

    graphe = _obtenir_graphe()
    with _verrou:
        if _recherche_en_cours(thread_id):
            return jsonify({"error": "Cette recherche est déjà lancée."}), 409
        if not attend_confirmation(graphe, thread_id):
            return jsonify({"error": "Aucune recherche à confirmer : décrivez d'abord ce que vous cherchez."}), 409
        if lancer:
            if not os.environ.get("APIFY_API_TOKEN"):
                return jsonify({"error": "Recherche impossible : le jeton Apify (APIFY_API_TOKEN) n'est pas configuré."}), 503
            _taches[thread_id] = {"statut": "en_cours"}
    if not lancer:
        return jsonify({"statut": "annule", **decider(graphe, thread_id, False)})
    _demarrer(_executer_recherche, current_app._get_current_object(), graphe, thread_id, session.get("user_id"))
    return jsonify({"statut": "en_cours"})


@bp.route("/api/chatbot/etat")
def api_chatbot_etat():
    return jsonify(_taches.get(_thread_id(), {"statut": "aucune"}))


@bp.route("/api/chatbot/nouveau", methods=["POST"])
def api_chatbot_nouveau():
    if _recherche_en_cours(_thread_id()):
        return jsonify({"error": "Une recherche Apify est en cours : attendez son résultat."}), 409
    session["chatbot_thread"] = uuid.uuid4().hex
    return jsonify({"ok": True})
