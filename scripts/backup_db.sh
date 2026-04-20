#!/bin/bash
# Daily PostgreSQL backup with 14-day rotation.
# Runs from systemd timer every night at 03:00 MSK.
set -e

BACKUP_DIR="/home/ubuntu/backups"
STAMP=$(date +%F)     # YYYY-MM-DD
FILE="$BACKUP_DIR/skibot_$STAMP.sql.gz"

# Load DB creds from .env
set -a
. /home/ubuntu/ski-mvp-bot/.env
set +a

# Dump + gzip atomically (write to .tmp, then rename)
PGPASSWORD="$PG_PASSWORD" pg_dump \
    -h "${PG_HOST:-localhost}" \
    -p "${PG_PORT:-5432}" \
    -U "${PG_USER:-skibot}" \
    -d "${PG_DB:-skibot}" \
    --clean --if-exists --no-owner --no-acl \
  | gzip -9 > "$FILE.tmp"
mv "$FILE.tmp" "$FILE"
chmod 600 "$FILE"

# Rotation: delete backups older than 14 days
find "$BACKUP_DIR" -maxdepth 1 -name 'skibot_*.sql.gz' -mtime +14 -delete

# Report
SIZE=$(du -h "$FILE" | cut -f1)
COUNT=$(ls -1 "$BACKUP_DIR"/skibot_*.sql.gz 2>/dev/null | wc -l)
echo "[backup] created $FILE ($SIZE), total backups: $COUNT"
