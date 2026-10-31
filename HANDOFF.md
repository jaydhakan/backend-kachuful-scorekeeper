# Kachuful API: handoff to the frontend

_Work in progress. Updated at the end of every backend milestone; complete at B10._

## Status

| Milestone | State |
| --------- | ----- |
| B0 skeleton | done |
| B1 auth + admin | done |
| B2 players | done |
| B3 games | done |
| B4 domain port + derived tables | done (all golden fixtures pass) |
| B5 boards | done (SQL leaderboards equal the app for every metric and range) |
| B6 achievements | done (stored rows equal the app's full set) |
| B7 daily, seasons, Wrapped | done (daily and season awards equal the app) |
| B8 live + recap | done (SSE tested on a real server) |
| B9–B10 | not started |

## Endpoints so far

| Endpoint | State |
| -------- | ----- |
| `GET /api/health` | done, tested |
| `POST /api/auth/login` | done, tested |
| `POST /api/auth/logout` | done, tested |
| `GET /api/auth/me` | done, tested |
| `GET /api/auth/invites/{token}` | done, tested |
| `POST /api/auth/invites/{token}` | done, tested |
| `GET /api/auth/resets/{token}` | done, tested |
| `POST /api/auth/resets/{token}` | done, tested |
| `POST /api/me/password` | done, tested |
| `GET /api/admin/users` | done, tested (`invite` is `{email, expiresAt}`) |
| `POST /api/admin/invites` | done, tested |
| `DELETE /api/admin/invites/{email}` | done, tested |
| `POST /api/admin/users/{id}/reset-link` | done, tested |
| `PATCH /api/admin/users/{id}` | done, tested |
| `GET /api/players` | done, tested |
| `GET /api/players/check` | done, tested |
| `POST /api/players` | done, tested (201 new, 200 same id + body, 409 `id_conflict`) |
| `PATCH /api/players/{id}` | done, tested |
| `POST /api/admin/players/{id}/merge` | done, tested (moves games, rounds, days; 422 `invalid` if both sat at one table) |
| `GET /api/admin/overview` | done, tested |
| `PUT /api/games/{id}` | done, tested (stale, idempotent, 403, 410, unknown_player + remap, invalid_game) |
| `GET /api/games` | done, tested (tombstones with `updatedSince`) |
| `GET /api/games/{id}` | done, tested |
| `DELETE /api/games/{id}` | done, tested |
| `GET /api/leaderboard` | done, tested (ETag/304) |
| `GET /api/bootstrap` | done, tested (ETag/304) |
| `GET /api/players/{id}` | done, tested |
| `GET /api/players/{id}/stats` | done, tested |
| `GET /api/players/{id}/games` | done, tested |
| `GET /api/players/{id}/calendar` | done, tested |
| `GET /api/players/{a}/vs/{b}` | done, tested |
| `GET /api/achievements` | done, tested |
| `GET /api/activity` | done, tested |
| `GET /api/awards/daily` | done, tested (closed days: immutable cache) |
| `GET /api/seasons` | done, tested |
| `GET /api/seasons/{yyyy-mm}` | done, tested (closed seasons: immutable cache) |
| `GET /api/wrapped/{year}/{playerId}` | done, tested |
| `POST /api/games/{id}/links` | done, tested |
| `DELETE /api/games/{id}/links/{kind}` | done, tested |
| `GET /api/games/{id}/links/{kind}/qr.svg` | done, tested |
| `GET /api/public/live/{token}` | done, tested |
| `GET /api/public/live/{token}/events` | done, tested (SSE) |
| `GET /api/public/recap/{token}` | done, tested |
| `GET /api/public/recap/{token}/og.png` | done, tested |
| `GET /r/{token}`, `GET /l/{token}` | done, tested |
