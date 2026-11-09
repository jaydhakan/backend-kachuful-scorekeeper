# AI Agent Rules

Backend for the Kachuful scorekeeper PWA (https://play.vectanix.com): invite-only accounts, sync of games played offline, a global player registry, leaderboards, stats, achievements, live and recap links. The API as built (its routes and schemas, `/api/docs` when running) is the contract shared with the app. Every change to it comes from the app repo's `backend-requests.md` (see Delivery). There is no `plan-backend.md`, `HANDOFF.md` or `DEPLOY.md` (removed 2026-11-01); don't recreate them.

## Stack - what this is NOT
- **Python 3.12 (uv) + FastAPI + Pydantic v2 + SQLAlchemy 2 async/asyncpg + Alembic + PostgreSQL 16 (`citext`, `pg_trgm`) + argon2-cffi + pytest/httpx/hypothesis + ruff + mypy --strict.** Pillow and segno for images and QR codes; python-dotenv loads a local `.env`.
- Not used, on purpose: pre-commit, pytest-cov, email-validator.
- No Django, no Flask, no GraphQL, no Redis, no Celery/APScheduler or any in-process scheduler (jobs are CLI commands run by systemd timers), no second ORM, no ORM magic in the domain, no CORS (same origin, nginx proxies `/api/`, `/r/`, `/l/`).
- **No new dependency** beyond this list without asking Jay first.

## Commands - uv only
- `make verify` = `uv run ruff check .` + `uv run ruff format --check .` + `uv run mypy app` + `uv run pytest`. It must be green before anything is called done.
- `make dev` starts Postgres 16 (Docker Compose, host port 5433) and the API with reload; `make migrate` = `uv run alembic upgrade head`. Tests use the same container (`kachuful_test` database).
- `app/settings.py` loads `.env` with python-dotenv when the file exists; real environment variables win. No `--env-file` flags needed.
- `uv run pytest -k "pattern"` for a subset. Performance: `make load` once (the load dataset in the dev database), then `make perf` (budgets in `tests/perf`; report in `var/perf-report.json`).
- Golden fixtures: copy the app's `contract/fixtures/*.json` into `tests/fixtures/` and update `tests/fixtures/VERSION.md`.
- Jobs: `uv run python -m app.cli <command>` (`auto-end`, `close-day`, `close-season`, `prune`, `prune-empty`, `backup`, `seed-admin`, `seed-demo`, `seed-load`). `prune-empty` is a one-off (2026-11-06): it deletes games stored before the rule that a game with no round done leaves no trace; `--dry-run` only counts.
- Runtime (systemd) runs `.venv/bin/python -m app`, never `uv run` or a shell. `app/__main__.py` passes the `"app.bootstrap.app:app"` string to uvicorn (workers + `proxy_headers` from settings) and never imports the app object.

## Architecture
- Flow: **route → service → repositories / domain / security**. Imports only flow downwards; `domain` imports nothing from `app`; repositories never import services.
- Routes: thin, async, no business logic or DB access. Parse input, call one service method, return a schema.
- Services: use cases - transactions, permissions, orchestration. No FastAPI imports (jobs call them too).
- Repositories: SQL only, no business rules.
- Domain: pure rules over the game JSON, mirroring the app's TypeScript `src/domain`. Every derived number comes from it.

```
app/
  __main__.py         `python -m app`: starts uvicorn from settings
  settings.py         the only place that reads env vars (typed, validated at startup)
  logger.py           get_logger(), JSON formatter, redaction
  exceptions.py       ApiError, ErrorCode, register_exception_handlers()
  bootstrap/          app.py (FastAPI instance, middleware, routers), lifespan.py
  api/                one package per resource: core (health), auth, admin, players, games, boards,
                      public, pages; each `__init__.py` exports `<resource>_router`
  schemas/            Pydantic request/response models, one module per resource
  services/           one module per resource: `<Resource>Service(session)`; no FastAPI imports
  api/deps.py         request session (`SessionDep`), auth dependencies, `get_<resource>_service()` factories
  repositories/       one module per aggregate: `<Aggregate>Repository` + module-level singleton,
                      both exported from `repositories/__init__.py`
  domain/             rules, validate, derive, stats, leaderboard, achievements, awards, seasons,
                      wrapped, names, time, enums, constants. Frozen dataclasses. No I/O.
  db/                 models.py (tables + naming convention), session.py (the only engine/pool)
  security/           passwords, tokens, sessions, CSRF, rate limits (the only crypto code)
  live/               LISTEN/NOTIFY fan-out and SSE streams
  render/             OG images, QR SVG, thread helper
  jobs/               one module per CLI command
  cli.py              `python -m app.cli <command>`
alembic/              migrations
tests/                mirrors app/: domain/, services/, api/, perf/; fixtures/ (golden); factories.py; conftest.py
deploy/               nginx snippet, systemd units and timers, env example
assets/fonts/         fonts for OG images
```

## Reuse before you write
- Game rules, scores, stats: `app/domain` (pure; parity proven by golden fixtures)
- Errors to clients: `ApiError(status, code, message, **extra)` with `ErrorCode`
- Time: `app/domain/time.py` (epoch ms ⇄ aware UTC; `APP_TZ` days and months)
- Name uniqueness: `domain.names.name_key()` (same function as the app's)
- Game kinds: every `games` row has a `kind` (`domain.enums.GameKind`); `games_repository` reads take the kind they serve, and Kachuful code passes `GameKind.KACHUFUL`
- Player-wide queries (in use, games count, merge, duplicates): `repositories/seats.py` reads every kind's seat table; a new game adds its table to `SEAT_TABLES` and its reseat to `services/merge.RESEAT_BY_KIND`
- Settings: `from app.settings import settings`; never `os.environ`/`os.getenv` elsewhere
- Session/CSRF/rate limits: `app/security`; DB sessions: `app/db/session.py`
- Blocking work (Pillow): `render.run_in_thread()`, never on the event loop

## API Design
- Everything under `/api` except the `/r/{token}` and `/l/{token}` preview pages. Bodies are exactly the contract's shapes: no envelope. Every route declares `response_model` (or `status_code=204`).
- **JSON is camelCase, Python is snake_case.** Models extend `CamelModel` (`alias_generator=to_camel, populate_by_name=True`) and are serialized `by_alias`. Query params use `Annotated[..., Query(alias="updatedSince")]`.
- Times on the wire are epoch milliseconds (`int`); ids are UUID strings; lists are `{items, nextCursor}` with `?cursor=&limit=` (default 50, max 200); ranges are half-open `[from, to)`.
- Routes are plural nouns, kebab-case for multi-word segments (`/reset-link`), no verbs except the actions the contract names (`/merge`, `/login`).
- Route shape:
```python
_GamesServiceDep = Annotated[GamesService, Depends(get_games_service)]

games_router = APIRouter(prefix="/games", tags=["Games"])


@games_router.get("/{game_id}", response_model=GameDetail)
async def get_game(
    game_id: Annotated[UUID, Path(description="Game id")],
    service: _GamesServiceDep,
    user: CurrentUser,
) -> GameDetail:
    """Get one game with its payload, summary and achievements."""
    return await service.get_game(game_id=game_id, user=user)
```
- Pydantic: full type hints, `Field(..., description=...)` on every field (the frontend reads `/api/docs`), `field_validator`/`model_validator` for rules. No raw dicts past the boundary; `Any` only for the stored game payload.

## Logging & Errors
```python
from app.logger import get_logger

logger = get_logger(__name__)
```
- Logs are structured JSON lines. Levels: `info` (lifecycle), `warning` (recoverable), `error` (failures, `exc_info=True`), `debug` (traces). No `print` (ruff `T20`).
- Never log passwords, tokens, cookies or `Authorization`/`Cookie` headers. All masking lives in `app/logger.py` (`SENSITIVE_KEYS`); add new names there, never a second list.
- Raise `ApiError` - one handler maps it to `{code, message, ...extra}`. Request validation becomes `422 invalid {fields}`; anything unhandled becomes `500 internal` with no stack trace or SQL in the body.
- Error codes are `snake_case` and come from the contract (`name_taken`, `stale`, `weak_password`, ...). A new code is a contract change: it must be in `backend-requests.md` first.
- Always raise or propagate - never swallow. No bare `except`, no blind `except Exception` (ruff `BLE`) except at the one 500 handler and in best-effort jobs, which log it.

## Database
### Naming
- Tables plural `snake_case`; columns `snake_case`; timestamps `*_at timestamptz`; foreign keys `<thing>_id`.
- Constraints via the metadata naming convention: `pk_<table>`, `fk_<table>_<col>`, `uq_<table>_<cols>`, `ix_<table>_<cols>`, `ck_<table>_<name>`.

### Repository Pattern
- One class per aggregate, module-level singleton, methods take `session: AsyncSession` first. Data in, rows/dataclasses out; free of business rules and permissions.
- All SQL through SQLAlchemy Core/ORM with bound parameters. No string-built SQL.

### Efficiency (target: a fixed number of queries per request, never N)
```python
# Bad: for row in rows: await session.execute(insert(RoundScore).values(**row))
# Good: await session.execute(insert(RoundScore), rows)  # one executemany
```
1. Collect ids first → 2. one `IN (...)` / join fetch → 3. build in-memory maps (`players_by_id = {p.id: p for p in players}`) → 4. bulk insert/update.
- Tests assert the query count on the main endpoints. Pass fetched rows down, not ids that need re-fetching.
- **Every hot-path query has an index**, with an `EXPLAIN` test asserting it is used. List endpoints never read `games.payload`; they read `games.summary`.
- Derived data (`round_scores`, `game_players`, `player_day`, `games.summary`) is written on upload in the same transaction, never computed on read.
- Paginate with keyset cursors on indexed columns; sort deterministically (`ORDER BY updated_at DESC, id DESC`).

### Migrations (Alembic)
- One migration per change; files are date-prefixed (`20261031_<rev>_add_player_day_rollup.py`) with an imperative message ("add player_day rollup").
- The module docstring says what changes, why, and how to roll back. Every migration has a working `downgrade()` or a written rollback note.
- Never edit an applied migration. Data migrations are separate revisions from schema ones.

## Domain & Parity
- The app's TypeScript `src/domain` is the reference. Golden fixtures in `tests/fixtures/` must all pass; never edit a fixture to make a test pass - report the mismatch to Jay.
- Until the app exports `contract/fixtures`, fixtures are hand-written from the domain rules and marked `"source": "hand-written"`.
- Game rules and scoring never change without a failing test first. Domain tests come before domain code.

## Security
- argon2id (library defaults); tokens are 32 random bytes, url-safe, stored only as SHA-256 (share-link tokens are the one exception).
- Cookie `kf_session` (HttpOnly, Secure, SameSite=Lax, Path=/); 180-day rolling expiry refreshed at most once a day.
- Every non-GET needs `X-Kachuful: 1` and a same-site `Origin`, else `403 csrf`. Rate limits as `app/security/rate_limit.py` sets them.
- Password rule: 3-128 characters, no whitespace. Nothing else.
- Public endpoints never expose emails, user ids or first/last names.

## Code Quality
- Python 3.12, PEP 8, ruff format at line length 100, f-strings, keyword args for anything non-obvious, keyword-only (`*,`) for flags. No mixed returns.
- Type hints everywhere; `mypy --strict` passes. `Any` only at the JSON boundary.
- Imports: stdlib → third-party → internal, always at the top, never inside functions.
- Cognitive complexity < 15, one responsibility per function. No nested functions - extract module-level `_helpers`. Many small functions over one big one.
- Names: modules/functions `snake_case` (functions are verbs: `derive_game`, `rank_rows`), classes `PascalCase`, constants `UPPER_SNAKE`, private helpers `_leading_underscore`. Enums are `StrEnum` with `UPPER` members.
- Docstrings: one line on public functions, methods and routes (routes' docstrings show in `/api/docs`). Add a short paragraph only when behaviour isn't obvious; no `Args:`/`Returns:` blocks - the type hints say that.
- Comments: 1-3 lines, non-obvious *why* only. No commented-out code.
- Async for all I/O. CPU-heavy work (OG images) goes through `render.run_in_thread()`.
- **No temporary fixes.** Fix the root cause or say where the fix belongs.

## Jobs
- One module per command in `app/jobs/`, called by `app/cli.py`, scheduled by systemd timers in `deploy/`. Jobs reuse services; they never duplicate a use case.
- Jobs are idempotent and safe to re-run; `statement_timeout` is 60 s for jobs (2 s for API requests).

## Testing
- Files are `test_<unit>.py`, mirroring `app/`; test names state the behaviour: `test_upload_with_older_rev_returns_stale`.
- Every endpoint has tests for the happy path, every error code and every permission rule, against a real Postgres (transaction per test, rolled back). Never mock the DB.
- Property tests (`hypothesis`) for validation; concurrency and SSE tests for every lock and stream.
- No coverage tool: completeness comes from the rule above, and every domain rule has its own test.
- `tests/perf` (marker `perf`) holds the performance budgets; it must stay under them.

## Delivery
- **Git: work on `main` only and push to `origin main`** (Jay's standing instruction, 2026-10-31). No branches, no PRs.
- Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `chore:`, `docs:`, `ci:`), one feature or concern each; never mix a refactor with a behaviour change. **No co-author trailer.**
- Never deploy to production, run `seed-admin` on production, or touch CI secrets unless Jay asks.
- **Changes come from `backend-requests.md`**, at the root of the app repo (`../web-kachuful-scorekeeper`): the one working doc for any API change, saying what is needed and why. Once it exists, build the backend side here in the same task, exactly what it asks and nothing more. If it is wrong or missing something, stop and tell Jay; never change the contract silently.
- **Working docs are temporary:** when the feature is done in both repos (built, tested, committed), delete `backend-requests.md` and the task's plan docs. Never commit them, and don't keep any other changelog or handoff file.
- Touch only the files a change needs and mirror existing patterns. If a change touches more than ~3 files, outline the plan first.
- **Progress:** after every milestone, tell Jay where the build stands as a percentage, using these weights (cumulative): B0 5% · B1 15% · B2 23% · B3 33% · B4 45% · B5 59% · B6 71% · B7 80% · B8 90% · B9 96% · B10 100%.
- **Parallel agents:** whenever pieces of work don't conflict (different modules, migrations vs routes, tests vs fixtures), run them as parallel subagents (2, 4 or 6 at once). Give each a narrow brief with the files it owns, and review what each returns before building on it.
- Jay deploys. There is **no staging** (2026-11-01): one production environment, and every push to `main` deploys there after CI, so `make verify` must be green before every push. `deploy/` and `.github/workflows/` are the source of truth for how it runs; keep them in step with any change that affects deployment, and tell Jay what he must do on the VPS.

## Pre-Submit Checklist
1. `make verify` green; new migration applies and downgrades cleanly
2. Routes thin and async; services orchestrate; repositories data-only; domain pure
3. Every route has `response_model`, camelCase out, contract error codes only
4. Fixed query count; hot queries indexed with an `EXPLAIN` test; no `payload` reads on lists
5. Tests for happy path, each error code, each permission rule
6. No secrets, tokens or passwords in code or logs; logging levels sensible
7. Minimal scope, no surprise files; done items in `backend-requests.md` ticked
8. Anything stubbed, mocked or unverified is said plainly

## Definition of Done
- `make verify` is green, everything `backend-requests.md` asked for is built and tested, and anything stubbed, mocked or unverified is said plainly. Production is deployed by Jay; until he confirms it, say the deploy is unverified.

## Debugging
- Use `logger.debug(...)` and remove noisy logs before committing; never `print`.
- Always update this file when new rules emerge.
