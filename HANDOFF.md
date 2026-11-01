# Kachuful API: handoff to the frontend

**Complete as of B10 (2026-10-31), plus the player identity and photos change of the same day
(§5.7).** Everything in plan §6 is built and tested. **Where this file
and a `plan-*.md` disagree, this file is right**: the plans in the app repo predate the approved
contract changes in §7. The backend repo's `plan-backend.md` §6 is up to date.

There is **no staging**: one production environment (`play.vectanix.com`) plus local runs.
Interactive docs (every route, every field, every error) are served at `/api/docs` on local runs,
not on production.

---

## 1. Status

| Milestone | State |
| --- | --- |
| B0 skeleton, CI, deploy pipeline | done |
| B1 auth + admin | done |
| B2 players | done; **2026-10-31: player emails, the name + email same-player rule and photos added (§5.7)** |
| B3 games (sync) | done |
| B4 domain port + derived tables + auto-end | done; all golden fixtures pass |
| B5 boards, stats, bootstrap, ETags | done; SQL leaderboards equal the app's for every metric × range |
| B6 achievements + activity | done; stored rows equal the app's full achievement set |
| B7 daily awards, seasons, Wrapped | done; daily and season awards equal the app's |
| B8 live + recap | done; SSE tested on a real server with 2 workers |
| B9 hardening | done locally: backup + **rehearsed restore**, prune, load dataset, perf budgets, nginx/systemd |
| B10 handoff | this file |
| Production deploy + `seed-admin` | **done 2026-11-01**: `https://play.vectanix.com/api/health` → `{"ok":true,"db":true}`, security headers present, job timers running, admin invite issued. The CD job deploys every push to `main` |

Tests: 372 automated tests (`make verify`: ruff, mypy --strict, pytest against real Postgres 16),
plus the perf suite (`make perf`).

### Every endpoint

All are **done and tested**. Notes point to deviations or additions (§7).

| Endpoint | Notes |
| --- | --- |
| `GET /api/health` | 503 (same body) when the database is down |
| `POST /api/auth/login` | |
| `POST /api/auth/logout` | always 204 |
| `GET /api/auth/me` | |
| `GET /api/bootstrap` | ETag / 304 |
| `GET /api/auth/invites/{token}` | **2026-10-31: also finds the unlinked player with the invite email** |
| `POST /api/auth/invites/{token}` | also `409 id_conflict`, `422 unknown_player`, `422 invalid` (§7); **2026-10-31: invite email given to the player, `409 email_taken`, no player details in errors** |
| `GET /api/auth/resets/{token}` | |
| `POST /api/auth/resets/{token}` | `403 inactive` for a switched-off account |
| `POST /api/me/password` | |
| `GET /api/admin/users` | `invite: {email, expiresAt}` (no url: approved) |
| `POST /api/admin/invites` | |
| `DELETE /api/admin/invites/{email}` | 204 even when none is pending (approved) |
| `POST /api/admin/users/{id}/reset-link` | |
| `PATCH /api/admin/users/{id}` | `409 last_admin` (approved) |
| `POST /api/admin/players/{id}/merge` | also `422 invalid` when both sat at one table (§7); **2026-10-31: the target keeps its email and photo, or takes the merged player's** |
| `GET /api/admin/overview` | |
| `GET /api/players` | |
| `GET /api/players/check?name=&email=` | **changed 2026-10-31**: `{status: match \| email_taken \| name_taken \| new}` (§5.7) |
| `POST /api/players` | 201 new / 200 same id+body / `409 id_conflict`; **2026-10-31: email required, `409 same_player` / `email_taken` / `name_taken` (§5.7)** |
| `PATCH /api/players/{id}` | **2026-10-31: also `email`, `409 email_taken`** |
| `PUT /api/players/{id}/photo` | **new 2026-10-31** (§5.7) |
| `DELETE /api/players/{id}/photo` | **new 2026-10-31** |
| `GET /api/players/{id}/photo?v=` | **new 2026-10-31**, public, immutable cache |
| `GET /api/players/{id}` | ETag / 304 |
| `GET /api/players/{id}/stats` | ETag / 304 |
| `GET /api/players/{id}/games` | |
| `GET /api/players/{id}/calendar?year=` | ETag / 304 |
| `GET /api/players/{a}/vs/{b}` | ETag / 304; `biggestWin.winner` added (§7) |
| `PUT /api/games/{id}` | game id rule (§5.1); `410 gone` for deleted games (approved) |
| `GET /api/games` | tombstones with `updatedSince` (approved) |
| `GET /api/games/{id}` | |
| `DELETE /api/games/{id}` | also revokes its links |
| `POST /api/games/{id}/links` | always 201, existing active link reused |
| `DELETE /api/games/{id}/links/{kind}` | |
| `GET /api/games/{id}/links/{kind}/qr.svg` | |
| `GET /api/leaderboard` | ETag / 304 |
| `GET /api/awards/daily?date=` | closed days: `Cache-Control: public, max-age=31536000, immutable` |
| `GET /api/seasons` | ETag / 304 |
| `GET /api/seasons/{yyyy-mm}` | closed seasons: immutable cache |
| `GET /api/wrapped/{year}/{playerId}` | shape in §6.6 (the plan left it open) |
| `GET /api/activity` | |
| `GET /api/achievements` | |
| `GET /api/public/live/{token}` | `summary.owner.id` is `null` (approved) |
| `GET /api/public/live/{token}/events` | SSE |
| `GET /api/public/recap/{token}` | |
| `GET /api/public/recap/{token}/og.png` | |
| `GET /r/{token}`, `GET /l/{token}` | 404 page (redirects to `/`) for unknown or revoked tokens |

---

## 2. Environments

| | Local | Production |
| --- | --- | --- |
| App + API | the app's Vite dev server (`http://localhost:5173`) proxying `/api`, `/r`, `/l` to `http://localhost:8000` | `https://play.vectanix.com` (+ `/api`) |
| Version | `GET /api/health` | `GET /api/health` → `version` is the deployed commit (12 chars) |
| Docs | `http://localhost:8000/api/docs` | none |
| Data | `seed-demo` (§3) | real games; `seed-demo`/`seed-load` refuse to run here |
| Deploys | — | every push to the backend `main`, after CI passes |

**Running the backend locally** (needs Docker and uv):

