#!/bin/bash
# Daily PostgreSQL backup with 14-day rotation.
# Runs from systemd timer every night at 03:00 MSK.
#
# Usage:
#   backup_db.sh                     # uses default env file / backup dir (prod)
#   ENV_FILE=/path/.env.staging \
#   BACKUP_DIR=/home/ubuntu/backups_staging \
#   BACKUP_PREFIX=skibot_staging \
#   backup_db.sh
set -e

ENV_FILE="${ENV_FILE:-/home/ubuntu/ski-mvp-bot/.env}"
BACKUP_DIR="${BACKUP_DIR:-/home/ubuntu/backups}"
BACKUP_PREFIX="${BACKUP_PREFIX:-skibot}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
STAMP=$(date +%F)     # YYYY-MM-DD
FILE="$BACKUP_DIR/${BACKUP_PREFIX}_${STAMP}.sql.gz"

# Load DB creds from env file
set -a
. "$ENV_FILE"
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

# Rotation: delete backups older than $RETENTION_DAYS
find "$BACKUP_DIR" -maxdepth 1 -name "${BACKUP_PREFIX}_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

# Report
SIZE=$(du -h "$FILE" | cut -f1)
COUNT=$(ls -1 "$BACKUP_DIR"/${BACKUP_PREFIX}_*.sql.gz 2>/dev/null | wc -l)
echo "[backup] created $FILE ($SIZE), total backups: $COUNT"
