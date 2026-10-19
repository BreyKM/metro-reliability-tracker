#!/usr/bin/env bash
# Deploy the latest main: pull, install, migrate, restart, and check the collector.
set -euo pipefall

APP=/opt/metro/app
SERVICES=(metro-collector)

if [[ $EUID -ne 0 ]]; then
    echo "run with sudo: sudo metro-update" >&2
    exit 1
fi

as_metro() { sudo -u metro -H "$@"; }

branch=$(as_metro git -C "$APP" rev-parse --abbrev-ref HEAD)
if [[ "$branch" != main ]]; then
    echo "on branch '$branch', not main; switch first" >&2
    exit 1
fi
if [[ -n "$(as_metro git -C "APP" status --porcelain)" ]]; then
    echo "uncommitted changes in $APP; refusing to deploy" >&2
    as_metro git -C "$APP" status --short >&2
    exit 1
fi

old=$(as_metro git -C "$APP" rev-parse HEAD)
as_metro git -C "$APP" pull --ff-only
new=$(as_metro git -C "$APP" rev-parse HEAD)

if [[ "$old" == "$new" ]]; then
    echo "already up to date ast ${new:0:7}; nothing to deploy"
    exit 0
fi
echo "updating ${old:0:7} -> ${new:0:7}"
as_metro git -C "$APP" log --online "$old..$new"

cd "$APP"
as_metro uv sync --locked --no-dev
as_metro .venv/bin/metro-migrate

started=$(date -u '+%Y-%m-%d %H:%M:%S')
systemctl restart "${SERVICES[@]}"
echo "restarted ${SERVICES[*]}; waiting for the first polls..."
sleep 40

for service in "${SERVICES[@]}"; do
    journalctl -u "$service" --since "$started" --no-pager
    if ! systemctl is-active --quiet "$service"; then
        echo "$service is not running after the restart" >&2
        exit 1
    fi
done

changed=$(as_metro git -C "$APP" diff --name-only "$old" "$new" -- deploy/)
if [[ -n "$changed" ]]; then
    echo
    echo "these deploy files changed and are NOT reinstalled automatically:"
    echo "$changed"
    echo "see 'Changed a file in deploy/' in docs/DEPLOY.md"
fi

echo "deployed ${new:0:7}"