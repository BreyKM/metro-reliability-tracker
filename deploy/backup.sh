#!/usr/bin/env bash
# Dump the metro database, check the dump, and upload it to Backblaze B2.
set -euo pipefail

COMPOSE_FILE=/opt/metro/app/compose.yaml
BACKUP_DIR=/opt/metro/backups
RCLONE_CONFIG=/etc/metro/rclone.conf
REMOTE="b2backup:${B2_BUCKET:?set B2_BUCKET in /etc/metro/backup.env}/daily"
KEEP_LOCAL_DAYS=3

stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
file="$BACKUP_DIR/metro-$stamp.dump"
trap 'rm -f "$file.tmp"' ERR

mkdir -p "$BACKUP_DIR"

docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -U metro_app -d metro --format=custom > "$file.tmp"

docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_restore --list > /dev/null < "$file.tmp"

mv "$file.tmp" "$file"
echo "dumped $(du -h "$file" | cut -f1) to $file"

rclone --config "$RCLONE_CONFIG" copy "$file" "$REMOTE"
echo "uploaded to $REMOTE"

find "$BACKUP_DIR" -name 'metro-*.dump' -mtime +"$KEEP_LOCAL_DAYS" -print -delete

if [[ -n "${BACKUP_HEALTHCHECK_URL:-}" ]]; then
    curl -fsS -m 10 --retry 3 "$BACKUP_HEALTHCHECK_URL" > /dev/null \
        || echo "health check ping failed"
fi