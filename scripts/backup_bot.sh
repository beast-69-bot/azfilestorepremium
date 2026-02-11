#!/usr/bin/env bash
set -euo pipefail

# Backup bot data for VPS migration.
# Creates a timestamped tar.gz containing:
# - data/bot.db (SQLite)
# - .env

PROJECT_DIR="${PROJECT_DIR:-$HOME/azfilestorepremium}"
SERVICE_NAME="${SERVICE_NAME:-azfilestorepremium}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/bot_backups}"
TS="$(date +%Y%m%d_%H%M%S)"
OUT_FILE="$BACKUP_DIR/azfilestorepremium_backup_${TS}.tar.gz"

mkdir -p "$BACKUP_DIR"

echo "[1/4] Stopping service: $SERVICE_NAME"
sudo systemctl stop "$SERVICE_NAME"

echo "[2/4] Validating required files"
if [[ ! -f "$PROJECT_DIR/data/bot.db" ]]; then
  echo "ERROR: Missing $PROJECT_DIR/data/bot.db"
  sudo systemctl start "$SERVICE_NAME" || true
  exit 1
fi
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  echo "ERROR: Missing $PROJECT_DIR/.env"
  sudo systemctl start "$SERVICE_NAME" || true
  exit 1
fi

echo "[3/4] Creating backup archive"
tar -C "$PROJECT_DIR" -czf "$OUT_FILE" data/bot.db .env

echo "[4/4] Starting service: $SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"

echo "Backup created: $OUT_FILE"

