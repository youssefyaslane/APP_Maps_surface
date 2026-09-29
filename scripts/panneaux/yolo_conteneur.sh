# Conteneur jetable pour le modèle YOLO « panneaux solaires ». À sourcer :
#   source scripts/panneaux/yolo_conteneur.sh
#   yolo_demarrer [dossier_images]      # nom du conteneur dans $YOLO_NOM
#   docker exec -i "${YOLO_EXEC[@]}" python -u /classer.py ...
#
# Utilisé par scripts/panneaux/classer_panneaux.sh et tests/panneaux/essai_yolo.sh.
#
# Rien ne s'installe sur la machine ni dans l'application : le conteneur est
# tiré de l'image de l'application (qui a déjà PyTorch) et reçoit YOLO. Il est
# coupé d'Internet AVANT qu'on y dépose le modèle (un fichier .pt peut exécuter
# du code à l'ouverture), n'écrit rien sur le disque de la machine, et il est
# supprimé à la sortie du script, même en cas d'erreur.

YOLO_RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
YOLO_MODELE="$YOLO_RACINE/models/ign_solar/yolo26l_solar_panel-s4.pt"   # models/ est exclu de git
YOLO_URL="https://huggingface.co/Cyrille37/solar-panels-IGN-bdortho/resolve/main/yolo26l_solar_panel-s4.pt"
YOLO_IMAGE_BASE="maps_surfface-web"
YOLO_NOM="yolo-panneaux-$$"
# Lancé avec votre utilisateur plutôt que root.
YOLO_EXEC=(-u "$(id -u):$(id -g)" "$YOLO_NOM")

yolo_nettoyer() { docker rm -f "$YOLO_NOM" >/dev/null 2>&1 || true; }

yolo_demarrer() {
  local images="${1:-}" journal
  trap yolo_nettoyer EXIT
  if ! docker image inspect "$YOLO_IMAGE_BASE" >/dev/null 2>&1; then
    echo "Image Docker $YOLO_IMAGE_BASE introuvable : construisez d'abord l'application (docker compose build)." >&2
    return 1
  fi
  if [ ! -s "$YOLO_MODELE" ]; then
    echo "• Téléchargement du modèle (53 Mo, une seule fois)…" >&2
    mkdir -p "$(dirname "$YOLO_MODELE")"
    curl -fL --progress-bar -o "$YOLO_MODELE.part" "$YOLO_URL" && mv "$YOLO_MODELE.part" "$YOLO_MODELE"
  fi

  echo "• Conteneur YOLO jetable, installation (environ 1 min)…" >&2
  local volume=()
  [ -n "$images" ] && volume=(-v "$(cd "$images" && pwd)":/images:ro)
  docker run -d --name "$YOLO_NOM" "${volume[@]}" \
    -e YOLO_CONFIG_DIR=/tmp -e YOLO_OFFLINE=1 -e YOLO_VERBOSE=False -e HOME=/tmp \
    -e SEUIL="${SEUIL:-0.25}" -e CHIP="${CHIP:-960}" \
    "$YOLO_IMAGE_BASE" sleep infinity >/dev/null
  journal="$(mktemp)"
  if ! docker exec "$YOLO_NOM" pip install --no-cache-dir -q ultralytics >"$journal" 2>&1; then
    echo "Échec de l'installation de YOLO :" >&2
    tail -20 "$journal" >&2
    rm -f "$journal"
    return 1
  fi
  rm -f "$journal"

  echo "• Coupure d'Internet, puis dépôt du modèle." >&2
  docker network disconnect bridge "$YOLO_NOM"
  docker cp "$YOLO_MODELE" "$YOLO_NOM":/modele.pt
  docker cp "$YOLO_RACINE/scripts/panneaux/classer_panneaux_yolo.py" "$YOLO_NOM":/classer.py
}
