#!/usr/bin/env bash
# Essai du modèle YOLO sur les images de tests/panneaux/img (rien n'est écrit).
#   bash tests/panneaux/essai_yolo.sh
# Mettez « avec_panneaux » ou « sans_panneaux » dans le nom d'une image pour que
# la réponse soit notée juste ou fausse.
set -euo pipefail
ICI="$(cd "$(dirname "$0")" && pwd)"
source "$ICI/../../scripts/yolo_conteneur.sh"
yolo_demarrer "$ICI/img"
echo
docker exec "${YOLO_EXEC[@]}" python -u /classer.py /images 2>&1 \
  | { grep -vE "WARNING|Ultralytics Settings|yolo settings|docs.ultralytics" || true; }
