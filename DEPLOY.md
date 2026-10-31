# Deploying the Kachuful API

A runbook for Jay. Follow it top to bottom once for **staging**, then repeat the marked steps for
**production**. Commands are for Ubuntu 22.04/24.04 on the VPS that already serves
`play.vectanix.com`. Anything in `CAPITALS` is yours to fill in.

What you end up with:

| | Staging | Production |
| --- | --- | --- |
| URL | `https://staging.play.vectanix.com` | `https://play.vectanix.com` |
| API port (local only) | 8001 | 8000 |
| systemd unit | `kachuful-api@staging` | `kachuful-api@production` |
| Code | `/srv/kachuful/staging/current` | `/srv/kachuful/production/current` |
| Settings | `/srv/kachuful/staging/shared/env` | `/srv/kachuful/production/shared/env` |
| Database | `kachuful_staging` | `kachuful_production` |
| Deploys | every push to `main` (after CI) | by hand: Actions → CD → Run workflow → production |

---

## 1. DNS (Cloudflare) — staging only

1. Cloudflare → `vectanix.com` → **DNS** → **Add record**.
2. Type `A`, Name `staging.play`, IPv4 = the VPS IP (same as the `play` record), Proxy status
   **Proxied** (orange cloud) or DNS only — both work.
3. **SSL/TLS** → mode **Full (strict)** (the VPS has a real certificate, step 6).
4. If proxied: Cloudflare closes idle connections after 100 s; the live stream pings every 25 s,
   so SSE works through it. Do **not** turn on "Rocket Loader" or any HTML rewriting for `/r/*`
   and `/l/*`.

`play.vectanix.com` already exists; nothing to change for production.

## 2. Packages on the VPS (once)

```sh
sudo apt update
sudo apt install -y postgresql-16 postgresql-client-16 nginx rclone certbot python3-certbot-nginx
# uv for the deploy user (the CD job uses ~/.local/bin/uv)
sudo -iu deploy sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
```

