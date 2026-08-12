#!/usr/bin/env bash
set -euo pipefail

ROOT=/opt/moneymore/current
BACKUP_ROOT=/opt/moneymore/backups
STAMP=$(date +%Y%m%d_%H%M%S)
TARGET="$BACKUP_ROOT/$STAMP"

mkdir -p "$TARGET"
cp -a "$ROOT/state" "$TARGET/state"
cp -a "$ROOT/data/processed" "$TARGET/processed"
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf -- {} +
