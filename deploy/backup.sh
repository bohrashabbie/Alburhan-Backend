#!/bin/sh
# Nightly database backup: a compressed pg_dump, kept 14 days locally and (optionally) copied to S3.
#   crontab -e  →  30 1 * * * /opt/alburhan/Alburhan-Backend/deploy/backup.sh >> /var/log/alburhan-backup.log 2>&1
set -eu
cd "$(dirname "$0")"
DATABASE_URL=$(grep '^DATABASE_URL=' api.env | cut -d= -f2-)
TARGET=${BACKUP_DIR:-/var/backups/alburhan}
STAMP=$(date +%Y-%m-%d_%H%M)
mkdir -p "$TARGET"
docker run --rm --network host postgres:17-alpine \
  pg_dump --format=custom --no-owner "$DATABASE_URL" > "$TARGET/alburhan_erp_$STAMP.dump"
find "$TARGET" -name 'alburhan_erp_*.dump' -mtime +14 -delete
if [ -n "${BACKUP_S3_URI:-}" ]; then
  aws s3 cp "$TARGET/alburhan_erp_$STAMP.dump" "$BACKUP_S3_URI/alburhan_erp_$STAMP.dump" --only-show-errors
fi
echo "$(date -Is) backup ok: alburhan_erp_$STAMP.dump"
