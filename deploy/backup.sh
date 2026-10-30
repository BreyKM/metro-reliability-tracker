#!/usr/bin/env bash
# Dump the metro database, check the dump, and upload it to Backblaze B2.
set -euo pipefail

COMPOSE_FILE=/opt/metro/app/compose.yaml
BACKUP_DIR=/opt/metro/backups
RCLONE_CONFIG=/etc/metro/rclone.conf
REMOTE="b2backup:${B2_BUCKET:?set B2_BUCKET in /etc/metro/backup.env}/daily"
KEEP_LOCAL_DAYS=3
# About 3 weeks of growth at ~0.5 GB/day
MIN_FREE_GB=10

stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
file="$BACKUP_DIR/metro-$stamp.dump"
trap 'rm -f "$file.tmp"' ERR

mkdir -p "$BACKUP_DIR"

docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_dump -U metro_app -d metro --format=custom \
        --exclude-table-data=stop_observation --exclude-table-data=schedule_match \
        > "$file.tmp"

docker compose -f "$COMPOSE_FILE" exec -T postgres \
    pg_restore --list > /dev/null < "$file.tmp"

mv "$file.tmp" "$file"
echo "dumped $(du -h "$file" | cut -f1) to $file"

rclone --config "$RCLONE_CONFIG" copy "$file" "$REMOTE"
echo "uploaded to $REMOTE"

find "$BACKUP_DIR" -name 'metro-*.dump' -mtime +"$KEEP_LOCAL_DAYS" -print -delete

free_gb=$(df --output=avail -BG / | tail -n 1 | tr -dc '0-9')
status=""
if (( free_gb < MIN_FREE_GB )); then
    echo "only ${free_gb}G free on /, under ${MIN_FREE_GB}G" >&2
    status=/fail
else
    echo "${free_gb}G free on /"
fi

if [[ -n "${BACKUP_HEALTHCHECK_URL:-}" ]]; then
    curl -fsS -m 10 --retry 3 --data-raw "${free_gb}G free on /" \
        "$BACKUP_HEALTHCHECK_URL$status" > /dev/null \
        || echo "health check ping failed"
fi