```sh
cd backend-kachuful-scorekeeper
cp .env.example .env            # APP_ORIGIN=http://localhost:5173 (the Vite dev server)
make dev                        # Postgres 16 in Docker + migrations + the API on :8000
uv run python -m app.cli seed-demo --reset     # in a second terminal: the §3 data
```

- `seed-demo --reset`: empty everything, then the demo data in §3.
- `seed-load --reset`: the performance dataset (50 players, 5,000 games, ~80,000 rounds).

---

## 3. Test accounts (local, after `seed-demo`)

| Role | Email | Password | Player |
| --- | --- | --- | --- |
| admin | `admin@kachuful.test` | `admin-demo` | Jay |
| member | `riya@kachuful.test` | `riya-demo` | Riya |
| member | `kabir@kachuful.test` | `kabir-demo` | Kabir |

Also seeded:
- six more players: Asha, Om, Neha, Dev, Zoya, Ishan. Every demo player's email is
  `<name>@kachuful.test` (`asha@kachuful.test`, …), which you can use to try the same-player check;
- 40 games over the last two weeks; the newest is still being played, so it's good for live links;
- a pending invite for `new.player@kachuful.test` and a reset link for Kabir.

`seed-demo` prints those two links. To make fresh ones yourself, sign in as the admin and call
`POST /api/admin/invites` or `POST /api/admin/users/{id}/reset-link`.

---

## 4. Copy-paste checks

I ran all of these against a local 2-worker server with the demo data (2026-10-31). Run them
against your local backend (§2) in order. They're safe to re-run: the players and the game use fixed ids, and a repeat
upload is idempotent. They need `curl` and `jq`.

```sh
BASE=http://localhost:8000
ORIGIN=http://localhost:5173    # must equal the API's APP_ORIGIN
JAR=kf.jar; rm -f $JAR
H=(-H "X-Kachuful: 1" -H "Origin: $ORIGIN" -H "Content-Type: application/json")

# 1. log in (cookie kf_session goes into the jar)
curl -s -c $JAR -b $JAR "${H[@]}" -X POST $BASE/api/auth/login \
  -d '{"email":"riya@kachuful.test","password":"riya-demo"}' | jq '{user: .user.email, player: .player.displayName}'

# 2. bootstrap, with its ETag
curl -s -D - -o boot.json -b $JAR $BASE/api/bootstrap | grep -i '^etag'
jq '{players: (.players|length), myGames: (.myGames|length), dataVersion}' boot.json

# 3. create three players (201; 200 when re-run with the same body)
for p in "11111111-1111-4111-8111-111111111111 Curl A" "22222222-2222-4222-8222-222222222222 Curl B" "33333333-3333-4333-8333-333333333333 Curl C"; do
  set -- $p
  curl -s -o /dev/null -w "%{http_code} " -b $JAR "${H[@]}" -X POST $BASE/api/players \
    -d "{\"id\":\"$1\",\"displayName\":\"$2 $3\",\"email\":\"curl.$3@example.com\",\"emoji\":\"🙂\",\"color\":\"#60a5fa\"}"
done; echo

# 4. a name clash → 409 name_taken, with no details about the holder (more in §5.7)
curl -s -b $JAR "${H[@]}" -X POST $BASE/api/players \
  -d '{"id":"44444444-4444-4444-8444-444444444444","displayName":"curl a","email":"someone@example.com","emoji":"🐸","color":"#000000"}' | jq .

# 5. upload a finished two-round game → {rev: 1, earned: [winner, wooden_spoon, ..., first_win]}
cat > game.json <<'JSON'
{"id":"g-5c0ffee0-0000-4000-8000-000000000001","syncId":"5c0ffee0-0000-4000-8000-000000000001","rev":1,"schemaVersion":3,
 "createdAt":1793000000000,"updatedAt":1793000120000,"finishedAt":1793000120000,"status":"finished",
 "direction":"cw","firstDealerIndex":0,"decks":1,"maxCards":10,"lengthMode":"endless",
 "players":[{"id":"p0","playerId":"11111111-1111-4111-8111-111111111111","name":"Curl A","emoji":"🙂","color":"#60a5fa"},
            {"id":"p1","playerId":"22222222-2222-4222-8222-222222222222","name":"Curl B","emoji":"🙂","color":"#60a5fa"},
            {"id":"p2","playerId":"33333333-3333-4333-8333-333333333333","name":"Curl C","emoji":"🙂","color":"#60a5fa"}],
 "rounds":[
  {"index":0,"cards":3,"trump":"spades","dealerIndex":0,"firstPlayerIndex":1,"bids":{"p0":1,"p1":0,"p2":1},
   "results":{"p0":"made","p1":"made","p2":"missed"},"tricks":{"p0":1,"p1":0,"p2":2},"hookHits":0,"phase":"done","doneAt":1793000060000},
  {"index":1,"cards":4,"trump":"diamonds","dealerIndex":1,"firstPlayerIndex":2,"bids":{"p0":2,"p1":1,"p2":0},
   "results":{"p0":"made","p1":"missed","p2":"made"},"tricks":null,"hookHits":0,"phase":"done","doneAt":1793000120000}],
 "ui":{"view":"summary","bidIdx":0}}
JSON
GAME=5c0ffee0-0000-4000-8000-000000000001
curl -s -b $JAR "${H[@]}" -X PUT $BASE/api/games/$GAME --data @game.json | jq '{rev, earned: [.earned[].code]}'

# 6. the same rev with a change → 409 stale, with the server's copy
jq '.ui.bidIdx = 2' game.json > stale.json
curl -s -b $JAR "${H[@]}" -X PUT $BASE/api/games/$GAME --data @stale.json | jq '{code, serverRev: .server.rev}'

# 7. leaderboard, then 304 with its ETag
ETAG=$(curl -s -D - -o /dev/null -b $JAR "$BASE/api/leaderboard?metric=points" | grep -i '^etag' | cut -d' ' -f2 | tr -d '\r')
curl -s -o /dev/null -w "%{http_code}\n" -b $JAR -H "If-None-Match: $ETAG" "$BASE/api/leaderboard?metric=points"   # 304

# 8. live link + SSE: leave this running...
LIVE=$(curl -s -b $JAR "${H[@]}" -X POST $BASE/api/games/$GAME/links -d '{"kind":"live"}' | jq -r .token)
curl -N $BASE/api/public/live/$LIVE/events
#    ...and in a second terminal (same BASE, ORIGIN, JAR, H, GAME), push rev 2: the stream prints `event: game` / `id: 2`
jq '.rev = 2 | .ui.bidIdx = 1' game.json > rev2.json
curl -s -b $JAR "${H[@]}" -X PUT $BASE/api/games/$GAME --data @rev2.json
#    and switch the link off: the stream prints `event: revoked` and ends
curl -s -b $JAR "${H[@]}" -X DELETE $BASE/api/games/$GAME/links/live

# 9. recap link: the WhatsApp preview page and the OG image
RECAP=$(curl -s -b $JAR "${H[@]}" -X POST $BASE/api/games/$GAME/links -d '{"kind":"recap"}' | jq -r .token)
curl -s $BASE/r/$RECAP | grep -E 'og:title|og:image"|refresh'
curl -s -o recap.png -w "%{http_code} %{content_type}\n" $BASE/api/public/recap/$RECAP/og.png && file recap.png   # 1200 x 630 PNG
```

