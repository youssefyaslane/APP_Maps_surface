#!/usr/bin/env bash
# Toutes les entreprises : leur toit porte-t-il déjà des panneaux solaires ?
#
#   bash scripts/panneaux/classer_panneaux.sh           # les toits pas encore analysés
#   bash scripts/panneaux/classer_panneaux.sh --tout    # réanalyse tous les toits
#   bash scripts/panneaux/classer_panneaux.sh --detectes  # seulement les toits déjà détectés
#                                                          (après un changement de règle)
#
# Trois programmes reliés par un tuyau ; aucune image n'est écrite sur le disque :
#   1. extraire_toits_panneaux.py   (conteneur de l'application : base + Esri)
#      télécharge l'image satellite de chaque toit, en mémoire ;
#   2. classer_panneaux_yolo.py     (conteneur YOLO coupé d'Internet)
#      répond oui/non avec toutes les confiances, et affiche chaque verdict ;
#   3. importer_panneaux.py         (conteneur de l'application)
#      écrit les verdicts dans la table pv_detections au fil de l'eau, puis
#      affiche le bilan.
# Le tableau de bord les montre dans la colonne « Déjà équipé ? » et le filtre
# « Panneaux ». Interrompue, la commande reprend là où elle s'était arrêtée.
set -euo pipefail

RACINE="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$RACINE"
TOUT=()
case "${1:-}" in --tout|--detectes) TOUT=("$1") ;; esac

source "$RACINE/scripts/panneaux/yolo_conteneur.sh"
yolo_demarrer

APPLI=(docker compose run --rm --no-deps -T -v "$RACINE/scripts/panneaux":/pv:ro web)

echo "• Analyse des toits (le verdict de chaque toit s'affiche ci-dessous)…" >&2
"${APPLI[@]}" python -u /pv/extraire_toits_panneaux.py "${TOUT[@]}" \
  | docker exec -i "${YOLO_EXEC[@]}" python -u /classer.py --flux \
      2> >(grep --line-buffered -vE "WARNING|Ultralytics Settings|yolo settings|docs.ultralytics" >&2) \
  | "${APPLI[@]}" python -u /pv/importer_panneaux.py --flux
