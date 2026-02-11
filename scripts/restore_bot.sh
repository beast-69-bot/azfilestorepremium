#!/usr/bin/env bash
set -euo pipefail

# Restore bot data from backup archive.
# Usage:
#   ./scripts/restore_bot.sh /path/to/azfilestorepremium_backup_YYYYmmdd_HHMMSS.tar.gz

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backup.tar.gz>"
  exit 1
fi

BACKUP_FILE="$1"
PROJECT_DIR="${PROJECT_DIR:-$HOME/azfilestorepremium}"
SERVICE_NAME="${SERVICE_NAME:-azfilestorepremium}"

if [[ ! -f "$BACKUP_FILE" ]]; then
  echo "ERROR: Backup file not found: $BACKUP_FILE"
  exit 1
fi

mkdir -p "$PROJECT_DIR/data"

echo "[1/5] Stopping service: $SERVICE_NAME"
sudo systemctl stop "$SERVICE_NAME"

echo "[2/5] Extracting backup archive"
tar -C "$PROJECT_DIR" -xzf "$BACKUP_FILE"

echo "[3/5] Setting permissions"
chmod 600 "$PROJECT_DIR/.env" || true

echo "[4/5] Starting service: $SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"

echo "[5/5] Service status"
sudo systemctl --no-pager --full status "$SERVICE_NAME" | head -n 20 || true

echo "Restore complete from: $BACKUP_FILE"