---

## 5. Rules as built

### 5.1 Sync

- **Game id.** The server stores a game under its `syncId`, or under `id` when there is no `syncId`.
  The path `{id}` must equal it, else `422 invalid_game {reason: "id"}`. Send the app's `Game`
  exactly as it is (schema 3: `id` local, `syncId` UUID). It is stored byte for byte, except that
  merged `playerId`s are rewritten.
- **Upload outcomes:**
  - same payload again (canonical JSON hash): `200 {rev, earned: []}`, nothing changes;
  - `rev` ≤ stored with any change: `409 stale {server: <stored payload>}`;
  - higher `rev`: accepted;
  - only the uploader or an admin may update (`403`);
  - deleted games answer `410 gone`, so drop them locally.
- **Every seat needs a registry `playerId`.** A seat without one gives `422 invalid_game {reason: "players"}`.
  Unknown ids give `422 unknown_player {playerIds}` (create the players first).
- **Merged players:** when a merged id is uploaded, the stored payload uses the kept id and the
  response carries `remap: {old: new}`. `remap` is left out when nothing was rewritten.
- **`updatedSince`** (games and players) compares against the server's change time, `updatedAt`.
  Pass the newest `updatedAt` you have seen **minus 5000 ms**: a write that started just before your
  last read can commit with a slightly older timestamp. Repeats are harmless, because items are
  idempotent by id.
- **Lists:** players come oldest change first (sync order); games come newest change first.
  `nextCursor` is opaque; `limit` is 1–200 (default 50).
- **Tombstones:** `GET /api/games?updatedSince=` also returns `{id, deleted: true, updatedAt}` for
  games deleted since then. Without `updatedSince`, deleted games never appear.

### 5.2 Games ending, places and auto-end

- **When a game ends:**
  - `status: finished`: it ended at `finishedAt` (or `updatedAt` for legacy games);
  - active, with at least one done round, and untouched (payload `updatedAt`) for 24 h: it ends at
    its last `doneAt`. The `auto-end` job does this every 15 min, and an upload that is already
    that old ends at once, as the app's engine does.
  - A later upload with a fresh `updatedAt` reopens it.
- Places, wins and game/career achievements count only ended games. `summary.players[].place` is
  `null` until the game ends.
- Every confirmed round counts at once (points, rounds, runs, daily awards).

### 5.3 Accounts and security

| Rule | Value |
| --- | --- |
| Password | 3–128 characters, no whitespace anywhere. Otherwise `422 weak_password` |
| Session | cookie `kf_session` (HttpOnly, Secure, SameSite=Lax, Path=/), 180 days, rolling: extended and re-issued at most once a day |
| CSRF | every non-GET under `/api` needs `X-Kachuful: 1` **and** `Origin` equal to the site's origin, else `403 csrf` (login and logout too) |
| Login limit | 10 per 10 min per IP **and** 10 per 10 min per email |
| Token limit | 30 per 10 min per IP across `GET/POST /api/auth/invites/*` and `/api/auth/resets/*` |
| Upload limit | 120 `PUT /api/games/*` per minute per user |
| Rate-limited answer | `429 {code: "rate_limited", retryAfter: <seconds>}` |
| Revoked sessions | logout (this one), password change (all others), reset (all others), deactivation (all) |
| Invite / reset life | 7 days / 24 hours, one use; a new invite replaces the pending one |
| Body size | 1 MB, else `413 too_large` |
| Public responses | never emails, user ids (`owner.id` is `null`) or first/last names |

### 5.4 Players

- **Display names:** unique ignoring case and repeated spaces (`" riya  S"` = `"Riya s"`). Stored
  trimmed with single spaces. 1–14 characters, counted like JavaScript's `.length`.
- **Emails, the same-player rule and photos:** see §5.7.
- **Who may edit:** the display name, the admin or the linked user; first/last name, emoji and
  colour, also the player's creator.
- **Merge** (admin): the duplicate gets `mergedInto`, and every game, round, day and badge moves to
  the kept player. An account link moves too. It's refused (`422 invalid`) when the two sat at the
  same table, because that means they are different people.

### 5.5 Time and boards

- `from` / `to` are exact epoch ms, half-open, worked out in the viewer's timezone. Leave both out
  for all time (`[0, 2100-01-01)`).
- **Server days** are `Asia/Kolkata` days: daily awards, seasons, the calendar, Wrapped months and
  days, and night owl.
- **Minimum rounds** for rate metrics (`avgPerRound`, `accuracy`, `winRate`). Count metrics rank
  everyone. `minRounds` in the query overrides the minimum.

  | Range length | Ranked with more than |
  | --- | --- |
  | ≤ 1 day (+1 h slack) | 10 rounds |
  | ≤ 7 days (+1 h slack) | 25 rounds |
  | longer, and all time | 50 rounds |

- **Ranking order:** qualified rows first, then value, points and rounds (all descending), then
  player id. Equal values share a rank (1, 1, 3).
- `rankDelta` is the previous rank minus this rank (positive = up), against the window of the same
  length just before; all time has none.
- **Board columns:** `games` counts games with a round in the range. `wins` and `winRate` use games
  that *ended* in the range.
- `accuracy` and `winRate` are fractions from 0 to 1. `value` is exact; round it for display.
- **Daily awards** are provisional until **00:05 the next day** (Kolkata), when they are frozen.
  **Seasons** (calendar months) are live until **the 1st at 00:10**. Closed days and seasons never
  change and are cached forever.
