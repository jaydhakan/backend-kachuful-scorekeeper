# Kachuful API

Backend for the Kachuful scorekeeper PWA (https://play.vectanix.com): invite-only accounts, sync of
games played offline, a global player registry, leaderboards, stats, achievements, daily awards,
seasons, Wrapped, live table links and shareable recaps.

FastAPI + PostgreSQL 16, Python 3.12 with uv. Same origin as the app (`/api`), cookie sessions.

- **For the frontend:** [HANDOFF.md](HANDOFF.md): every endpoint, rule and test account.
- **To deploy:** [DEPLOY.md](DEPLOY.md): GitHub, VPS, Postgres, systemd, nginx, Cloudflare.
- **How it was planned:** [plan-backend.md](plan-backend.md). **How to work on it:** [CLAUDE.md](CLAUDE.md).

## Run it locally

```sh
cp .env.example .env
make dev        # Postgres 16 in Docker (port 5433), migrations, API on http://127.0.0.1:8000
make verify     # ruff, mypy --strict, pytest against real Postgres
```

Demo data: `uv run python -m app.cli seed-demo --reset` (prints accounts and links).
Docs: http://127.0.0.1:8000/api/docs.

Performance: `make load` (5,000 games through the real upload path), then `make perf`.
