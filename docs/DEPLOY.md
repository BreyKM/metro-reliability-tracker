# Deploying and operating the collector

How the production server is set up and run. Following this top to bottom on a fresh Ubuntu 24.04 VPS rebuilds everything. The reasons behind each choice are in [DECISIONS.md](../DECISIONS.md) (#13, #14, #19–#21).

## What runs where

| Piece | How it runs | Location |
|---|---|---|
| Collector | systemd service `metro-collector`, as user `metro` | `/opt/metro/app` |
| Postgres 18 | Docker Compose, named volume `pgdata`, bound to `127.0.0.1:5432` | `/opt/metro/app/compose.yaml` |
| Raw snapshots | written by the collector, gzipped, one folder per UTC day | `/opt/metro/data/raw` |
| Raw cleanup | systemd timer `metro-raw-cleanup`, daily 09:00 UTC, keeps ~8 days | `deploy/metro-raw-cleanup.*` |
| Backups | systemd timer `metro-backup`, daily 08:00 UTC, `pg_dump` → Backblaze B2 | `/usr/local/sbin/metro-backup` |
| Secrets | app: `/opt/metro/app/.env` (metro, 600). Backups: `/etc/metro/` (root, 600) | never in the repo |

Server: OVHcloud VPS-2 (4 vCore, 8 GB RAM, 75 GB), Ubuntu 24.04 LTS, US data center.

## 1. Server setup

Create the VPS with your SSH public key (`ssh-ed25519 ...`). OVH's image creates a `ubuntu` user with an expired password; get the current one from OVH's install email and change it on first login.

```bash
sudo apt update && sudo apt upgrade -y
sudo adduser ADMIN_USER
sudo usermod -aG sudo ADMIN_USER
sudo rsync --archive --chown=ADMIN_USER:ADMIN_USER /home/ubuntu/.ssh /home/ADMIN_USER
```

Log in as ADMIN_USER in a **new** window and confirm `sudo whoami` prints `root` before continuing.

`/etc/ssh/sshd_config.d/10-hardening.conf` (read before OVH's `50-cloud-init.conf`, which enables passwords):

```
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowUsers ADMIN_USER
```

```bash
sudo sshd -t
sudo systemctl restart ssh
sudo sshd -T | grep -Ei 'permitrootlogin|passwordauthentication|kbdinteractiveauthentication|allowusers'
sudo usermod --lock --expiredate 1 ubuntu
sudo ufw allow OpenSSH
sudo ufw enable
```

Check from another machine that `ssh ubuntu@SERVER_IP` and `ssh root@SERVER_IP` are refused.

## 2. Docker and Postgres

Install Docker Engine from Docker's apt repository: https://docs.docker.com/engine/install/ubuntu/. Don't add users to the `docker` group (it's equivalent to root); use `sudo docker`.

`/etc/docker/daemon.json` (no trailing commas; check with `sudo dockerd --validate --config-file=/etc/docker/daemon.json`):

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
```

```bash
sudo systemctl restart docker
sudo adduser --system --group --home /opt/metro metro
sudo usermod -aG metro ADMIN_USER    # log out and back in
sudo -u metro git clone https://github.com/GITHUB_USER/metro-reliability-tracker.git /opt/metro/app
```

`/opt/metro/app/.env` (owner metro, mode 600; generate the password with `openssl rand -hex 32`):

```
POSTGRES_PASSWORD=...
DATABASE_URL=postgresql://metro_app:...@127.0.0.1:5432/metro
METRO_GTFSRT_KEY=...
METRO_TRANSIT_DATA_KEY=...
RAW_DIR=/opt/metro/data/raw
```

```bash
cd /opt/metro/app
sudo docker compose config          # check; output includes the password
sudo docker compose up -d
sudo docker compose ps              # expect (healthy)
sudo ss -tlnp | grep 5432           # expect 127.0.0.1:5432 only
```

From another machine, a TCP connection to `SERVER_IP:5432` must fail. Docker's published ports bypass ufw, so the `127.0.0.1` binding in `compose.yaml` is the only thing keeping Postgres private.

## 3. Install the collector

```bash
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
sudo env UV_UNMANAGED_INSTALL=/usr/local/bin sh /tmp/uv-install.sh
cd /opt/metro/app
sudo -u metro -H uv sync --locked --no-dev
sudo -u metro -H .venv/bin/metro-migrate
```

Run git in `/opt/metro/app` as `metro` (`sudo -u metro git ...`). As any other user, git refuses with "dubious ownership."

## 4. Start the collector

```bash
sudo cp deploy/metro-collector.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/metro-collector.service
sudo systemctl daemon-reload
sudo systemctl enable --now metro-collector
sudo journalctl -u metro-collector -f
```

Expect a `recorded ... rows in N ms` line every 30 seconds.

## 5. Raw snapshot cleanup

```bash
sudo cp deploy/metro-raw-cleanup.service deploy/metro-raw-cleanup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start metro-raw-cleanup.service     # run once and check the journal
sudo systemctl enable --now metro-raw-cleanup.timer
```

## 6. Backups

Backblaze B2: a private bucket with a lifecycle rule (hide 30 days after upload, delete 1 day later) and an application key limited to that bucket.

```bash
sudo apt install rclone
sudo install -d -m 700 /etc/metro
```

`/etc/metro/rclone.conf` (mode 600):

```ini
[b2backup]
type = b2
account = KEY_ID
key = APPLICATION_KEY
```

`/etc/metro/backup.env` (mode 600):

```
B2_BUCKET=B2_BUCKET
```

```bash
sudo rclone --config /etc/metro/rclone.conf lsd b2backup:      # should list only the bucket
sudo install -m 750 -o root -g root deploy/backup.sh /usr/local/sbin/metro-backup
sudo cp deploy/metro-backup.service deploy/metro-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start metro-backup.service
sudo journalctl -u metro-backup.service -n 10 --no-pager
sudo systemctl enable --now metro-backup.timer
systemctl list-timers 'metro-*'
```

The script runs as root from a root-owned copy, not from the repo, because the repo is writable by `metro`.

## 7. Monitoring

healthchecks.io has two checks:
- `metro-collector`: period 1 minute, grace 10 minutes. The collector pings it after recording a new snapshot (at most once a minute).
- `metro-backup`: period 1 day, grace 2 hours. `backup.sh` pings it after a successful upload.

Add the ping URLs (secret; anyone with them can ping):
- `HEALTHCHECK_URL=...` in `/opt/metro/app/.env`, then `sudo systemctl restart metro-collector`
- `BACKUP_HEALTHCHECK_URL=...` in `/etc/metro/backup.env`, then reinstall the script (`sudo install -m 750 -o root -g root deploy/backup.sh /usr/local/sbin/metro-backup`)

The collector logs `health check pings on` at startup when the URL is set.

## Routine operations

### Deploy a code change

```bash
cd /opt/metro/app
sudo -u metro git pull
sudo -u metro -H uv sync --locked --no-dev
sudo -u metro -H .venv/bin/metro-migrate
sudo systemctl restart metro-collector
sudo journalctl -u metro-collector -n 20 --no-pager
```

A restart causes a short gap (usually no missed poll).

### Changed a file in `deploy/`

Unit files and the backup script are **copied**, so `git pull` alone doesn't change what runs:

```bash
sudo cp deploy/<unit> /etc/systemd/system/ && sudo systemctl daemon-reload
sudo install -m 750 -o root -g root deploy/backup.sh /usr/local/sbin/metro-backup
```

### Health checks

```bash
systemctl status metro-collector
systemctl list-timers 'metro-*'
df -h /
sudo du -sh /opt/metro/data/raw /opt/metro/backups
```

Gaps (more than one missed poll), in psql (`sudo docker compose exec postgres psql -U metro_app -d metro`):

```sql
SELECT requested_at AS resumed_at, gap
FROM (
    SELECT requested_at, requested_at - lag(requested_at) OVER (ORDER BY requested_at) AS gap
    FROM poll WHERE feed = 'trip_updates'
) gaps
WHERE gap > interval '35 seconds'
ORDER BY requested_at;
```

Recent failures:

```sql
SELECT requested_at, http_status, error
FROM poll WHERE error IS NOT NULL
ORDER BY requested_at DESC LIMIT 20;
```

## Restore from backup

### Test a backup (do this occasionally)

```bash
cd /opt/metro/app
sudo rclone --config /etc/metro/rclone.conf copy b2backup:B2_BUCKET/daily /tmp/restore-test --max-age 2d
sudo docker compose exec postgres createdb -U metro_app metro_restore_test
sudo docker compose exec -T postgres pg_restore -U metro_app -d metro_restore_test --no-owner < /tmp/restore-test/metro-YYYY-MM-DDTHHMMSSZ.dump.dump
```

Compare `count(*)` of `poll` and `stop_prediction` with the live database, then:

```bash
sudo docker compose exec postgres dropdb -U metro_app metro_restore_test
sudo rm -rf /tmp/restore-test
```

### Rebuild onto a new server

> **Only on a new server with an empty database.** Never run these steps on the live server. To check a backup there, use "Test a backup" above.

1. Sections 1–3, but **skip `metro-migrate`**.
2. Set up rclone as in section 6 (install it and create `/etc/metro/rclone.conf` and `backup.env`), then download the newest dump:
   ```bash
   sudo rclone --config /etc/metro/rclone.conf copy b2backup:B2_BUCKET/daily /tmp/restore --max-age 2d
   ```
3. Restore it into the empty `metro` database:
   ```bash
   sudo docker compose exec -T postgres pg_restore -U metro_app -d metro --no-owner < /tmp/restore/metro-YYYY-MM-DDTHHMMSSZ.dump
   ```
4. Run `metro-migrate` to apply any migrations newer than the backup.
5. Sections 4–6 (skipping the rclone setup already done).

Data between the last backup and the rebuild is lost and shows as a gap in `poll`.

## Things that bit me

- Docker-published ports bypass ufw. Bind to `127.0.0.1`.
- `postgres:18` stores data under `/var/lib/postgresql`. Older examples use `/var/lib/postgresql/data`, which doesn't work with 18.
- OVH's `50-cloud-init.conf` turns password login on. A `10-` file overrides it, because sshd uses the first value it reads.
- JSON doesn't allow trailing commas (`daemon.json`).
- Check configs before restarting: `sshd -t`, `dockerd --validate`, `systemd-analyze verify`, `docker compose config`. None of these check the arguments to the command in `ExecStart`, so run new services by hand once.