- **ETags:** every read in §6 marked "ETag" sends `ETag` with `Cache-Control: private, no-cache`.
  Send `If-None-Match` and you get a bodiless `304` when nothing changed. Weak `W/` tags are
  accepted, because nginx gzip makes them weak. The tag changes after any write that affects reads.

### 5.6 Achievements

The catalogue, codes and rules are exactly the app's engine. The golden fixtures prove it; see
§8 and the app's `contract/fixtures/README.md` for the rules the plan left open. As built:

- **Round moments** (`hot_*`, `grand_slam`, …) carry `gameId` and `roundIndex`, earned at the
  round's `doneAt`.
- **Game finishes** (`winner`, …) carry `gameId`, earned at the end.
- **Career:**
  - every row carries the `gameId` where it was earned;
  - `rounds_*`, `made_*` and `points_*` also carry the `roundIndex`;
  - streaks (`back_to_back`, `hat_trick`, `dynasty`, `spoon_collector`, `podium_regular`) and
    `personal_best` can be earned again on later streaks;
  - `rivalry_20` is earned once per rival, with `data.with` = the rival's id;
  - milestones (`first_win`, `games_*`, `rounds_*`, `made_*`, `points_*`, `crew_*`) are earned once.
- **Daily and season awards** are stored as achievements too, with `period` set (`"2026-10-31"` or
  `"2026-10"`) and `data.value`.
- Edits can un-earn achievements, for example after an undo. That row disappears from lists, and
  its activity item goes with it. Rows that still hold keep their `earnedAt`.
- `earned` in the upload response lists only rows newly inserted by that upload.

### 5.7 Player identity and photos as built (contract change, 2026-10-31)

#### The same-player rule

**A person is the same player only when the display name AND the email both match one live
(unmerged) player.** A partial match never reveals anything about the other player: no id, name,
emoji, games or email.

- **Email:** every player has one. It identifies the person, including players with no account, and
  is never used to send mail. Emails are unique ignoring case, and stored trimmed and lower-cased.
  The shape check is `x@y.z` with no spaces.
- **Required for new players** (`POST /api/players`). Players created before this change may have no
  email until the admin, their creator or their linked user adds one (`PATCH`). Until then, nobody
  can match them by name and email; they just block their name (`name_taken`).
- **Display names** stay globally unique, as before.
- **Setup suggestions** ("players I added, or people I've played with") are for the app to compute:
  use `createdBy` and the user's games. `GET /api/players` still returns the whole registry. The
  server does nothing extra for this.

How the app adds someone at setup:

1. Suggestions show only the players this user created or has played with.
2. For anyone else, ask for **name and email**, then call
   `GET /api/players/check?name=&email=` (60 calls per minute per user):

   | Answer | Meaning | What the app does |
   | --- | --- | --- |
   | `{status: "match", player: PlayerSummary}` | That person is already a player | Use `player.id`; ask "Is this Riya?" with the details shown |
   | `{status: "email_taken", player: null}` | Someone else has that email | Ask for a different email |
   | `{status: "name_taken", player: null}` | Someone else has that name | Ask for a different name, e.g. "Riya S" |
   | `{status: "new", player: null}` | Nobody has either | `POST /api/players` |

   When both are taken by different players, the answer is `email_taken` (email is checked first).
3. `POST /api/players` answers the same way if two phones race:
   - `409 same_player {player}`: use that id;
   - `409 email_taken` or `409 name_taken`: no details;
   - `422 invalid {fields: {email}}`: the email is missing or malformed.

#### Who sees emails

| Viewer | `Player.email` |
| --- | --- |
| admin | yes, for every player |
| the player's creator (`createdBy`) | yes |
| the player's linked user | yes (their own) |
| everyone else | `null` |
| `PlayerSummary` (leaderboards, pages, `check`, `same_player`, …) | always `null` |
| public endpoints, preview pages, OG images | never |

`PATCH /api/players/{id}` accepts `email` from the admin, the creator or the linked user. A clash
answers `409 email_taken`; a bad shape answers `422 invalid`.

#### Invites, linked by email

- **`GET /api/auth/invites/{token}`:** `player` is the invite's pre-chosen player if it has one,
  otherwise the **unlinked** player whose email equals the invite email, otherwise `null`. Show it
  as "This is you", so the invitee claims their history.
- **`POST /api/auth/invites/{token}`:**
  - a **new** player always gets the invite's email; any email in the body is ignored;
  - a claimed player with **no** email gets the invite's email;
  - a claimed player with **another** email is refused (`409 email_taken`), unless the admin
    pre-chose that player for the invite.
- **Errors:** `409 name_taken`, `409 email_taken` and `409 player_linked` never include player
  details. A full name + email match is reported as `email_taken` here, because the invite's
  `GET` already offered that player.

#### Photos

| | |
| --- | --- |
| Upload | `PUT /api/players/{id}/photo`, body = the raw image bytes, `Content-Type: image/webp`, `image/jpeg` or `image/png`, at most **2 MB** (nginx and the API both allow 2 MB on this route only). Needs `X-Kachuful` and `Origin` like every write |
| Who | the admin, the player's creator, the player's linked user; anyone else gets `403` |
| Stored as | a **256×256 WebP, at most ~60 KB**, re-encoded in a worker thread: EXIF, GPS and every other metadata chunk are dropped; the photo is rotated upright and centre-cropped square; decoding refuses images over 40 megapixels (image bombs) |
| Answers | `200 {photoUrl}`; `415 unsupported_media_type` (other types or undecodable); `413 too_large` (over 2 MB) |
| Remove | `DELETE /api/players/{id}/photo` → `204`; the player shows the emoji again (`photoUrl: null`) |
| Fetch | `GET /api/players/{id}/photo?v=<rev>`: **public, no login** (shared links show photos), `image/webp`, `Cache-Control: public, max-age=31536000, immutable`; `404` when there is no photo |
| URL | `photoUrl = "/api/players/{id}/photo?v={photo_rev}"`, or `null`. Every upload or delete bumps the rev, so a new photo always has a new URL and old copies can be cached forever |
| Where `photoUrl` appears | `Player`, `PlayerSummary`, every `GameSummary.players[]` item (`{playerId, name, emoji, color, photoUrl, points, place}`), and so in public live and recap responses too. The recap OG image draws the photo (in a circle) instead of the emoji |
| Privacy | photo responses carry nothing but the image; no email or other private field ever leaks through them |
| Changes | an upload or delete bumps the player's `updatedAt` (it comes back in `updatedSince` syncs) and the data version (ETags) |

