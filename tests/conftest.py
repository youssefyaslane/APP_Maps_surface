"""Réglages communs aux tests.

`app.py` précharge les villes et le modèle IA au moment de l'import, sauf quand
`WERKZEUG_RUN_MAIN` vaut « true » — le drapeau que Flask pose dans le processus
de rechargement. Les scripts hors ligne s'en servent déjà pour importer le
module sans réveiller ses tâches de fond ; les tests font de même, ce qui leur
permet de tourner sans base ni réseau.
"""
import os
import sys

os.environ.setdefault("WERKZEUG_RUN_MAIN", "true")

# Les tests sont lancés depuis la racine du projet, mais pytest n'y ajoute pas
# forcément le dossier courant au chemin d'import selon la version.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
