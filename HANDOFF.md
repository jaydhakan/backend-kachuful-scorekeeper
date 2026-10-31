# Kachuful API: handoff to the frontend

_Work in progress. Updated at the end of every backend milestone; complete at B10._

## Status

| Milestone | State |
| --------- | ----- |
| B0 skeleton | done |
| B1 auth + admin | done |
| B2–B10 | not started |

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