#### Copy-paste checks (verified on a local server, 2026-10-31)

Run these after §4 step 1 (signed in as Riya) and §3's `seed-demo`. Asha is
`asha@kachuful.test`.

```sh
# check: name + email of one player → match (email hidden)
curl -s -b $JAR -G $BASE/api/players/check --data-urlencode "name=asha" --data-urlencode "email=ASHA@kachuful.test" | jq '{status, player: .player.displayName, email: .player.email}'
#   {"status":"match","player":"Asha","email":null}

# check: right name, another email → name_taken (nothing else)
curl -s -b $JAR -G $BASE/api/players/check --data-urlencode "name=Asha" --data-urlencode "email=someone.new@example.com" | jq -c .
#   {"status":"name_taken","player":null}

# check: another name, a known email → email_taken
curl -s -b $JAR -G $BASE/api/players/check --data-urlencode "name=Brand New" --data-urlencode "email=asha@kachuful.test" | jq -c .
#   {"status":"email_taken","player":null}

# check: nothing known → new
curl -s -b $JAR -G $BASE/api/players/check --data-urlencode "name=Brand New" --data-urlencode "email=brand.new@example.com" | jq -c .
#   {"status":"new","player":null}

P=55555555-5555-4555-8555-555555555555
# create: a full match → 409 same_player {player}
curl -s -b $JAR "${H[@]}" -X POST $BASE/api/players -d "{\"id\":\"$P\",\"displayName\":\"Asha\",\"email\":\"asha@kachuful.test\",\"emoji\":\"🐯\",\"color\":\"#60a5fa\"}" | jq '{code, player: .player.id}'

# create: someone else's email → 409 email_taken, no details
curl -s -b $JAR "${H[@]}" -X POST $BASE/api/players -d "{\"id\":\"$P\",\"displayName\":\"Brand New\",\"email\":\"asha@kachuful.test\",\"emoji\":\"🐯\",\"color\":\"#60a5fa\"}" | jq -c .
#   {"code":"email_taken","message":"That email belongs to another player."}

# create: someone else's name → 409 name_taken, no details
curl -s -b $JAR "${H[@]}" -X POST $BASE/api/players -d "{\"id\":\"$P\",\"displayName\":\"Asha\",\"email\":\"brand.new@example.com\",\"emoji\":\"🐯\",\"color\":\"#60a5fa\"}" | jq -c .
#   {"code":"name_taken","message":"That name belongs to another player."}

# create: no email → 422 invalid
curl -s -b $JAR "${H[@]}" -X POST $BASE/api/players -d "{\"id\":\"$P\",\"displayName\":\"Brand New\",\"emoji\":\"🐯\",\"color\":\"#60a5fa\"}" | jq -c '{code, fields}'
#   {"code":"invalid","fields":{"email":"must look like x@y.z"}}

# create: new name + new email → 201 (200 when re-run); the creator sees the stored (lower-cased) email
curl -s -b $JAR "${H[@]}" -X POST $BASE/api/players -d "{\"id\":\"$P\",\"displayName\":\"Brand New\",\"email\":\"Brand.New@Example.com\",\"emoji\":\"🐯\",\"color\":\"#60a5fa\"}" | jq -c '{displayName, email, photoUrl}'
#   {"displayName":"Brand New","email":"brand.new@example.com","photoUrl":null}

# photo: upload any JPEG from your machine, then fetch it back
URL=$(curl -s -b $JAR -H "X-Kachuful: 1" -H "Origin: $ORIGIN" -H "Content-Type: image/jpeg" -X PUT --data-binary @face.jpg $BASE/api/players/$P/photo | jq -r .photoUrl)
echo $URL                                   # /api/players/5555.../photo?v=1
curl -s -D - -o photo.webp "$BASE$URL" | grep -iE '^(content-type|cache-control)'
file photo.webp                             # Web/P image, 256x256
#   content-type: image/webp
#   cache-control: public, max-age=31536000, immutable

# photo: a text body → 415
curl -s -b $JAR -H "X-Kachuful: 1" -H "Origin: $ORIGIN" -H "Content-Type: text/plain" -X PUT --data-binary "hello" $BASE/api/players/$P/photo | jq -c .
#   {"code":"unsupported_media_type","message":"Send a WebP, JPEG or PNG image (at most 2 MB)."}

# another member (Kabir) can't change it (403) and sees email: null
curl -s -o /dev/null -c k2.jar "${H[@]}" -X POST $BASE/api/auth/login -d '{"email":"kabir@kachuful.test","password":"kabir-demo"}'
curl -s -o /dev/null -w "%{http_code}\n" -b k2.jar -H "X-Kachuful: 1" -H "Origin: $ORIGIN" -H "Content-Type: image/jpeg" -X PUT --data-binary @face.jpg $BASE/api/players/$P/photo
curl -s -b k2.jar "$BASE/api/players?limit=200" | jq -c --arg p $P '.items[] | select(.id == $p) | {displayName, email, photoUrl}'
#   403
#   {"displayName":"Brand New","email":null,"photoUrl":"/api/players/5555.../photo?v=1"}
```

---

## 6. API reference

Everything lives under `/api` and uses JSON with camelCase fields. Times are epoch ms (integers)
and ids are UUID strings. Error bodies are `{code, message, ...extra}`.

### 6.1 Errors

| Status | `code` | Extra fields |
| --- | --- | --- |
| 401 | `unauthenticated`, `bad_credentials` | |
| 403 | `forbidden`, `csrf`, `inactive` | |
| 404 | `not_found` | |
| 405 | `method_not_allowed` | |
| 409 | `name_taken`, `email_taken` | none (they never reveal the other player) |
| 409 | `same_player` | `player: PlayerSummary` (name and email both matched; use this id) |
| 409 | `stale` | `server: <payload>` |
| 409 | `player_linked`, `user_exists`, `last_admin`, `both_linked`, `id_conflict` | |
| 410 | `gone` | |
| 413 | `too_large` | |
| 415 | `unsupported_media_type` | (photo uploads) |
| 422 | `invalid` | `fields: {"fieldName": "message"}`, e.g. `{"displayName": "must be 1-14 characters"}` or `{"player.id": "..."}` |
| 422 | `weak_password` | `fields: {"password": "..."}` |
| 422 | `invalid_game` | `reason` (`id`, `players`, `max_cards`, `round_plan`, `round_order`, `bids`, `results`, `tricks`, `times`), `roundIndex?` |
| 422 | `unknown_player` | `playerIds: string[]` |
| 429 | `rate_limited` | `retryAfter` (seconds) |
| 500 | `internal` | (never a stack trace) |

