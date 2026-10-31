"""Every hot-path index in plan §15.2 is used by its query (plan: an EXPLAIN test per index)."""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ID = "00000000-0000-4000-8000-000000000001"

CASES = [
    ("uq_users_email", "SELECT id FROM users WHERE email = 'a@b.co'"),
    ("uq_sessions_token_hash", "SELECT id FROM sessions WHERE token_hash = 'x'"),
    ("ix_sessions_user_id", f"SELECT id FROM sessions WHERE user_id = '{ID}'"),
    ("ix_sessions_expires_at", "SELECT id FROM sessions WHERE expires_at <= now()"),
    ("uq_invites_token_hash", "SELECT id FROM invites WHERE token_hash = 'x'"),
    (
        "ix_invites_email_pending",
        "SELECT id FROM invites WHERE email = 'a@b.co' AND accepted_at IS NULL AND revoked_at IS NULL",
    ),
    ("uq_password_resets_token_hash", "SELECT id FROM password_resets WHERE token_hash = 'x'"),
    (
        "uq_players_name_key_active",
        "SELECT id FROM players WHERE name_key = 'riya' AND merged_into IS NULL",
    ),
    (
        "uq_players_email_active",
        "SELECT id FROM players WHERE email = 'a@b.co' AND merged_into IS NULL",
    ),
    ("ix_players_updated_at_id", "SELECT id FROM players ORDER BY updated_at, id LIMIT 50"),
    ("ix_players_name_key_trgm", "SELECT id FROM players WHERE name_key % 'riy'"),
    ("ix_games_updated_at_id", "SELECT id FROM games ORDER BY updated_at DESC, id DESC LIMIT 50"),
    (
        "ix_games_owner_id_updated_at",
        f"SELECT id FROM games WHERE owner_id = '{ID}' ORDER BY updated_at DESC LIMIT 50",
    ),
    (
        "ix_games_ended_at_live",
        "SELECT id FROM games WHERE ended_at >= now() - interval '1 day' AND deleted_at IS NULL",
    ),
    (
        "ix_games_auto_end",
        "SELECT id FROM games WHERE status = 'active' AND ended_at IS NULL "
        "AND deleted_at IS NULL AND client_updated_at < now() AND round_count > 0 "
        "ORDER BY client_updated_at LIMIT 200",
    ),
    (
        "ix_game_players_player_id",
        f"SELECT game_id, points FROM game_players WHERE player_id = '{ID}'",
    ),
    (
        "ix_game_players_ended_at",
        "SELECT player_id, won FROM game_players WHERE ended_at >= now() - interval '30 days' "
        "AND ended_at < now() AND place IS NOT NULL",
    ),
    (
        "ix_game_players_last_done_at",
        "SELECT player_id FROM game_players WHERE last_done_at >= now() - interval '7 days' "
        "AND first_done_at < now()",
    ),
    (
        "ix_round_scores_player_id_done_at",
        f"SELECT points FROM round_scores WHERE player_id = '{ID}' "
        "AND done_at >= now() - interval '1 day'",
    ),
    (
        "ix_round_scores_done_at",
        "SELECT player_id FROM round_scores WHERE done_at >= now() - interval '1 hour'",
    ),
    (
        "ix_player_day_day",
        "SELECT player_id, sum(points) FROM player_day "
        "WHERE day >= '2026-10-01' AND day < '2026-11-01' GROUP BY player_id",
    ),
    (
        "pk_player_day",
        f"SELECT rounds FROM player_day WHERE player_id = '{ID}' AND day = '2026-10-01'",
    ),
    (
        "ix_achievements_player_id_earned_at",
        f"SELECT id FROM achievements WHERE player_id = '{ID}' "
        "ORDER BY earned_at DESC, id DESC LIMIT 50",
    ),
    (
        "ix_achievements_code_period",
        "SELECT id FROM achievements WHERE code = 'day_star' AND period = '2026-10-31'",
    ),
    (
        "ix_activity_created_at_id",
        "SELECT id FROM activity ORDER BY created_at DESC, id DESC LIMIT 50",
    ),
    (
        "ix_share_links_game_id_kind_active",
        f"SELECT token FROM share_links WHERE game_id = '{ID}' AND kind = 'live' AND revoked_at IS NULL",
    ),
]


@pytest.mark.parametrize(("index", "query"), CASES, ids=[c[0] for c in CASES])
async def test_hot_query_uses_its_index(session: AsyncSession, index: str, query: str) -> None:
    # Empty test tables make a sequential scan cheapest; switching it off asks "is the index usable?".
    await session.execute(text("SET LOCAL enable_seqscan = off"))

    plan = (await session.execute(text(f"EXPLAIN (FORMAT JSON) {query}"))).scalar_one()

    assert index in json.dumps(plan), json.dumps(plan)
