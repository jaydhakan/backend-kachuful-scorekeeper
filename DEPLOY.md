# Deploying the Kachuful API

A runbook for Jay. There is **one environment, production**, on the VPS that already serves
`play.vectanix.com` (Ubuntu 24.04). Follow it top to bottom once; after that, every push to `main`
deploys by itself. Run the commands from your checkout of this repo on the VPS
(`~/projects/personal/backend-kachuful-scorekeeper`), as your own sudo user.

| | |
| --- | --- |
| URL | `https://play.vectanix.com/api` (same origin as the app) |
| API port (local only) | 8000 |
| systemd unit | `kachuful-api@production` |
| Code | `/srv/kachuful/production/current` (a link to `releases/<sha>`) |
| Settings | `/srv/kachuful/production/shared/env` |
| Database | `kachuful_production` on the local Postgres 16 |
| Deploys | every push to `main`, after CI passes (or Actions → CD → Run workflow) |

Nothing changes in Cloudflare: the API lives under `play.vectanix.com/api`, which already points here.
Do **not** turn on Rocket Loader or any HTML rewriting for `/r/*` and `/l/*`. If the record is
proxied, SSE still works: the live stream pings every 25 s, and Cloudflare closes idle connections
only after 100 s.

---

## 1. Packages and the deploy user (once)

```sh
sudo apt update
sudo apt install -y postgresql-16 nginx rclone certbot python3-certbot-nginx
id deploy || sudo adduser --disabled-password --gecos "" deploy
sudo -iu deploy sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -iu deploy /home/deploy/.local/bin/uv --version
pg_lsclusters                                   # expect: 16 main 5432 online
```

Write `/home/deploy/...` in full: `~` would expand to *your* home before `sudo` runs.
`citext` and `pg_trgm` ship with `postgresql-16`.

## 2. Database, folders and settings (once)

Run this as one block. It makes a random database password, puts it straight into the env file and
never prints it:

```sh
PGPASS=$(openssl rand -hex 24)
sudo -u postgres psql -v ON_ERROR_STOP=1 -v pw="$PGPASS" <<'SQL'
CREATE ROLE kachuful_production LOGIN PASSWORD :'pw';
CREATE DATABASE kachuful_production OWNER kachuful_production;
\c kachuful_production
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL

sudo mkdir -p /srv/kachuful/production/{releases,shared/og-cache,shared/backups}
sudo chown -R deploy:deploy /srv/kachuful
sed "s/CHANGE_ME/$PGPASS/" deploy/env.example \
  | sudo -u deploy sh -c 'umask 077; cat > /srv/kachuful/production/shared/env'
unset PGPASS

sudo ls -l /srv/kachuful/production/shared/env   # -rw------- deploy deploy
sudo -u deploy bash -c 'set -a; . /srv/kachuful/production/shared/env; psql "${DATABASE_URL/+asyncpg/}" -tAc "select current_user, current_database()"'
# expect: kachuful_production|kachuful_production
```

The other values come from [`deploy/env.example`](deploy/env.example) and are already right for
production. To change one later: `sudo -u deploy nano /srv/kachuful/production/shared/env`, then
`sudo systemctl restart kachuful-api@production`.

Optional Postgres tuning (`/etc/postgresql/16/main/conf.d/kachuful.conf`, then
`sudo systemctl restart postgresql`). The VPS also runs Grafana, Prometheus and other containers, so
stay modest. Example for 8 GB of RAM:

```ini
shared_buffers = 512MB
effective_cache_size = 2GB
work_mem = 16MB
random_page_cost = 1.1          # SSD
```

## 3. systemd (once)

```sh
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo cp deploy/sudoers.kachuful /etc/sudoers.d/kachuful && sudo visudo -cf /etc/sudoers.d/kachuful
sudo systemctl daemon-reload
sudo systemctl enable kachuful-api@production
```

The API starts at the first deploy (step 6). The job timers are switched on in step 7, after there
is code to run.

## 4. nginx (once)