### 6.2 Types

```ts
User          = { id, email, role: 'admin'|'member', playerId: string|null, createdAt }
Player        = { id, displayName, email: string|null, photoUrl: string|null,
                  firstName: string|null, lastName: string|null, emoji, color,
                  createdBy: string|null, linkedUserId: string|null, mergedInto: string|null,
                  createdAt, updatedAt }
                // email only for the admin, the creator and the linked user (§5.7)
PlayerSummary = Player & { games: number, lastPlayedAt: number|null, createdByName: string }
                // email always null; createdByName = the creator's player name ('' if none)
NewPlayer     = { id, displayName, email, firstName?: string|null, lastName?: string|null, emoji, color }
                // email required by POST /api/players; ignored when accepting an invite
GameSummary   = { id, owner: { id: string|null, name: string }, createdAt, updatedAt,
                  endedAt: number|null, status: 'active'|'finished',
                  players: { playerId, name, emoji, color, photoUrl: string|null, points,
                             place: number|null }[],
                  rounds: number /* done */, lengthMode, live: boolean }
Achievement   = { id, code, playerId, gameId: string|null, roundIndex: number|null,
                  period: string|null, earnedAt, data: object }
MadeOf        = { made: number, total: number }
PlayerStats   = { points, rounds, made, missed, accuracy, avgPerRound, games, gamesEnded, wins, winRate,
                  places: { "1": n, "2": n, "3": n }, bestRound: number|null, worstRound: number|null,
                  longestHot, longestCold, hookHits, bestGame: GameSummary|null,
                  bidHistogram: { [bid]: MadeOf }, byCards: { [cards]: MadeOf },
                  byTrump: { [trump]: MadeOf }, asDealer: { rounds, made } }
LeaderboardRow = { rank: number|null, qualified, player: PlayerSummary, value, points, games, rounds,
                   wins, accuracy, longestHot, rankDelta: number|null }
Award         = { code, player: PlayerSummary, value, gameId: string|null, roundIndex: number|null }
Page<T>       = { items: T[], nextCursor: string|null }
```

### 6.3 Auth (signed out allowed where noted)

| Call | Body → success | Errors |
| --- | --- | --- |
| `POST /auth/login` (signed out) | `{email, password}` → `{user, player: Player\|null}` + cookie | 401 `bad_credentials`, 403 `inactive`, 422 `invalid`, 429 |
| `POST /auth/logout` | → 204, cookie cleared | |
| `GET /auth/me` | → `{user, player}` | 401 |
| `GET /bootstrap` (ETag) | → `{user, player, players: Player[] (whole registry, merged included), myGames: GameSummary[] (uploaded by me, newest change first), myAchievements: Achievement[] (my player's), dataVersion}` | 401 |
| `GET /auth/invites/{token}` (signed out) | → `{email, player: PlayerSummary\|null}`: the pre-chosen player, else the unlinked player with the invite email (§5.7) | 410, 429 |
| `POST /auth/invites/{token}` (signed out) | `{password, player: {id} \| NewPlayer}` → `{user, player}` + cookie; the invite email goes to a new player, or to a claimed player that has none | 410, 422 `weak_password`, 409 `name_taken`, 409 `email_taken`, 409 `player_linked`, 409 `id_conflict`, 422 `unknown_player`, 422 `invalid` (the invite pre-chose another player, or a bad name), 429 |
| `GET /auth/resets/{token}` (signed out) | → `{email}` | 410, 429 |
| `POST /auth/resets/{token}` (signed out) | `{password}` → `{user, player}` + cookie; every other session ends | 410, 422 `weak_password`, 403 `inactive`, 429 |
| `POST /me/password` | `{current, password}` → 204; other sessions end | 401 `bad_credentials`, 422 `weak_password` |

### 6.4 Admin (admins only: 401 signed out, 403 `forbidden` for members)

| Call | Body → success | Errors |
| --- | --- | --- |
| `GET /admin/users` | → `{user: User\|null, player: Player\|null, active, lastSeenAt: number\|null, invite: {email, expiresAt}\|null}[]`. Accounts first (oldest first), then rows for invites with no account yet (`user: null`, `active: false`) | |
| `POST /admin/invites` | `{email, playerId?}` → 201 `{inviteUrl, expiresAt}` (`https://play.vectanix.com/?invite=<token>`) | 409 `user_exists`, 422 `unknown_player`, 409 `player_linked`, 422 `invalid` (email) |
| `DELETE /admin/invites/{email}` | → 204 | |
| `POST /admin/users/{id}/reset-link` | → 201 `{resetUrl, expiresAt}` (`.../?reset=<token>`) | 404 |
| `PATCH /admin/users/{id}` | `{active?: boolean}` → `{user}` | 404, 409 `last_admin` |
| `POST /admin/players/{id}/merge` | `{intoId}` → `{player}` (the kept one) | 404, 409 `both_linked`, 422 `invalid` |
| `GET /admin/overview` | → `{users, players, games, rounds, lastBackupAt: number\|null}` | |

### 6.5 Players and games (signed in)

