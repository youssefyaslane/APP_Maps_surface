#!/usr/bin/env bash
# Sauvegarde la base PostgreSQL du projet vers backups/, compressée, datée.
#
# S'exécute sur l'hôte (pas dans un conteneur) : il faut invoquer `docker
# compose`, et le conteneur web n'a pas le client pg_dump installé. Le
# conteneur db, lui, l'a — c'est l'image officielle postgres.
#
# Usage : ./scripts/backup_db.sh
# Pensé pour tourner sans surveillance via une tâche planifiée (cron) ; ne rien
# écrire sur stdout en cas de succès serait plus discret, mais une ligne par
# nuit dans le journal permet de vérifier d'un coup d'œil que ça tourne encore.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

BACKUP_DIR="$PROJECT_DIR/backups"
RETENTION_DAYS=14

mkdir -p "$BACKUP_DIR"

TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
DEST="$BACKUP_DIR/maps_${TIMESTAMP}.sql.gz"
TMP_DEST="${DEST}.tmp"

# Écrit dans un fichier temporaire puis renomme : un pg_dump interrompu en
# cours de route (disque plein, conteneur arrêté) ne doit pas laisser un
# fichier .sql.gz tronqué qui se ferait passer pour une sauvegarde valide.
docker compose exec -T db pg_dump -U maps -d maps | gzip > "$TMP_DEST"
mv "$TMP_DEST" "$DEST"

SIZE="$(du -h "$DEST" | cut -f1)"
echo "$(date '+%Y-%m-%d %H:%M:%S') — sauvegarde écrite : $DEST ($SIZE)"

# Purge des sauvegardes de plus de RETENTION_DAYS jours.
DELETED=$(find "$BACKUP_DIR" -name 'maps_*.sql.gz' -mtime +"$RETENTION_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') — $DELETED sauvegarde(s) de plus de ${RETENTION_DAYS} jours supprimée(s)"
fi
