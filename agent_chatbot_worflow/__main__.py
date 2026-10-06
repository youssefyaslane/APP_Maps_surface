"""Discuter avec le chatbot dans le terminal, sans passer par le site.

    docker compose exec web python -m agent_chatbot_worflow

Ctrl+D ou une ligne vide pour quitter.
"""
import uuid

from agent_chatbot_worflow.graphe import construire_graphe, repondre


def main():
    graphe = construire_graphe()
    thread_id = uuid.uuid4().hex
    print("Que voulez-vous chercher, et où ? (ligne vide pour quitter)")
    while True:
        try:
            message = input("> ").strip()
        except EOFError:
            break
        if not message:
            break
        resultat = repondre(graphe, thread_id, message)
        print(resultat["reponse"])
        if resultat["pret"]:
            print(f"  requêtes : {resultat['requetes']} | ville : {resultat['ville']} | max : {resultat['max_resultats']}")


if __name__ == "__main__":
    main()