| Call | Body / query → success | Errors |
| --- | --- | --- |
| `GET /players?updatedSince=&cursor=&limit=` | → `Page<Player>`, oldest change first, merged included | 422 (bad cursor) |
| `GET /players/check?name=&email=` | → `{status: 'match', player: PlayerSummary}` or `{status: 'email_taken' \| 'name_taken' \| 'new', player: null}` (§5.7) | 422 (email), 429 (60/min) |
| `POST /players` | `NewPlayer` (email required) → 201 `Player` (200 if the same id and body exist) | 409 `same_player {player}`, 409 `email_taken`, 409 `name_taken`, 409 `id_conflict`, 422 `invalid` |
| `PATCH /players/{id}` | any of `{displayName, email, firstName, lastName, emoji, color}` → `Player` | 403, 404 (unknown or merged), 409 `name_taken`, 409 `email_taken`, 422 |
| `PUT /players/{id}/photo` | raw `image/webp\|jpeg\|png` bytes (≤ 2 MB) → `{photoUrl}` | 403, 404, 413, 415 |
| `DELETE /players/{id}/photo` | → 204 | 403, 404 |
| `GET /players/{id}/photo?v=` (**public**) | → `image/webp`, cached forever | 404 |
| `GET /players/{id}` (ETag) | → `{player: PlayerSummary, totals: PlayerStats (all time), achievements: Achievement[] (newest first), rank: {global: number\|null, outOf}}` (all-time average board, more than 50 rounds) | 404 |
| `GET /players/{id}/stats?from=&to=` (ETag) | → `PlayerStats` | 404, 422 |
| `GET /players/{id}/games?from=&to=&cursor=&limit=` | → `Page<GameSummary>`, games with a round in the range, newest created first | 404, 422 |
| `GET /players/{id}/calendar?year=` (ETag) | → `{days: {"2026-10-31": rounds, ...}}` | 404 |
| `GET /players/{a}/vs/{b}?from=&to=` (ETag) | → `{a, b: PlayerSummary, games, aAhead, bAhead, ties, aPoints, bPoints, lastGames: GameSummary[] (≤5, newest end first), biggestWin: {by, game: GameSummary, winner: playerId}\|null}`, over games both finished in the range | 404 |
| `PUT /games/{id}` | the app's `Game` → `{rev, earned: Achievement[], remap?: {old: new}}` | 403, 409 `stale`, 410, 422 `invalid` / `invalid_game` / `unknown_player`, 429 |
| `GET /games?updatedSince=&cursor=&mine=&limit=` | → `Page<GameSummary \| {id, deleted: true, updatedAt}>`, newest change first; `mine=true` → only mine | 422 |
| `GET /games/{id}` | → `{game: <payload>, summary: GameSummary, achievements: Achievement[] (oldest first)}` | 404 |
| `DELETE /games/{id}` | → 204 (owner or admin); boards, stats and achievements stop counting it; its links are revoked | 403, 404 |
| `POST /games/{id}/links` | `{kind: 'live'\|'recap'}` → 201 `{token, url, qrSvgUrl}`; the active link is reused | 403, 404 |
| `DELETE /games/{id}/links/{kind}` | → 204; live viewers get `revoked` | 403, 404 |
| `GET /games/{id}/links/{kind}/qr.svg` | → `image/svg+xml` | 404 (no active link) |

### 6.6 Boards (signed in)

| Call | Query → success |
| --- | --- |
| `GET /leaderboard` (ETag) | `from, to, metric` (default `avgPerRound`; also `points, wins, winRate, accuracy, bestRound, longestHot, games, rounds`), `minRounds?` → `{rows: LeaderboardRow[], minRounds, generatedAt}` |
| `GET /awards/daily?date=YYYY-MM-DD` (ETag) | → `{date, final, awards: Award[]}`; `final` is true once the day is closed |
| `GET /seasons` (ETag) | → `{season: "YYYY-MM", champion: PlayerSummary\|null}[]`, newest first, from the first played month to this one |
| `GET /seasons/{yyyy-mm}` (ETag) | → `{season, final, standings: LeaderboardRow[], awards: Award[]}` |
| `GET /wrapped/{year}/{playerId}` (ETag) | → Wrapped (below); 404 for an unknown player |
| `GET /activity?cursor=&limit=` | → `Page<{id: number, kind, player: PlayerSummary\|null, game: GameSummary\|null, data, createdAt}>`. Kinds and their `data`: `achievement` → `{code, ...the achievement's data}`; `game_finished` → `{}`; `season_closed` → `{season, champions: playerId[]}`; `player_joined` → `{}` |
| `GET /achievements?playerId=&cursor=&limit=` | → `Page<Achievement>`, newest first |

```ts
Wrapped = { year, playerId, player: PlayerSummary, totals: PlayerStats,
  bestMonth: { month: "YYYY-MM", points } | null,  bestDay: { day: "YYYY-MM-DD", points } | null,
  favouriteTrump: { trump, accuracy, rounds } | null,          // best accuracy, at least 10 rounds
  nemesis: { playerId, player: PlayerSummary, losses } | null, // finished above you most often
  partner: { playerId, player: PlayerSummary, games } | null,  // most games together
  biggestRound: { points, gameId, roundIndex } | null,
  longestRun: number, achievements: number,                    // round/game/career earned that year
  topDays: { day, rounds }[],                                  // up to 3
  rank: { rank: number|null, outOf } }                         // year board, more than 50 rounds
```

### 6.7 Public (no login)

| Call | Success | Errors |
| --- | --- | --- |
| `GET /public/live/{token}` | `{game, summary, updatedAt, scorerSeenAt: number\|null}` | 404 unknown or wrong kind; 410 revoked or game deleted |
| `GET /public/live/{token}/events` | SSE (below) | same |
| `GET /public/recap/{token}` | `{game, summary, achievements}` | same |
| `GET /public/recap/{token}/og.png` | 1200×630 PNG (top three with emoji and points, rounds, players, date) | same |
| `GET /r/{token}`, `GET /l/{token}` (not under `/api`) | HTML with Open Graph and Twitter tags (`og:image` for recaps) and `<meta http-equiv="refresh" content="0;url=/?recap=<token>">` (or `?live=`) | 404 page that redirects to `/` |

**SSE** (`text/event-stream`):
- `event: game` with `id: <rev>` and the same JSON as `GET /public/live/{token}`, sent on every
  upload of the game. The first one is sent on connect, unless `Last-Event-ID` is already the
  current rev.
- `event: ping` every 25 s.
- `event: revoked`, after which the stream ends.

Reconnect with `Last-Event-ID` (EventSource does this by itself). Measured locally: under 40 ms from
`PUT` to the viewer.

---

## 7. Contract changes

### 7.1 Approved by Jay (2026-10-31), already in the backend's `plan-backend.md`

1. `GET /admin/users`: `invite` has no `url` (tokens are stored hashed); a fresh link comes from
   `POST /admin/invites` again.
2. Public `GameSummary.owner.id` is `null`.
3. `PUT /games/{id}` on a deleted game → `410 gone`; `GET /games?updatedSince=` includes tombstones.
4. New codes `409 last_admin`, `409 id_conflict`, `422 invalid_game {reason: "id"}`.
5. Defined: `invalid.fields` shape, `retryAfter` in seconds, `accuracy`/`winRate` as 0–1,
   `DELETE /admin/invites/{email}` → 204 always, `createdByName`/`owner.name` = the user's player
   name, registry names 1–14 characters.