The API routes live in their own file, pulled into the existing `play` site with one `include`
line (just above Certbot's `listen 443 ssl;`):

```sh
sudo cp deploy/nginx/kachuful-api.conf /etc/nginx/snippets/kachuful-api.conf
sudo cp /etc/nginx/sites-available/play /root/play.nginx.bak
grep -q 'snippets/kachuful-api.conf' /etc/nginx/sites-available/play || \
  sudo sed -i 's|^\( *\)listen 443 ssl;|\1include snippets/kachuful-api.conf;\n\n&|' /etc/nginx/sites-available/play
sudo grep -n 'include snippets/kachuful-api.conf' /etc/nginx/sites-available/play
sudo nginx -t && sudo systemctl reload nginx
curl -s -o /dev/null -w "%{http_code}\n" https://play.vectanix.com/api/health   # 502 until step 6
```

When `deploy/nginx/kachuful-api.conf` changes, copy it again and reload. If `nginx -t` fails, put
the old file back (`sudo cp /root/play.nginx.bak /etc/nginx/sites-available/play`) and reload. The
TLS certificate for `play.vectanix.com` already exists.

## 5. GitHub (once)

1. Make a deploy key on the VPS:

   ```sh
   sudo -u deploy install -d -m 700 /home/deploy/.ssh
   sudo -u deploy ssh-keygen -t ed25519 -C kachuful-api-deploy -N '' -f /home/deploy/.ssh/kachuful_api_deploy
   sudo -u deploy sh -c 'cat /home/deploy/.ssh/kachuful_api_deploy.pub >> /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys'
   sudo cat /home/deploy/.ssh/kachuful_api_deploy        # → the DEPLOY_SSH_KEY secret
   ssh-keyscan -t ed25519 VPS_IP                         # → the DEPLOY_KNOWN_HOSTS secret
   sudo rm /home/deploy/.ssh/kachuful_api_deploy           # GitHub keeps the only copy
   ```

   You can reuse the app repo's deploy key and known-hosts instead, if it logs in as `deploy`.

2. The backend repo on GitHub → **Settings** → **Environments** → **New environment** `production`:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Variable | `DEPLOY_HOST` | the VPS IP |
   | Variable | `PUBLIC_URL` | `https://play.vectanix.com` |
   | Secret | `DEPLOY_SSH_KEY` | the private key printed above (all of it, including the `BEGIN`/`END` lines) |
   | Secret | `DEPLOY_KNOWN_HOSTS` | the `ssh-keyscan` line |

Until `DEPLOY_HOST` and `PUBLIC_URL` exist, the CD job runs CI and skips deploying.

## 6. First deploy

1. GitHub → **Actions** → **CD** → **Run workflow** (or push to `main`).
2. The job runs lint, types and tests against Postgres 16; uploads the release to
   `/srv/kachuful/production/releases/<sha>`; runs [`deploy/remote-deploy.sh`](deploy/remote-deploy.sh)
   (installs deps with uv, runs `alembic upgrade head`, switches `current`, restarts the unit, keeps
   5 releases); then checks that `/api/health` reports the new version.
3. Check it:

```sh
curl -s https://play.vectanix.com/api/health          # {"ok":true,"db":true,"version":"<sha>"}
curl -sI https://play.vectanix.com/api/health | grep -iE 'x-content-type|referrer|x-frame'
sudo journalctl -u kachuful-api@production -n 50 --no-pager   # JSON logs, no tokens or passwords
```

## 7. Job timers (once, after the first deploy)

```sh
for job in auto-end close-day close-season prune backup; do
  sudo systemctl enable --now "kachuful-$job@production.timer"
done
systemctl list-timers 'kachuful-*'
```

| Job | When | Does |
| --- | --- | --- |
| `auto-end` | every 15 min | ends games idle for 24 h at their last round |
| `close-day` | 00:05 Asia/Kolkata | freezes yesterday's daily awards |
| `close-season` | 1st of the month, 00:10 Asia/Kolkata | freezes last month's season |
| `prune` | 02:30 Asia/Kolkata | deletes expired sessions/invites/resets/windows and revoked preview images |
| `backup` | 03:00 Asia/Kolkata | `pg_dump -Fc`, keeps 14 daily + 8 weekly, copies to `BACKUP_REMOTE` |

## 8. Your admin account (once)

```sh
sudo -u deploy /srv/kachuful/production/current/deploy/cli.sh production seed-admin
```

It creates the admin for `SEED_ADMIN_EMAIL` and prints a one-time `https://play.vectanix.com/?invite=...`
link. Open it on your phone, choose your player and set a password. Invite everyone else from the
admin screen.

`seed-demo` and `seed-load` refuse to run on production. Test data lives only on local runs
(HANDOFF.md §2).

## 9. Backups and a restore drill

1. Set up an rclone remote as the deploy user (`sudo -iu deploy rclone config`), e.g. Backblaze B2
   or Google Drive, and set `BACKUP_REMOTE=REMOTE:kachuful-backups/production` in the env file.
2. Run one now: `sudo systemctl start kachuful-backup@production && journalctl -u kachuful-backup@production -n 5`.
   The admin overview shows `lastBackupAt`.
3. Restore drill (once, ~5 minutes; restores into a scratch database, production is untouched):

```sh
sudo -u postgres createdb -O kachuful_production kachuful_restore_check
sudo -u postgres pg_restore --no-owner --role=kachuful_production -d kachuful_restore_check \
  /srv/kachuful/production/shared/backups/kachuful-YYYY-MM-DD.dump
sudo -u postgres psql -d kachuful_restore_check -c \
  "select (select count(*) from games) games, (select count(*) from round_scores) rounds, (select max(version_num) from alembic_version) migration;"
sudo -u postgres dropdb kachuful_restore_check
```

The counts should match the admin overview. A real restore is the same `pg_restore` into a fresh
`kachuful_production` (stop `kachuful-api@production` first, start it after).

## 10. Rolling back

```sh
ls -1t /srv/kachuful/production/releases                    # newest first
sudo -u deploy ln -sfn /srv/kachuful/production/releases/PREVIOUS_SHA /srv/kachuful/production/current
sudo systemctl restart kachuful-api@production
```

If the bad release added a migration, first run its downgrade from the **new** release folder:
`cd /srv/kachuful/production/releases/BAD_SHA && set -a && . ../../shared/env && set +a && .venv/bin/alembic downgrade -1`.
Each migration's docstring says what a downgrade loses. The next push to `main` deploys again, so
push the fix (or a revert) soon after a rollback.

## 11. Day-to-day

- Logs: `journalctl -u kachuful-api@production -f` (one JSON object per line).
- Restart: `sudo systemctl restart kachuful-api@production`.
- A job by hand: `sudo -u deploy /srv/kachuful/production/current/deploy/cli.sh production close-day --date 2026-10-30`.
- Live viewers per worker are in memory; a restart drops them and their browsers reconnect by themselves.
