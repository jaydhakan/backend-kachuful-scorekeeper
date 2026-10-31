# Kachuful backend: build plan

_For the backend repo. Written 2026-10-31 from decisions made with Jay. The frontend has its own plan (`plan-frontend.md` in the app repo). **§6, the API contract, is shared by both and must not change without updating both plans.**_

---

## 1. What we are building

Kachuful is a mobile-first scorekeeper for the Judgement / Kachuful card game (`https://play.vectanix.com`). One phone or tablet scores the whole table. It is a React PWA that works fully offline, keeps everything in the browser, and has no backend today.

This backend adds:

- **Invite-only accounts.** There is no sign-up: one seeded admin invites people.
- **Sync.** A signed-in scorer's games, history and players are stored on the server.
- **A global player registry** with unique names.
- **Leaderboards, stats, achievements, daily awards and monthly seasons**, computed by the server from the rounds.
- **Live table links and shareable recaps** that anyone with the link can open.

The app stays **offline-first**: the phone is the source of truth while playing and syncs when it can. A signed-out phone never talks to the server.

---

## 2. Decisions (final; don't reopen without Jay)

| Topic                | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Stack                | **FastAPI + PostgreSQL** on Jay's own VPS (the one serving `play.vectanix.com`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Address              | Same origin: **`https://play.vectanix.com/api/...`**. nginx proxies `/api/` (and the `/r/` and `/l/` preview pages) to FastAPI. No CORS.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Sign-up              | **None.** One admin is seeded: `jaydhakan11@gmail.com`. Only the admin can invite.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Invites              | Admin enters an email and gets a **copyable one-time link** (shared over WhatsApp). The invitee opens it and sets a password. **No email sending.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Forgot password      | Admin generates a **one-time reset link** from the admin screen. No self-service email.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Login                | Email + password only. No Google or other social login.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Password rule        | **At least 3 characters, no whitespace anywhere.** Nothing else: all lower-case, all upper-case and no special characters are all fine. Maximum 128.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| "Online"             | Signed in = online: every game the scorer plays syncs, with all its history.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Which games count    | **All games count**, provided the scorer was signed in (the uploader) and the game has **at least 3 players** (the app's minimum).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Global leaderboard   | **Every player name** is ranked, not just account holders.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Leaderboard ranking  | **Default sort: average points per round** (total points ÷ rounds; shown as a whole number, ranked on the exact value). Players are **ranked only with more than N rounds in the range**: N = 10 for Today, 25 for Last 7 days, 50 for This month, Last month and All time. A custom range uses the N for its length (≤ 1 day → 10, ≤ 7 days → 25, longer → 50). Everyone else is listed **unranked and greyed out below the ranked players**, sorted the same way. The minimum applies to rate metrics (average, accuracy, win rate); count metrics (points, wins, games, rounds, best round, longest run) rank everyone. |
| Player names         | **Globally unique, case-insensitive** (spaces collapsed). Adding an existing name asks "Is this the same Riya?". Yes links to that player; No means choosing a different name. Optional first/last name help tell people apart. **Updated 2026-10-31 (§6.8):** a player is the same person only when name **and** email both match; partial matches reveal nothing.                                                                                                                                                                                                                                                                                                                                                                                            |
| Visibility           | **Every signed-in user can see every game** (a small, closed, invite-only circle).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Old signed-out games | On first sign-in the app asks whether to upload them. Uploaded games count like any other.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| Unfinished games     | **Every confirmed round counts** (points, rounds, streaks). Wins and places count when a game ends: finished, End game, or **auto-ended after 24 h** without an update.                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| Live / recap links   | **Anyone with the link** (no login), read-only. Long random tokens that the scorer can revoke.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Name edits           | **Display name:** admin or the user linked to that player. **First/last name, emoji, colour:** also the user who created the player. **Merging two players:** admin only.                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Timezone             | Daily awards, seasons and "night owl" use **`APP_TZ = Asia/Kolkata`**. Leaderboard date ranges arrive from the client as exact instants.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| v1 extras            | Player pages + head-to-head, achievements + seasons (with daily awards and a yearly "Wrapped"), live table link, shareable recaps with link previews, activity feed.                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Not in v1            | Groups, tournaments, bidding from each player's own phone, web push notifications, house-rule scoring variants.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

---

## 3. Stack