6. Career repeat rules: as the app's engine (§5.6). Golden fixtures override the plan, as agreed.

7. **Player identity and photos (2026-10-31):** every player has a unique, private email; the same
   player = name **and** email; the new `check` answer; `409 same_player` / `email_taken`;
   `name_taken` without details (it no longer carries `existing`); invite linking by email; photos
   and `photoUrl`. Built exactly as in §5.7, and recorded in `plan-backend.md` §6.8.

### 7.2 Additions while building (backward compatible; Jay to confirm)

- `GameSummary.updatedAt`: the server change time; you need it for `updatedSince`.
- `GET /admin/users` `invite.email`, so invite-only rows show who was invited.
- `HeadToHead.biggestWin.winner` (which of the two won that game).
- The Wrapped shape above (the plan left it open): the app's own `Wrapped` plus `PlayerSummary`s.
- `Award.gameId` and `roundIndex` are `null` except for `day_bang` and `day_ouch`.
- Error codes `413 too_large` and `405 method_not_allowed` (transport level).
- `422 invalid_game {reason: "players"}` for a seat with no `playerId`.
- `422 invalid` on merge when both players sat at one table; on invite acceptance when the invite
  pre-chose a different player.
- The game id rule in §5.1: the app's `syncId`, else `id`.
- Identity details the spec left open: a claimed (existing) player whose email is not the invite's
  is refused with `409 email_taken` unless the admin pre-chose it; a full match during invite
  acceptance is reported as `email_taken`; the photo error code is `unsupported_media_type`;
  `PATCH` cannot clear an email (`null` → `422`).

---

## 8. Parity

The golden fixtures are `contract/fixtures` from the app repo at commit **`39733d1`**, copied
unchanged into `tests/fixtures/` (17 files: 7 games, 8 invalid, 1 month-long set). **Every
expectation passes**, none hand-written, and through two paths:

1. **Pure Python domain:** validation reasons and rounds, end times, totals, places, `game_players`,
   `round_scores`, every achievement, all 45 leaderboards (9 metrics × 5 ranges), daily awards
   (3 days), seasons (2 months), career.
2. **The real server path:**
   - the fixtures are uploaded through `PUT` logic into Postgres;
   - the stored `game_players` and `round_scores` equal the expectations;
   - the SQL leaderboards equal all 45 expectations;
   - SQL daily awards and seasons equal theirs;
   - the incrementally maintained `achievements` table equals the set's full achievement list.

When the app's rules change: re-export, copy into `tests/fixtures/`, and run `make verify`.

---

## 9. Performance

These numbers were re-measured after the photos change (summaries now look up photos), **locally** (MacBook, Postgres 16 in Docker, in-process, every cache
cleared before each request), **not on the VPS**. The dataset was built by `seed-load` through the
real upload path: 50 players, 5,000 games, 400,645 `round_scores` rows (about 80,000 rounds).

| Endpoint | p95 | Budget |
| --- | ---: | ---: |
| `GET /api/auth/me` | 3.4 ms | 30 ms |
| `GET /api/bootstrap` (user with 1,000 games) | 58.8 ms | 80 ms |
| `PUT /api/games/{id}` (16 rounds, with derive + achievements) | 66.3 ms | 120 ms |
| `GET /api/leaderboard` (worst of 9 metrics × 4 ranges) | 21.7 ms | 60 ms |
| `GET /api/players/{id}` | 53.8 ms | 80 ms |
| `GET /api/players/{id}/stats` | 9.7 ms | 80 ms |
| `GET /api/players/{id}/games` | 7.5 ms | 80 ms |
| `GET /api/players/{id}/calendar` | 4.7 ms | 80 ms |
| `GET /api/players/{a}/vs/{b}` | 6.7 ms | 80 ms |
| `GET /api/games` | 4.7 ms | 50 ms |
| any `304 Not Modified` | 2.8 ms | 10 ms |
| live update, `PUT` → viewer (max of 10) | 22.6 ms | 300 ms |

Every index in plan §15.2 exists, and a test runs `EXPLAIN` to prove its query uses it. Query
counts on the main endpoints are fixed whatever the data size (tested). Re-run locally with
`make load` then `make perf`. Never run `seed-load` against production.

---

## 10. Known gaps and follow-ups

- **What's verified on production (2026-11-01):** the CD deploy (CI → upload → migrate → restart →
  health check), `/api/health`, the nginx security headers and the job timers. The rest of this file
  (the §4 and §5.7 checks) was verified against a local server, because production has no test
  accounts; the app's own use is the first real test there.
- **Backups are local only** until Jay sets `BACKUP_REMOTE` (DEPLOY.md §9). The restore drill on
  production waits for the first backup with real data.
- **No staging (decided 2026-11-01).** Every push to the backend `main` goes to production after
  CI. Try changes against a local backend with `seed-demo`; production only gets real data.
- **Board `games` count:** a game counts in a range when its first round is before the range's end
  and its last round is in or after the range's start. A game with a full day's pause inside the
  range, and no round in the range itself, would be counted where the app wouldn't. No fixture
  covers it.
- **`GET /seasons`** computes not-yet-closed months live. This is cheap while `close-season` runs
  monthly; a long backlog of unclosed months would make the first call after a change slower.
- **Live viewers** are held in memory per worker. A restart or deploy drops them; EventSource
  reconnects by itself with `Last-Event-ID`.
- **The activity feed** gets an item for every achievement, round moments included. Filter by
  `data.code` in the app if it's too busy.
- **OG images** use Noto Color Emoji 2.047. Emoji newer than that draw as blanks.
- **Restore rehearsal:** done locally with a dump of the 5,000-game database; every count matched.
  The first production restore drill is in DEPLOY.md §11.
- **No email sending** of any kind (by design); invite and reset links are copied by hand.
- **`check` can confirm an email exists:** any signed-in member can learn that an email belongs to
  *some* player (`email_taken`), though never which one. It's limited to 60 a minute per user, and
  the group is invite-only.
- **Photos live in Postgres** (about 60 KB each), so the nightly backup includes them; there is no
  separate file store to back up.
- **Players from before emails** can't be matched until someone adds their email (§5.7).
