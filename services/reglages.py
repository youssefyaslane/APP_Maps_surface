"""Réglages partagés : dossier de cache (clé de session, cache des bâtiments)."""

import os

# Racine du projet par défaut, comme lorsque ce réglage vivait dans app.py.
CACHE_DIR = os.environ.get("CACHE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs(CACHE_DIR, exist_ok=True)