| Concern        | Choice                                                                                                                                   |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Runtime        | Python 3.12, managed with `uv`                                                                                                           |
| Web            | FastAPI, Pydantic v2, Uvicorn (2–4 workers behind gunicorn, or plain uvicorn under systemd)                                              |
| DB             | PostgreSQL 16, SQLAlchemy 2.0 (async) + asyncpg, Alembic migrations, the `citext` and `pg_trgm` extensions                                              |
| Passwords      | `argon2-cffi` (argon2id)                                                                                                                 |
| Live updates   | Server-Sent Events (`StreamingResponse`), fanned out with Postgres `LISTEN/NOTIFY` so any worker can serve a viewer                      |
| Scheduled jobs | systemd timers calling CLI commands (auto-end games, daily awards, season close). No in-process scheduler.                               |
| Link previews  | Pillow renders the 1200×630 recap image; `segno` renders QR codes as SVG                                                                 |
| Quality        | `ruff` (lint + format), `mypy --strict`, `pytest` + `httpx` AsyncClient against a real Postgres                                          |
| CI/CD          | GitHub Actions: lint, typecheck and tests with a Postgres service container, then deploy over SSH (same style as the app's `deploy.yml`) |

---

## 4. Architecture

```
Browser (PWA at play.vectanix.com)
   │  same-origin fetch, cookie session, header X-Kachuful: 1 on writes
   ▼
nginx ── /            → static app (/var/www/play)       (unchanged)
      ├─ /api/        → FastAPI 127.0.0.1:8000           (proxy_buffering off for */events)
      └─ /r/  /l/     → FastAPI (preview HTML for WhatsApp, then redirect into the app)
FastAPI
   ├─ auth        sessions, invites, resets, rate limits
   ├─ admin       users, invites, reset links, player merge
   ├─ players     registry, name check, profiles, stats, head-to-head
   ├─ games       upload (PUT), validate, derive, list, links
   ├─ boards      leaderboard, daily awards, seasons, wrapped, activity
   ├─ public      live JSON + SSE, recap JSON, OG image, preview pages
   └─ domain/     pure Python port of the app's rules + stats + achievements (golden-fixture tested)
PostgreSQL (games stored whole as JSON + derived tables for fast queries)
```

**Rule:** `domain/` is pure. It has no database or FastAPI imports and is just functions over the game JSON, mirroring the app's TypeScript `src/domain`. Every derived number comes from it.

---

## 5. Data model

### 5.1 Tables

```
users            id uuid pk, email citext unique, role text ('admin'|'member'), password_hash text null,
                 active bool default true, player_id uuid null unique → players, created_at, last_seen_at
invites          id uuid pk, email citext, token_hash text unique, player_id uuid null, created_by → users,
                 created_at, expires_at (7 days), accepted_at null, revoked_at null
password_resets  id uuid pk, user_id → users, token_hash text unique, created_at, expires_at (24 h), used_at null
sessions         id uuid pk, user_id → users, token_hash text unique, created_at, last_used_at,
                 expires_at (180 days, rolling), user_agent text
players          id uuid pk (client-generated), display_name text, name_key text, first_name text null,
                 last_name text null, emoji text, color text, created_by → users, merged_into uuid null → players,
                 created_at, updated_at
                 email citext null, photo_rev int default 0,
                 UNIQUE (name_key) WHERE merged_into IS NULL,
                 UNIQUE (email) WHERE merged_into IS NULL AND email IS NOT NULL
player_photos    player_id uuid pk → players, image bytea (256x256 WebP), updated_at
games            id uuid pk (client-generated), owner_id → users, rev int, payload jsonb, payload_hash text,
                 schema_version int, status text, player_count int, round_count int, summary jsonb (GameSummary),
                 created_at, updated_at, finished_at null, ended_at null, auto_ended bool, deleted_at null
game_players     game_id → games, local_id text, seat int, player_id → players, name_snapshot text,
                 points int, place int null, won bool, rounds int, made int, missed int,
                 best_round int null, worst_round int null, longest_hot int, longest_cold int, hook_hits int,
                 PRIMARY KEY (game_id, local_id)
round_scores     game_id, round_index, player_id, done_at timestamptz, cards int, bid int, outcome text,
                 points int, is_dealer bool, hook_hits int, run_hot int, run_cold int,
                 PRIMARY KEY (game_id, round_index, player_id)
player_day       player_id, day date (APP_TZ), rounds int, points int, made int, missed int, best_round int,
                 worst_round int, max_hot int, max_cold int, hook_hits int, games int, games_ended int, wins int,
                 PRIMARY KEY (player_id, day)     -- rollup; rebuilt for the days a game touches (§15)
achievements     id uuid pk, player_id → players, code text, game_id null, round_index int null,
                 period text null ('2026-10-31' daily, '2026-10' season, '2026' wrapped),
                 earned_at timestamptz, data jsonb,
                 UNIQUE (player_id, code, coalesce(game_id), coalesce(round_index), coalesce(period))
share_links      token text pk (22-char url-safe random), game_id → games, kind text ('live'|'recap'),
                 created_by → users, created_at, revoked_at null
activity         id bigserial pk, kind text, player_id null, game_id null, data jsonb, created_at
rate_limits      key text, window_start timestamptz, count int, PRIMARY KEY (key, window_start)
```

- `name_key = lower(collapse_spaces(trim(display_name)))`, the same function as the app's.
- Indexes:
  - `round_scores (done_at)` and `(player_id, done_at)`
  - `game_players (player_id)`
  - `games (ended_at)` and `(updated_at)`
  - `achievements (player_id, earned_at)` and `(code, period)`
  - `activity (created_at desc)`
- Tokens (invite, reset, session) are random 32 bytes, url-safe. Only their **SHA-256 is stored**. Share-link tokens are stored plain: they are capabilities the owner needs to show again as a QR code.
- `game_players` and `round_scores` are **rebuilt from `payload` inside the same transaction** as every game upload.

### 5.2 The game payload (wire format; exactly the app's `Game`, plus sync fields)

```jsonc
{
  "id": "6f1c…uuid", // stable sync id
  "rev": 12, // increments on every local change (undo included)
  "schemaVersion": 3,
  "createdAt": 1761900000000, // epoch ms
  "updatedAt": 1761903600000,
  "finishedAt": null, // epoch ms when the game ended in the app
  "status": "active", // 'active' | 'finished'
  "direction": "cw", // 'cw' | 'ccw' (seat layout only)
  "firstDealerIndex": 0,
  "decks": 1, // 1 | 2
  "maxCards": 10,
  "lengthMode": "endless", // 'endless' | '1cycle' | '2cycles'
  "players": [
    { "id": "p0", "playerId": "uuid", "name": "Riya", "emoji": "🦊", "color": "#ff8a65" },
  ],
  "rounds": [
    {
      "index": 0,
      "cards": 3,
      "trump": "spades",
      "dealerIndex": 0,
      "firstPlayerIndex": 1,
      "bids": { "p0": 1 },
      "results": { "p0": "made" },
      "tricks": null,
      "hookHits": 0,
      "phase": "done",
      "doneAt": 1761900300000,
    },
  ],
  "ui": { "view": "round", "bidIdx": 0 }, // app-only; store it, ignore it
}
```

- `players[].id` is the **game-local** id that `bids`, `results` and `tricks` are keyed by. `players[].playerId` is the **registry** id.
- Games from before this release have no `doneAt` on rounds and no `finishedAt`. For those, use `updatedAt` as the time of every round and of the finish.

---

## 6. API contract (shared with the frontend)

**Changes approved by Jay (mirror them in the app repo's plan):**

| Date       | Change                                                                                                                                                                              |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 2026-10-31 | `GET /api/admin/users`: `invite` is `{expiresAt}` only (no `url`; tokens are hash-only). A fresh link comes from `POST /api/admin/invites` again.                                    |
| 2026-10-31 | Public responses (`/api/public/*`) send `GameSummary.owner` as `{id: null, name}`: no user ids in public.                                                                            |
| 2026-10-31 | `PUT /api/games/{id}` on a deleted game → `410 gone`. `GET /api/games?updatedSince=` also returns tombstones `{id, deleted: true, updatedAt}` for games deleted since then.        |
| 2026-10-31 | New codes: `409 last_admin`, `409 id_conflict` (`POST /api/players`, same id with a different body), `422 invalid_game {reason: "id"}` (path id ≠ payload id).                    |
| 2026-10-31 | Defined: `invalid.fields` is `{"<camelCaseField>": "message"}`; `retryAfter` is in seconds; `accuracy` and `winRate` are fractions 0–1; `DELETE /api/admin/invites/{email}` is `204` even when none is pending; `createdByName` and `owner.name` are the user's linked player's display name; registry display names are 1–14 characters (same as in a game). |
| 2026-10-31 | §8.3 career repeat rules: as the app's engine and golden fixtures (see there). Game id = the app's `syncId` (else `id`). |
| 2026-10-31 | **Player identity and photos** (§6.8). Every player has a unique email (case-insensitive) that identifies the person with the name; the same player = name **and** email match; `GET /api/players/check?name=&email=` answers `match` / `email_taken` / `name_taken` / `new` and only a match names a player; `POST /api/players` needs `email` and answers `409 same_player {player}` / `409 email_taken` / `409 name_taken` (no details); emails only for admin, creator and linked user; invites link the unlinked player with the invite email; player photos (`photoUrl` on Player and `GameSummary.players[]`). `409 name_taken` no longer carries `existing` anywhere. |                                                                                                                                              |

General rules:

- **Paths:** everything under `/api`. JSON bodies, `camelCase` fields, times in **epoch milliseconds**, ids are UUID strings.
- **Auth:** a cookie named **`kf_session`** (`HttpOnly; Secure; SameSite=Lax; Path=/`).
  - Every non-GET request must carry the header **`X-Kachuful: 1`** and a same-site `Origin`. Anything else gets `403 {code:"csrf"}`.
- **Errors:** `{ "code": "snake_case", "message": "human text", ...extra }` with the right status.
  - Common codes: `unauthenticated` (401), `forbidden` (403), `not_found` (404), `gone` (410, used or expired token), `invalid` (422, with `fields: {"<camelCaseField>": "message"}`), `rate_limited` (429, with `retryAfter` in seconds).
- **Lists:** cursor pagination as `?cursor=…&limit=…` (default 50, max 200), answered as `{ items: [...], nextCursor: string|null }`.
- **Ranges:** `from` and `to` are epoch ms, half-open `[from, to)`. The client works them out in the viewer's timezone.

### 6.1 Auth

| Method & path                    | Body                                    | Success                                                                                                                                                                                                    | Errors                                                                                      |
| -------------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `POST /api/auth/login`           | `{email, password}`                     | `200 {user}` + cookie                                                                                                                                                                                      | `401 bad_credentials`, `403 inactive`, `429`                                                |
| `POST /api/auth/logout`          | –                                       | `204`, cookie cleared, session deleted                                                                                                                                                                     |                                                                                             |
| `GET /api/auth/me`               | –                                       | `200 {user, player}`                                                                                                                                                                                       | `401`                                                                                       |
| `GET /api/bootstrap`             | –                                       | `200 {user, player, players: Player[], myGames: GameSummary[], myAchievements: Achievement[], dataVersion}`. One call after sign-in or app start, so the app is filled in one round trip. Supports `ETag`. | `401`                                                                                       |
| `GET /api/auth/invites/{token}`  | –                                       | `200 {email, player: PlayerSummary\|null}`: the invite's player, else the unlinked player with the invite email                                                                                                                                                                 | `410 gone`                                                                                  |
| `POST /api/auth/invites/{token}` | `{password, player: {id} \| NewPlayer}` | `200 {user, player}` + cookie                                                                                                                                                                              | `410`, `422 weak_password`, `409 name_taken`, `409 email_taken`, `409 player_linked` (no player details in any) |
| `GET /api/auth/resets/{token}`   | –                                       | `200 {email}`                                                                                                                                                                                              | `410`                                                                                       |
| `POST /api/auth/resets/{token}`  | `{password}`                            | `200 {user, player}` + cookie; **all other sessions revoked**                                                                                                                                              | `410`, `422 weak_password`                                                                  |
| `POST /api/me/password`          | `{current, password}`                   | `204`; other sessions revoked                                                                                                                                                                              | `401 bad_credentials`, `422 weak_password`                                                  |

```ts
User = { id, email, role: 'admin'|'member', playerId: string|null, createdAt }
NewPlayer = { id: uuid, displayName, email, firstName?: string, lastName?: string, emoji, color }  // email required by POST /api/players; on invites the invite email is used
```

Notes:

- When accepting an invite, the player is either an existing registry player (`{id}`, which must not already be linked to another user) or a new one.
- If the invite was created with a `playerId`, the client sends that id.
- **Password validation:** `len >= 3 and len <= 128 and not re.search(r"\s", pw)`. Otherwise `422 weak_password`.

### 6.2 Admin (role `admin`; everyone else gets `403`)

| Method & path                           | Body                 | Success                                                                                                                                                                                                                      |
| --------------------------------------- | -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/admin/users`                  | –                    | `200 [{user, player, active, lastSeenAt, invite: {expiresAt}\|null}]`                                                                                                                                                        |
| `POST /api/admin/invites`               | `{email, playerId?}` | `201 {inviteUrl, expiresAt}`. URL: `https://play.vectanix.com/?invite=<token>`. A new invite replaces any pending one for that email. `409 user_exists` if already a user.                                                   |
| `DELETE /api/admin/invites/{email}`     | –                    | `204` (revokes the pending invite; also `204` when none is pending)                                                                                                                                                                                      |
| `POST /api/admin/users/{id}/reset-link` | –                    | `201 {resetUrl, expiresAt}`. URL: `https://play.vectanix.com/?reset=<token>`                                                                                                                                                 |
| `PATCH /api/admin/users/{id}`           | `{active?: bool}`    | `200 {user}`. Deactivating revokes all sessions. The last active admin can't be deactivated (`409 last_admin`).                                                                                                                                 |
| `POST /api/admin/players/{id}/merge`    | `{intoId}`           | `200 {player}`. Rewrites `game_players`, `round_scores`, `achievements` and every stored payload's `playerId`; sets `merged_into`; recomputes affected achievements. If both players are linked to users: `409 both_linked`. The target keeps its email and photo; if it has none, it takes the merged player's. |
| `GET /api/admin/overview`               | –                    | `200 {users, players, games, rounds, lastBackupAt}`                                                                                                                                                                          |

### 6.3 Players (signed in)

| Method & path                                   | Body / query                                            | Success                                                                                                                                                                                                                |
| ----------------------------------------------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/players?updatedSince=&cursor=`        | –                                                       | `200 {items: Player[], nextCursor}`. The whole registry, merged ones included (`mergedInto` set), so the app can keep a full copy.                                                                                     |
| `GET /api/players/check?name=&email=`           | –                                                       | `200 {status: 'match', player: PlayerSummary}` or `{status: 'email_taken' \| 'name_taken' \| 'new'}` (§6.8; 60/min per user)                                                                                                                                           |
| `POST /api/players`                             | `NewPlayer`                                             | `201 Player`. **Idempotent by `id`** (same id and same body → `200`; same id, different body → `409 id_conflict`). `409 same_player {player}`, `409 email_taken`, `409 name_taken`, `422 invalid {fields: {email}}` (§6.8).                                                                                                                    |
| `PATCH /api/players/{id}`                       | `{displayName?, email?, firstName?, lastName?, emoji?, color?}` | `200 Player`. Permissions per §2 (email: admin, creator, linked user). `409 name_taken`, `409 email_taken`.                                                                                                                                                                    |
| `GET /api/players/{id}`                         | –                                                       | `200 {player, totals: PlayerStats (all time), achievements: Achievement[], rank: {global: number\|null, outOf}}`. `rank` is all-time on the default board (average per round, more than 50 rounds), null when unranked |
| `GET /api/players/{id}/stats?from=&to=`         | –                                                       | `200 PlayerStats`                                                                                                                                                                                                      |
| `GET /api/players/{id}/games?from=&to=&cursor=` | –                                                       | `200 {items: GameSummary[], nextCursor}`                                                                                                                                                                               |
| `GET /api/players/{id}/calendar?year=`          | –                                                       | `200 {days: {"2026-10-31": rounds, …}}` (`APP_TZ` days) for the heatmap                                                                                                                                                |
| `GET /api/players/{a}/vs/{b}?from=&to=`         | –                                                       | `200 HeadToHead`                                                                                                                                                                                                       |

```ts
Player = { id, displayName, email, photoUrl, firstName, lastName, emoji, color, createdBy, linkedUserId, mergedInto, createdAt, updatedAt }
// email: only for the admin, the creator and the linked user; null for everyone else
PlayerSummary = Player & { games: number, lastPlayedAt: number|null, createdByName: string }  // email always null
PlayerStats = { points, rounds, made, missed, accuracy, avgPerRound, games, gamesEnded, wins, winRate,
                places: {1: n, 2: n, 3: n}, bestRound, worstRound, longestHot, longestCold, hookHits,
                bestGame: GameSummary|null, bidHistogram: {"0": {made, total}, …}, byCards: {"3": {made, total}, …},
                byTrump: {spades: {made, total}, …}, asDealer: {rounds, made} }
HeadToHead = { a: PlayerSummary, b: PlayerSummary, games, aAhead, bAhead, ties,  // finishing above the other
               aPoints, bPoints, lastGames: GameSummary[], biggestWin: {by, game}|null }
GameSummary = { id, owner: {id, name}, createdAt, endedAt|null, status, players: [{playerId, name, emoji,
                color, photoUrl, points, place}], rounds, lengthMode, live: boolean }
```

- `createdByName` and `owner.name` are the display name of that user's linked player. In public responses `owner.id` is `null`.
- `accuracy` and `winRate` are fractions from 0 to 1. Registry display names are 1–14 characters.

### 6.4 Games

| Method & path                                | Body / query              | Success                                                                                                                                  |
| -------------------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `PUT /api/games/{id}`                        | the game payload (§5.2)   | `200 {rev, earned: Achievement[]}`, where `earned` is everything newly earned by this upload                                             |
| `GET /api/games?updatedSince=&cursor=&mine=` | –                         | `200 {items: GameSummary[], nextCursor}`, all non-deleted games, newest first. With `updatedSince`, games deleted since then come as `{id, deleted: true, updatedAt}`. |
| `GET /api/games/{id}`                        | –                         | `200 {game: payload, summary: GameSummary, achievements: Achievement[]}`                                                                 |
| `DELETE /api/games/{id}`                     | –                         | `204`. Owner or admin; soft delete; leaderboards and achievements recomputed.                                                            |
| `POST /api/games/{id}/links`                 | `{kind: 'live'\|'recap'}` | `201 {token, url, qrSvgUrl}`. URL `https://play.vectanix.com/l/<token>` or `/r/<token>`. Returns the existing live link if there is one. |
| `DELETE /api/games/{id}/links/{kind}`        | –                         | `204` (revoke)                                                                                                                           |
| `GET /api/games/{id}/links/{kind}/qr.svg`    | –                         | the QR as `image/svg+xml`                                                                                                                |

Upload rules for `PUT /api/games/{id}`:

0. **Identity:** the path id must equal the payload `id` (`422 invalid_game {reason: "id"}`). A game that was deleted → `410 gone`.

1. **Stale check:**
   - `rev` ≤ stored `rev` and the payload hash differs → `409 stale {server: payload}`.
   - The same hash → `200` with nothing earned (an idempotent retry).
2. **Permissions:** the first upload sets `owner_id`. Later uploads are allowed for the owner or an admin; anyone else gets `403`.
3. **Unknown players:** every `players[].playerId` must exist (after following `merged_into`), or `422 unknown_player {playerIds}`. Merged ids are rewritten to the target in the stored payload, and the response then includes `remap: {old: new}`.
4. **Validation (§7.2):** a failure is `422 invalid_game {reason, roundIndex?}`.
5. **Store and derive:** store the payload, rebuild the derived rows, recompute achievements for the players in this game (§8), and set `ended_at`.
6. **After commit:** write `activity` rows and run `NOTIFY live_<gameId>`.

### 6.5 Boards

| Method & path                                       | Success                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/leaderboard?from=&to=&metric=&minRounds=` | `200 {rows: [{rank: number\|null, qualified: boolean, player: PlayerSummary, value, points, games, rounds, wins, accuracy, longestHot, rankDelta: number\|null}], minRounds, generatedAt}`. `metric` ∈ `avgPerRound` (**default**), `points, wins, winRate, accuracy, bestRound, longestHot, games, rounds`. `minRounds` defaults from the range length (§2). A row is `qualified` when `rounds > minRounds`. For rate metrics (`avgPerRound, accuracy, winRate`): qualified rows come first with ranks, then unqualified rows with `rank: null`; both are sorted by `value` desc, ties broken by points, then rounds. For count metrics every row is qualified. `value` is exact (`avgPerRound` = points ÷ rounds as a float; the app rounds it for display). Ties on the exact value share a rank (1, 1, 3). `rankDelta` compares with the previous window of the same length (null when unranked in either). Supports `ETag` / `If-None-Match` (§15). |
| `GET /api/awards/daily?date=YYYY-MM-DD`             | `200 {date, final: boolean, awards: [{code, player: PlayerSummary, value, gameId?, roundIndex?}]}`. `final` is false for today (provisional).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `GET /api/seasons/{yyyy-mm}`                        | `200 {season, final, standings: rows like the leaderboard, awards: [...]}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `GET /api/seasons`                                  | `200 [{season, champion: PlayerSummary\|null}]`, newest first                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `GET /api/wrapped/{year}/{playerId}`                | `200 Wrapped` (§8.5)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `GET /api/activity?cursor=`                         | `200 {items: [{id, kind: 'achievement'\|'game_finished'\|'season_closed'\|'player_joined', player?, game?, data, createdAt}], nextCursor}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `GET /api/achievements?playerId=&cursor=`           | `200 {items: Achievement[], nextCursor}`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |

```ts
Achievement = {
  id,
  code,
  playerId,
  gameId: string | null,
  roundIndex: number | null,
  period: string | null,
  earnedAt,
  data: object,
}; // data e.g. {n: 5} for streaks, {value: 120}
```

### 6.6 Public (no login)

| Method & path                          | Success                                                                                                                                                                                                                     |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/public/live/{token}`         | `200 {game: payload, summary, updatedAt, scorerSeenAt}`. `410` once revoked.                                                                                                                                                |
| `GET /api/public/live/{token}/events`  | SSE. `event: game` with the same JSON on every upload; `event: ping` every 25 s; `event: revoked`. Clients reconnect with `Last-Event-ID` (= `rev`).                                                                        |
| `GET /api/public/recap/{token}`        | `200 {game: payload, summary, achievements}`. `410` once revoked. Player first/last names are **never** included in public responses.                                                                                       |
| `GET /api/public/recap/{token}/og.png` | 1200×630 PNG: title, top 3 with emoji and points, rounds, date. Cached on disk by `(token, rev)`.                                                                                                                           |
| `GET /r/{token}` and `GET /l/{token}`  | A tiny HTML page with Open Graph and Twitter meta (title, description, image) and `<meta http-equiv="refresh" content="0;url=/?recap=<token>">` (or `?live=`), so WhatsApp shows a rich preview and people land in the app. |

### 6.8 Player identity and photos (contract change, 2026-10-31)

- **Email:** every player has one; it identifies the person (players without accounts too) and is never used to send mail. Unique, case-insensitive, stored trimmed and lower-cased, shape `x@y.z` with no spaces. Required for new players; older players may have none until someone adds it. Only the admin, the player's creator and the player's linked user see it; everyone else and every public endpoint get `email: null`. Display names stay globally unique.
- **Same player = name AND email both match; never reveal another player.** `GET /api/players/check?name=&email=` (signed in, 60/min per user): name and email of one live player → `{status: 'match', player: PlayerSummary}`; otherwise, in this order, `{status: 'email_taken'}` / `{status: 'name_taken'}` / `{status: 'new'}`. Only a full match returns a player. `POST /api/players` answers a full match with `409 same_player {player}` (the app then uses that id), otherwise `409 email_taken` / `409 name_taken` with no details; missing or malformed email → `422 invalid {fields: {email}}`.
- **Suggestions** ("players I added, or people I've played with") are computed in the app from `createdBy` and its games; `GET /api/players` still returns the whole registry.
- **Invites:** `GET /api/auth/invites/{token}` → `player` is the invite's player, else the unlinked player whose email equals the invite email, else null. Accepting: a new player always gets the invite's email; a claimed player with no email gets it. Errors `409 name_taken`, `409 email_taken`, `409 player_linked` carry no player details.
- **Photos:** `PUT /api/players/{id}/photo` (raw `image/webp`, `image/jpeg` or `image/png`, ≤ 2 MB; admin, creator or linked user, else 403) re-encodes in a thread to a 256x256 WebP (EXIF/GPS stripped, decompression capped, ≤ ~60 KB), bumps `photo_rev` and `updatedAt`, returns `200 {photoUrl}`; 415 other types, 413 too big. `DELETE` → 204, back to the emoji. `GET /api/players/{id}/photo?v=` is public, `image/webp`, `Cache-Control: public, max-age=31536000, immutable`, 404 without a photo. `photoUrl = "/api/players/{id}/photo?v={photo_rev}"` or null: on Player, on every `GameSummary.players[]` item, in public live and recap responses; the recap OG image uses the photo when there is one.

---

## 7. Domain port (Python `domain/`)

The app's rules live in TypeScript (`src/domain`). The backend reimplements exactly what it needs as pure functions. **Parity is proven by golden fixtures exported from the app** (§12.1). When a fixture and the code disagree, the fixture wins, and both teams are told.

### 7.1 Rules to port

- `TRUMPS = ['spades','diamonds','clubs','hearts']`; the trump of round `i` is `TRUMPS[i % 4]`.
- `max_for(n, decks) = min(10, floor(52*decks/n))`. Players: 3–7 with one deck, 3–10 with two.
- `cycle_len(max) = 2*(max-2)`. `cards_for(i, max)`: with `p = i % cycle_len`, return `3+p if p < max-2 else max-(p-(max-2))`. This gives 3 → max → 3, with max and 3 each repeated once.
- Round `i`: dealer `= (firstDealerIndex + i) % n`; first player `= (dealer + 1) % n`. Bid order runs from the first player round the table to the dealer.
- Total rounds: endless = ∞; `1cycle` = `cycle_len`; `2cycles` = `2*cycle_len`.
- **Score:** `v = max(bid, 1) * 10`. Made `+v`, missed `-v`. A game total is the sum over done rounds. Totals are never stored in the payload.
- **Dealer's hook:** the dealer may not bid `cards - sum(other bids)` when that is between 0 and `cards`. So in a done round, `sum(bids) != cards`. (`hookHits` counts the dealer's attempts to bid the blocked number.)
- **Results must add up** (`fits`): with `left = cards - sum(bids of made)`, `left >= 0` is required. Then `left` tricks must be shareable among the players who missed so that none of them wins exactly their own bid. Port `shareOut` (a subset-sum over 0..left excluding each bid).
- **Exact tricks (optional):** if `tricks` is present, `sum == cards`, each `0..cards`, and `results[p] == 'made'` iff `tricks[p] == bids[p]`.
- **Places:** competition ranking on totals (1, 1, 3); ties keep seat order for display.

### 7.2 Upload validation (each failure has a `reason` code)

| Reason        | Check                                                                                                                          |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `players`     | 3–10 players, decks-compatible, unique local ids, unique `playerId`s, names 1–14 characters                                    |
| `max_cards`   | `maxCards == max_for(n, decks)`                                                                                                |
| `round_plan`  | `index`, `cards`, `trump`, `dealerIndex` and `firstPlayerIndex` match the formulas for every round                             |
| `round_order` | Only the last round may be unfinished. No more rounds than `lengthMode` allows. `status = finished` requires every round done. |
| `bids`        | In a done round every player has an integer bid `0..cards`, and `sum != cards`                                                 |
| `results`     | In a done round every player has `made`/`missed` and `fits()` holds                                                            |
| `tricks`      | As in §7.1                                                                                                                     |
| `times`       | `createdAt ≤ doneAt ≤ updatedAt + 5 min`, and nothing more than 10 min in the future (server time)                             |

### 7.3 Derivation on every upload

- **`round_scores`**, one row per player per done round. `run_hot` and `run_cold` are the current made and missed run lengths **within that game** after that round.
- **`game_players`**: totals, place (only once the game has ended), `won` (place 1, ties included), made/missed, best/worst round, longest runs, and the hook hits charged to them as dealer.
- **`ended_at`** is `finishedAt` (or `updatedAt` for legacy games) when `status = finished`. Otherwise it is null, until auto-end sets it.

---

## 8. Achievements, awards, seasons, wrapped

The app shows the same catalogue live during play (from its own TypeScript engine); the server is the record. Codes, names and emoji are shared: the app renders names from `code`, and the server uses them in previews and the activity feed.

- **"Made"** means made the bid in a round; **"run"** means consecutive done rounds within one game.
- Ties earn together.
- Each row is earned at most once per `(player, code, game, round, period)`.

### 8.1 Round moments (live in the app; earned at the round's `doneAt`)

| Code                                 | Emoji · name                                               | Rule                                                                 |
| ------------------------------------ | ---------------------------------------------------------- | -------------------------------------------------------------------- |
| `hot_3` `hot_5` `hot_8` `hot_12`     | 🔥 On fire · 🚀 Unstoppable · 🐐 G.O.A.T. · 💎 Untouchable | A made run reaches 3/5/8/12 (`data.n`). A new run can earn it again. |
| `cold_3` `cold_5` `cold_8` `cold_12` | 🥶 Ice cold · 💀 Cursed · 🪦 Rest in peace · 🧟 Zombie     | A missed run reaches 3/5/8/12                                        |
| `grand_slam`                         | 💯 Grand slam                                              | Made a bid equal to the cards, with cards ≥ 5                        |
| `ninja`                              | 🥷 Ninja                                                   | Made a bid of 0 in a round of ≥ 7 cards                              |
| `high_roller`                        | 🎰 High roller                                             | Made a bid ≥ 5                                                       |
| `peak_performer`                     | 🏔️ Peak performer                                          | Made a bid ≥ 3 in a round where `cards == maxCards`                  |
| `lone_wolf`                          | 🐺 Lone wolf                                               | The only player who made it that round (table ≥ 4)                   |
| `odd_one_out`                        | 🫠 Odd one out                                             | The only player who missed that round (table ≥ 4)                    |
| `hooked`                             | 🪝 Hooked                                                  | As dealer, tried the blocked number (`hookHits ≥ 1`)                 |
| `dealers_revenge`                    | 😈 Dealer's revenge                                        | Hooked as dealer, then made the bid anyway                           |

### 8.2 Game finish (when the game ends; earned at `ended_at`)

| Code           | Emoji · name     | Rule                                                                        |
| -------------- | ---------------- | --------------------------------------------------------------------------- |
| `winner`       | 🏆 Winner        | Place 1                                                                     |
| `wire_to_wire` | 🏁 Wire to wire  | Place 1 (ties included) after every done round; ≥ 6 rounds                  |
| `comeback`     | 🔄 Comeback king | Last (ties included) after some round at or past the halfway point, and won |
| `photo_finish` | 📸 Photo finish  | Won (not tied) by ≤ 10 points                                               |
| `runaway`      | 🏃 Runaway       | Won by ≥ 100 points                                                         |
| `sharpshooter` | 🎯 Sharpshooter  | ≥ 80% bids made; ≥ 10 rounds                                                |
| `perfect_game` | 🌟 Perfect game  | Made every bid; ≥ 6 rounds                                                  |
| `wooden_spoon` | 🥄 Wooden spoon  | Last place                                                                  |
| `zero_hero`    | 0️⃣ Zero hero     | Finished on exactly 0; ≥ 6 rounds                                           |
| `marathon`     | 🏃‍♂️ Marathon      | Everyone at a game of ≥ 30 rounds                                           |
| `full_house`   | 🏠 Full house    | Everyone at a 10-player game                                                |
| `night_owl`    | 🦉 Night owl     | Everyone at a game that ended 01:00–05:00 `APP_TZ`                          |

### 8.3 Career (across a player's ended games, in `ended_at` order)

| Code                                 | Emoji · name                                | Rule                                                    |
| ------------------------------------ | ------------------------------------------- | ------------------------------------------------------- |
| `first_win`                          | 🥇 First win                                | First game won                                          |
| `back_to_back` `hat_trick` `dynasty` | ✌️ Back to back · 🎩 Hat-trick · 🦸 Dynasty | Won 2 / 3 / 5 games in a row                            |
| `spoon_collector`                    | 🥄 Spoon collector                          | Last in 3 games in a row                                |
| `podium_regular`                     | 🥉 Podium regular                           | Top 3 in 5 games in a row                               |
| `personal_best`                      | 📈 Personal best                            | A new highest game total, after ≥ 3 earlier ended games |
| `games_10/50/100/250/500`            | 🎲 N games                                  | Ended games played                                      |
| `rounds_100/500/1000/5000`           | 🔁 N rounds                                 | Done rounds played                                      |
| `made_100/500/1000`                  | ✅ N bids made                              |                                                         |
| `points_1000/5000/10000`             | 💰 N points earned                          | Sum of **positive** round scores                        |
| `crew_10` `crew_25`                  | 👥 Crew                                     | Shared a table with 10 / 25 different players           |
| `rivalry_20`                         | ⚔️ Rivalry                                  | 20 games with the same other player (`data.with`)       |

Repeat rules (approved 2026-10-31; updated the same day to the app's engine, which the golden fixtures prove):

- **Every career row carries the `game_id` where it was earned**; `rounds_*`, `made_*` and `points_*` also carry the `round_index` of the round that crossed the line.
- **Repeatable streaks** (`back_to_back`, `hat_trick`, `dynasty`, `spoon_collector`, `podium_regular`) and `personal_best` earn again on each new streak or best.
- **Milestones** (`first_win`, `games_*`, `rounds_*`, `made_*`, `points_*`, `crew_*`) are earned once per player.
- **`rivalry_20`** is earned once per rival (`data.with`), on the game where the pair reaches 20; when two rivalries reach 20 in one game, only the alphabetically first earns that game.

### 8.4 Daily awards (per `APP_TZ` day; `period = 'YYYY-MM-DD'`; provisional until the day closes)

| Code          | Emoji · name         | Winner                                               |
| ------------- | -------------------- | ---------------------------------------------------- |
| `day_star`    | ⭐ Player of the day | Most points from rounds done that day                |
| `day_bang`    | 💥 Round of the day  | Highest single round score (the earliest wins a tie) |
| `day_ouch`    | 🤕 Ouch of the day   | Lowest single round score                            |
| `day_hot`     | 🔥 Hot hand          | Longest made run reached that day                    |
| `day_ice`     | 🧊 Ice bucket        | Longest missed run reached that day                  |
| `day_sharp`   | 🎯 Sharpest          | Best accuracy, minimum 10 rounds                     |
| `day_grinder` | ⏱️ Grinder           | Most rounds played                                   |
| `day_hooked`  | 🪝 Most hooked       | Most hook hits as dealer                             |

A timer at 00:05 `APP_TZ` finalises the day before. During the day, `GET /api/awards/daily` computes awards on the fly.

### 8.5 Seasons and Wrapped

- **Seasons** are calendar months in `APP_TZ`; points come from round `done_at`. Standings use the default board rule: average points per round, and a player qualifies with **more than 50 rounds** that month. Awards (qualified players only):
  - `season_champion` 👑, `season_runner_up` 🥈, `season_third` 🥉
  - `season_sharpest` 🎯 (accuracy, minimum 30 rounds)
  - `season_iron` 🦾 (most games)
  - `season_climber` 📈 (biggest rank rise compared with last month; must qualify in both)
- A timer on the 1st at 00:10 closes the previous month and writes the awards and an activity row. The current month is live.
- **Wrapped** (`GET /api/wrapped/{year}/{playerId}`):
  - Totals, best month, best day, favourite trump (best accuracy), nemesis (most losses against) and best partner (most games together).
  - Biggest round, longest run, achievements earned, the top 3 days by rounds, and a rank among all players.
  - It is plain JSON; the app animates it as story slides.

### 8.6 Recompute strategy

- **On upload:** recompute round and game achievements for that game. Recompute career achievements for its players, which is cheap with per-player SQL over `game_players` / `round_scores`.
- **On merge, delete or auto-end:** recompute career achievements for the affected players. Delete rows that no longer hold (an edit can un-earn something) and keep the ones that still do (stable `earned_at`).
- `earned` in the upload response lists only rows **inserted** by that upload. The app uses it for the activity feed and to update badges on other devices; live celebrations come from the app's own engine.

---

## 9. Jobs (systemd timers → `python -m app.cli <cmd>`)

| Command        | When                    | Does                                                                                                                                                                                                               |
| -------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `auto-end`     | Every 15 min            | Sets `ended_at = last doneAt` (and `auto_ended = true`) on active games idle for 24 h with ≥ 1 done round, then derives game achievements. A later upload of the game reopens it (`ended_at` cleared, recomputed). |
| `close-day`    | 00:05 `APP_TZ`          | Finalises yesterday's daily awards and writes activity rows                                                                                                                                                        |
| `close-season` | 1st of the month, 00:10 | Finalises the season and its awards                                                                                                                                                                                |
| `prune`        | Nightly                 | Deletes expired sessions, invites, resets and rate-limit windows, and OG cache files for revoked links                                                                                                             |
| `seed-admin`   | Once, at install        | Creates `jaydhakan11@gmail.com` (role admin, no password) and prints a one-time **invite link** for setting the password                                                                                           |
| `backup`       | Nightly 03:00           | `pg_dump -Fc` → rotate 14 daily / 8 weekly → copy off the VPS (rclone remote set in env); writes `lastBackupAt`                                                                                                    |

---

## 10. Security

- argon2id with library defaults. Constant-time token comparison via hashes. Generic login errors (`bad_credentials` for both a wrong email and a wrong password).
- **Rate limits** (Postgres `rate_limits`):
  - Login: 10 per 10 min per IP, and 10 per 10 min per email.
  - Token endpoints: 30 per 10 min per IP.
  - `PUT /games`: 120 per minute per user.
- **CSRF:** `SameSite=Lax`, plus the `X-Kachuful: 1` header, plus an `Origin` check on every non-GET request.
- **Sessions:** 180-day rolling expiry, refreshed at most once a day. Logout, password change and deactivation revoke sessions.
- **Public endpoints** never return emails, first/last names or user ids, only display names, emoji and colours.
- Request size limit 1 MB; JSON depth limits; all SQL through SQLAlchemy.
- Security headers come from nginx. CSP is unchanged for the app; `/r/` and `/l/` pages are static.
- **Logs:** structured JSON, with no passwords, tokens or cookies in them.

---

## 11. Deploy and run

```nginx
# inside the existing server block for play.vectanix.com
location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 1m;
}
location ~ ^/api/public/live/[^/]+/events$ {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;
    proxy_read_timeout 1h;
    proxy_set_header Connection '';
    proxy_http_version 1.1;
}
location ~ ^/(r|l)/ { proxy_pass http://127.0.0.1:8000; }
```

- **systemd:** `kachuful-api.service` (uvicorn with `--proxy-headers`), plus timers for the jobs in §9.
- **Environment:** `DATABASE_URL`, `APP_ORIGIN=https://play.vectanix.com`, `APP_TZ=Asia/Kolkata`, `SEED_ADMIN_EMAIL=jaydhakan11@gmail.com`, `SESSION_DAYS=180`, `BACKUP_REMOTE`, `OG_CACHE_DIR`, `LOG_LEVEL`.
- **Environments:** local (Docker Compose Postgres), **staging** (`staging.play.vectanix.com` or a port on the same VPS, with its own database), production.
- **Migrations:** `alembic upgrade head` runs in the deploy job before the restart. Every migration is reversible or has a written rollback note.
- **Health:** `GET /api/health` returns `{ok, db, version}` for an uptime monitor.
- **Fonts for OG images:** Bricolage Grotesque and DM Sans (the app's fonts) plus Noto Color Emoji, bundled in the repo.

---

## 12. Testing

### 12.1 Golden fixtures (parity with the app)

The app repo exports `contract/fixtures/*.json`, which is committed to this repo under `tests/fixtures/` and refreshed when the app's rules change. Each fixture is one of:

- `{ kind: 'game', name, game, expect: { valid, reason?, totals, places, gamePlayers, roundScores, achievements } }`
- `{ kind: 'set', name, games, tz, expect: { leaderboard: {...per metric/range}, daily, season, career } }`

A parametrised pytest asserts the Python `domain/` reproduces every expectation exactly. **Until the first export arrives**, write fixtures by hand from §7–§8 and replace them when the real ones land.

### 12.2 Other tests

- **API tests:** httpx against the app with a real Postgres (transaction per test). Every endpoint, every error code, every permission rule.
- **Property tests (`hypothesis`):** random valid games always validate; any single corrupted field gets the right `reason`.
- **Concurrency:** two uploads of the same game at once → one wins, the other gets `409 stale`, and no duplicate derived rows.
- **SSE:** a viewer connected before an upload receives the `game` event, and revoking a link sends `revoked`.
- **Load sanity:** the leaderboard for a year of data (about 50 players, 5,000 games) answers in < 150 ms with the indexes.

---

## 13. Milestones (build in this order; each ends green and deployed to staging)

1. **B0. Skeleton:** repo, `uv`, FastAPI app, settings, Docker Compose Postgres, Alembic, CI, `GET /api/health`, deploy to staging behind nginx.
2. **B1. Auth + admin:** users, sessions, login/logout/me, `seed-admin`, invites, resets, admin users screen API, password rule, rate limits, CSRF.
3. **B2. Players:** registry, `name_key` uniqueness, check, create (idempotent), patch permissions, merge.
4. **B3. Games:** `PUT` with stale/idempotency checks, storage, `GET` list and detail, delete. Validation with hand-written fixtures.
5. **B4. Domain port + derived tables:** `round_scores`, `game_players`, `ended_at`, `auto-end`. Swap in the app's golden fixtures once they arrive.
6. **B5. Boards:** `player_day` rollups, leaderboard (all metrics, ranges, the ranked/unranked rule, rank deltas, ETags), player stats, player games, calendar, head-to-head, `bootstrap`.
7. **B6. Achievements:** the full catalogue in §8.1–8.3, recompute strategy, `earned` in the upload response, achievements list, activity feed.
8. **B7. Daily, seasons, Wrapped:** close-day and close-season jobs and endpoints, Wrapped.
9. **B8. Live + recap:** share links, SSE fan-out via `LISTEN/NOTIFY`, public endpoints, OG image, QR SVG, `/r` and `/l` pages.
10. **B9. Hardening:** backup job and a rehearsed restore, prune, security headers check, load sanity, production deploy, and `seed-admin` on production.
11. **B10. Handoff:** write `HANDOFF.md` (§16) and run the performance checks in §15 against staging with the load dataset. Tell Jay it's ready.

Hand-offs to the frontend: B1 unblocks sign-in screens, B2 the name dialog, B3/B4 sync, B5–B7 boards, B8 sharing. Keep staging running with seed data (`python -m app.cli seed-demo`) so the app can test against it.

---

## 14. Working agreement

- **The contract in §6 is frozen** once B1 starts. Changes need a note to Jay and an update to both plans.
- Never store or log plain tokens or passwords.
- Every endpoint gets tests before it is called done.
- Say plainly when something is stubbed, mocked or unverified.
- Conventional Commits, one concern each. Don't push or touch CI/production unless Jay asks.
- Project standards live in this repo's `CLAUDE.md`; follow them.

---

## 15. Performance (the app must feel instant)

The app renders from its own IndexedDB cache first and refreshes in the background. The server's job is to answer every refresh fast and to send nothing when nothing has changed.

### 15.1 Budgets (p95 server time on staging, with the load dataset of 50 players, 5,000 games and about 80,000 rounds)

| Endpoint                        | Budget                                                        |
| ------------------------------- | ------------------------------------------------------------- |
| `GET /api/auth/me`, `bootstrap` | 30 ms / 80 ms                                                 |
| `PUT /api/games/{id}`           | 120 ms, including derive and achievements                     |
| `GET /api/leaderboard`          | 60 ms (any range, any metric)                                 |
| Player page calls               | 80 ms each                                                    |
| `GET /api/games` (list)         | 50 ms                                                         |
| Live update                     | under 300 ms from `PUT` to the viewer receiving the SSE event |
| Any `304 Not Modified`          | 10 ms                                                         |

### 15.2 Indexes (each comes with a test that runs `EXPLAIN` on the real query and asserts the index is used)

| Table             | Index                                                                                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `users`           | unique `email` (citext)                                                                                                                                                                                            |
| `sessions`        | unique `token_hash`; `(user_id)`; `(expires_at)`                                                                                                                                                                   |
| `invites`         | unique `token_hash`; `(email) WHERE accepted_at IS NULL AND revoked_at IS NULL`                                                                                                                                    |
| `password_resets` | unique `token_hash`                                                                                                                                                                                                |
| `players`         | unique `(name_key) WHERE merged_into IS NULL`; `(updated_at, id)` for the sync cursor; GIN `name_key gin_trgm_ops` (`pg_trgm`) for search                                                                          |
| `games`           | `(updated_at, id) WHERE deleted_at IS NULL`; `(owner_id, updated_at DESC)`; `(ended_at) WHERE deleted_at IS NULL`; `(updated_at) WHERE status = 'active' AND ended_at IS NULL AND deleted_at IS NULL` for auto-end |
| `game_players`    | `(player_id) INCLUDE (game_id, points, place, won, rounds)`                                                                                                                                                        |
| `round_scores`    | `(player_id, done_at) INCLUDE (points, outcome, run_hot, run_cold, hook_hits)`; `(done_at)`, BRIN once over 1M rows                                                                                                |
| `player_day`      | primary key `(player_id, day)`; `(day) INCLUDE (player_id, rounds, points, made, wins)`                                                                                                                            |
| `achievements`    | `(player_id, earned_at DESC)`; `(code, period)`; the unique key in §5.1                                                                                                                                            |
| `activity`        | `(created_at DESC, id DESC)`                                                                                                                                                                                       |
| `share_links`     | `(game_id, kind) WHERE revoked_at IS NULL`                                                                                                                                                                         |

### 15.3 Precompute on write, never on read

- **Every upload** rebuilds, in one transaction:
  - `round_scores` and `game_players` for that game.
  - `games.summary`: list endpoints read this and never parse `payload`.
  - `player_day` rows for the days the game touches. A day is rebuilt by summing that day's `round_scores`, so edits and undo stay correct.
- **Leaderboards over whole `APP_TZ` days** (Today, 7 days, months, All time, and any custom range on day boundaries) read `player_day` only. Ranges that cut through a day read `round_scores` for the partial edge days and `player_day` for the rest.
- **Closed days and seasons are immutable.** Their awards are stored and served with `Cache-Control: public, max-age=31536000, immutable`.

### 15.4 Cache and transfer

- A global **`data_version`** counter (one row) is bumped by every upload, delete, merge or auto-end. Read endpoints send `ETag: "<data_version>-<params hash>"` and answer `304` to a matching `If-None-Match` before touching other tables.
- An in-process LRU (per worker, 30 s TTL, keyed by ETag) holds leaderboard, awards and player responses. No Redis.
- nginx: `gzip on; gzip_types application/json image/svg+xml;`, HTTP/2, keep-alive.
- Database:
  - asyncpg pool of 10 per worker.
  - Prepared statements on.
  - `statement_timeout = 2s` for API requests, 60 s for jobs.
- **No N+1 queries:** a request makes a fixed number of queries whatever the data size (tests assert the query count on the main endpoints).
- SSE: **one `LISTEN` connection per worker** feeds every subscriber on that worker from memory. Each event is the summary plus the payload, once.
- Postgres tuning for a small VPS: `shared_buffers` about 25% of RAM, `effective_cache_size` about 60%, `work_mem` 16 MB, `random_page_cost` 1.1 on SSD. Autovacuum stays on.

### 15.5 Proving it

- `python -m app.cli seed-load` builds the load dataset on staging.
- `tests/perf/` (marked `perf`, run on demand) measures p95 for each endpoint in §15.1 and fails over budget.
- The numbers go into `HANDOFF.md`.

---

## 16. Handoff to the frontend (`HANDOFF.md`)

Start `HANDOFF.md` at B1 and update it at the end of every milestone. When B10 is done it must let the frontend verify everything **without reading the backend code**:

1. **Status table:** every endpoint in §6, marked done / tested / deviations (and why). Any approved contract change, with its date.
2. **Environments:** staging and production base URLs, how to reset staging (`seed-demo`, `seed-load`), and the deployed version (`GET /api/health`).
3. **Test accounts on staging:** an admin and two members with known passwords, plus a pending invite link and a reset link to try.
4. **Copy-paste checks:** `curl` commands with a cookie jar for every flow:
   - log in, `bootstrap`, create a player, a name clash
   - upload a game, then a stale upload
   - leaderboard with an ETag `304`
   - live SSE (`curl -N`)
   - recap OG image
5. **Rules as built:** rate-limit numbers, session length, password rule, timezone handling, auto-end timing, the leaderboard minimum-rounds table.
6. **Parity:** which `contract/fixtures` version is checked in and that all of it passes (or exactly which cases fail).
7. **Performance:** measured p95 per endpoint against §15.1, with the dataset size.
8. **Known gaps and follow-ups**, said plainly.