If there is no `deploy` user yet (the app's CD already uses one): `sudo adduser --disabled-password deploy`.

`citext` and `pg_trgm` ship with `postgresql-16`; the first migration enables them.

## 3. Postgres (once per environment)

```sh
sudo -u postgres psql <<'SQL'
CREATE ROLE kachuful_staging LOGIN PASSWORD 'LONG_RANDOM_STAGING_PASSWORD';
CREATE DATABASE kachuful_staging OWNER kachuful_staging;
\c kachuful_staging
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
SQL
```

Production: same with `kachuful_production` and a different password.

Tuning for a small VPS (`/etc/postgresql/16/main/conf.d/kachuful.conf`, then
`sudo systemctl restart postgresql`) — use your RAM; example for 4 GB:

```ini
shared_buffers = 1GB            # ~25% of RAM
effective_cache_size = 2400MB   # ~60% of RAM
work_mem = 16MB
random_page_cost = 1.1          # SSD
listen_addresses = 'localhost'
```

## 4. Folders and settings (once per environment)

```sh
sudo mkdir -p /srv/kachuful/staging/{releases,shared/og-cache,shared/backups}
sudo chown -R deploy:deploy /srv/kachuful
sudo -u deploy cp /dev/null /srv/kachuful/staging/shared/env
sudo -u deploy chmod 600 /srv/kachuful/staging/shared/env
sudo -u deploy nano /srv/kachuful/staging/shared/env
```

Paste [`deploy/env.example`](deploy/env.example) and fill it in. For production change:
`APP_ENV=production`, `DATABASE_URL` (production role, password, database), `APP_ORIGIN=https://play.vectanix.com`,
`PORT=8000`, and the two `/srv/kachuful/production/...` paths.

## 5. systemd (once)

From a checkout of this repo on the VPS (or copy the files over):

```sh
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo cp deploy/sudoers.kachuful /etc/sudoers.d/kachuful && sudo visudo -cf /etc/sudoers.d/kachuful
sudo systemctl daemon-reload
sudo systemctl enable kachuful-api@staging
for job in auto-end close-day close-season prune backup; do
  sudo systemctl enable --now "kachuful-$job@staging.timer"
done
```

(The API unit starts at the first deploy, step 8.) Production: the same commands with `@production`.

`systemctl list-timers 'kachuful-*'` shows when each job runs next:

| Job | When | Does |
| --- | --- | --- |
| `auto-end` | every 15 min | ends games idle for 24 h at their last round |
| `close-day` | 00:05 Asia/Kolkata | freezes yesterday's daily awards |
| `close-season` | 1st of the month, 00:10 Asia/Kolkata | freezes last month's season |
| `prune` | 02:30 Asia/Kolkata | deletes expired sessions/invites/resets/windows and revoked preview images |
| `backup` | 03:00 Asia/Kolkata | `pg_dump -Fc`, keeps 14 daily + 8 weekly, copies to `BACKUP_REMOTE` |

## 6. nginx and TLS

**Staging** (new site):

```sh
sudo certbot certonly --nginx -d staging.play.vectanix.com
sudo cp deploy/nginx/staging.play.vectanix.com.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/staging.play.vectanix.com.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Staging serves the **production app build** (`root /var/www/play`); because the app calls `/api`
on its own origin, it talks to the staging API automatically.

**Production**: paste [`deploy/nginx/kachuful-api.conf`](deploy/nginx/kachuful-api.conf) inside the
existing `server { ... }` block for `play.vectanix.com`, then `sudo nginx -t && sudo systemctl reload nginx`.

## 7. GitHub (once)

Repository → **Settings** → **Environments** → create `staging` and `production`. In each:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `DEPLOY_HOST` | the VPS IP or hostname |
| Variable | `PUBLIC_URL` | `https://staging.play.vectanix.com` / `https://play.vectanix.com` |
| Secret | `DEPLOY_SSH_KEY` | a private key whose public half is in `/home/deploy/.ssh/authorized_keys` |
| Secret | `DEPLOY_KNOWN_HOSTS` | output of `ssh-keyscan -t ed25519 YOUR_VPS_HOST` |

Make a key just for this: `ssh-keygen -t ed25519 -C kachuful-api-deploy -f kachuful_deploy -N ''`,
put `kachuful_deploy.pub` in the deploy user's `authorized_keys`, paste `kachuful_deploy` into the
secret, then delete both local files. You can reuse the app repo's key and known-hosts if you prefer.

Optional: on `production`, add yourself under **Required reviewers** so a production deploy needs a click.

Until `DEPLOY_HOST` and `PUBLIC_URL` exist for an environment, the CD job runs CI and skips deploying.

## 8. First deploy (staging)

1. Push to `main` (or Actions → **CD** → **Run workflow** → `staging`).
2. The job runs lint, types and tests against Postgres 16; uploads the release to
   `/srv/kachuful/staging/releases/<sha>`; runs [`deploy/remote-deploy.sh`](deploy/remote-deploy.sh)
   (installs deps with uv, runs `alembic upgrade head`, switches `current`, restarts the unit,
   keeps 5 releases); then checks `/api/health` reports the new version.
3. On the VPS, fill staging with test data and print the test accounts:

```sh
sudo -u deploy /srv/kachuful/staging/current/deploy/cli.sh staging seed-demo --reset
```

It prints an admin and two members with passwords, a pending invite link and a reset link
(HANDOFF.md lists the same accounts for the frontend). For the performance dataset instead:
`... cli.sh staging seed-load --reset` (5,000 games; takes a few minutes).

## 9. Check staging

```sh
curl -s https://staging.play.vectanix.com/api/health          # {"ok":true,"db":true,"version":"<sha>"}
curl -sI https://staging.play.vectanix.com/api/health | grep -iE 'x-content-type|referrer|x-frame'
sudo journalctl -u kachuful-api@staging -n 50 --no-pager     # JSON logs, no tokens or passwords
systemctl list-timers 'kachuful-*'
```

Then run the copy-paste checks in HANDOFF.md §4 against staging and tell me it's up, so the
handoff can be marked "verified on staging".

## 10. Production (when you're ready)

1. Steps 3–5 and 7 with `production`; step 6's production snippet.
2. Actions → **CD** → **Run workflow** → environment **production**.
3. Create the admin and get the one-time link to set your password:

```sh
sudo -u deploy /srv/kachuful/production/current/deploy/cli.sh production seed-admin
```

Open the printed `https://play.vectanix.com/?invite=...` link on your phone, choose your player and
set a password. Invite everyone else from the admin screen.

**Never** run `seed-demo` or `seed-load` on production (they refuse anyway).

## 11. Backups and a restore drill

1. Set up an rclone remote as the deploy user (`sudo -iu deploy rclone config`), e.g. Backblaze B2
   or Google Drive, and set `BACKUP_REMOTE=REMOTE:kachuful-backups/production` in the env file.
2. Run one now: `sudo systemctl start kachuful-backup@production && journalctl -u kachuful-backup@production -n 5`.
   The admin overview shows `lastBackupAt`.
3. Restore drill (do it once on staging, ~5 minutes):

```sh
sudo -u postgres createdb -O kachuful_staging kachuful_restore_check
sudo -u postgres pg_restore --no-owner --role=kachuful_staging -d kachuful_restore_check \
  /srv/kachuful/production/shared/backups/kachuful-YYYY-MM-DD.dump
sudo -u postgres psql -d kachuful_restore_check -c \
  "select (select count(*) from games) games, (select count(*) from round_scores) rounds, (select max(version_num) from alembic_version) migration;"
sudo -u postgres dropdb kachuful_restore_check
```

The counts should match the admin overview. A real restore into production is the same `pg_restore`
into a fresh `kachuful_production` (stop `kachuful-api@production` first, start it after).

## 12. Rolling back

```sh
ls -1t /srv/kachuful/production/releases                    # newest first
sudo -u deploy ln -sfn /srv/kachuful/production/releases/PREVIOUS_SHA /srv/kachuful/production/current
sudo systemctl restart kachuful-api@production
```

If the bad release added a migration, first run its downgrade from the **new** release folder:
`cd /srv/kachuful/production/releases/BAD_SHA && set -a && . ../../shared/env && set +a && .venv/bin/alembic downgrade -1`.
Each migration's docstring says what a downgrade loses.

## 13. Day-to-day

- Logs: `journalctl -u kachuful-api@production -f` (one JSON object per line).
- Restart: `sudo systemctl restart kachuful-api@production`.
- A job by hand: `sudo -u deploy /srv/kachuful/production/current/deploy/cli.sh production close-day --date 2026-10-30`.
- Live viewers per worker are in memory; a restart drops them and their browsers reconnect by themselves.
