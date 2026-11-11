"""Ishaara actions through the API: a full game, replay, stale, roles, rule errors, the timer."""

import logging
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, IshaaraAction, IshaaraGamePlayer, IshaaraPlayerDay
from app.security import rate_limit
from tests.factories import make_user
from tests.ishaara_builder import (
    TEAM_WORDS,
    ClientFor,
    Clock,
    Table,
    act,
    cards_of,
    client_for_fixture,  # noqa: F401 - fixture
    create,
    freeze,
    hides_the_key,
    make_table,
    read,
    start,
)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    return freeze(monkeypatch)


@pytest.fixture
async def table(session: AsyncSession, client_for: ClientFor) -> Table:
    return await make_table(session, client_for)


async def _clue(table: Table, state: dict[str, Any], number: int | str = "inf") -> dict[str, Any]:
    team = state["turn"]
    response = await act(table, team, state, "clue", word=TEAM_WORDS[team], number=number)
    assert response.status_code == 200, response.text
    game: dict[str, Any] = response.json()["game"]
    return game


async def _guess(table: Table, state: dict[str, Any], card: int) -> dict[str, Any]:
    response = await act(table, "host", state, "guess", card=card)
    assert response.status_code == 200, response.text
    game: dict[str, Any] = response.json()["game"]
    return game


def _error(response: httpx.Response, status: int, code: str, reason: str | None = None) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code
    if reason is not None:
        assert body["reason"] == reason
    assert "game" in body and "key" not in body


async def _unchanged(table: Table, before: dict[str, Any]) -> None:
    assert (await read(table, before["id"]))["rev"] == before["rev"]


@pytest.mark.usefixtures("clock")
async def test_a_full_game_to_all_found_stores_rows_and_hides_the_key_until_the_end(
    table: Table, session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    state, key = await start(table)
    starting = state["starting"]
    texts = []

    state = await _clue(table, state)
    for card in cards_of(key, starting, state):
        response = await act(table, "host", state, "guess", card=card)
        assert response.status_code == 200, response.text
        texts.append(response.text)
        state = response.json()["game"]
        if state["status"] != "finished":
            assert hides_the_key(response.text, state, key)

    assert (state["status"], state["winner"], state["endedBy"]) == (
        "finished",
        starting,
        "all-found",
    )
    assert state["solution"] == key
    row = await session.get(Game, uuid.UUID(state["id"]), populate_existing=True)
    assert row is not None and row.status == "finished" and row.round_count == 9
    assert row.summary["winner"] == starting and row.finished_at is not None
    rows = (await session.execute(select(IshaaraGamePlayer))).scalars().all()
    assert len(rows) == 4
    assert {(r.team, r.won, r.words_found) for r in rows} == {
        (starting, True, 9),
        ("moon" if starting == "sun" else "sun", False, 0),
    }
    giver = next(r for r in rows if r.team == starting and r.role == "clue-giver")
    assert (giver.clues_given, giver.avg_clue_number) == (1, None)
    days = (await session.execute(select(IshaaraPlayerDay))).scalars().all()
    assert len(days) == 4 and sum(d.wins for d in days) == 2
    actions = await session.execute(select(func.count()).select_from(IshaaraAction))
    assert actions.scalar_one() == 10
    logged = caplog.text.replace(" ", "")
    assert ",".join(f'"{k}"' for k in key) not in logged
    assert str(key) not in caplog.text


@pytest.mark.usefixtures("clock")
async def test_replaying_an_action_id_changes_nothing(table: Table) -> None:
    state, key = await start(table)
    state = await _clue(table, state)
    body = {"actionId": str(uuid.uuid4()), "rev": state["rev"], "type": "guess"}
    body["card"] = cards_of(key, state["turn"], state)[0]
    path = f"/api/ishaara/games/{state['id']}/actions"

    first = await table.clients["host"].post(path, json=body)
    again = await table.clients["host"].post(path, json=body)

    assert first.status_code == 200 and again.status_code == 200
    assert again.json()["game"] == first.json()["game"]
    assert (await read(table, state["id"]))["rev"] == state["rev"] + 1


@pytest.mark.usefixtures("clock")
async def test_an_old_rev_is_stale_with_the_current_game(table: Table) -> None:
    state, _ = await start(table)

    response = await act(table, state["turn"], {**state, "rev": state["rev"] - 1}, "clue",
                         word="solar", number=1)  # fmt: skip

    _error(response, 409, "stale")
    assert response.json()["game"]["game"]["rev"] == state["rev"]
    await _unchanged(table, state)


@pytest.mark.usefixtures("clock")
async def test_wrong_role_or_turn_is_not_your_turn_and_changes_nothing(table: Table) -> None:
    state, key = await start(table)
    turn, off = state["turn"], ("moon" if state["turn"] == "sun" else "sun")

    tries = [
        await act(table, "host", state, "clue", word="solar", number=1),
        await act(table, off, state, "clue", word="solar", number=1),
        await act(table, turn, state, "guess", card=0),
        await act(table, turn, state, "endTurn"),
    ]
    clued = await _clue(table, state, 1)
    tries.append(await act(table, turn, clued, "notAllowed"))

    for response in tries:
        _error(response, 403, "not_your_turn")
        assert hides_the_key(response.text, response.json()["game"]["game"], key)
    await _unchanged(table, clued)


@pytest.mark.usefixtures("clock")
async def test_strangers_and_the_admin_cannot_act(
    table: Table,
    session: AsyncSession,
    client_for: ClientFor,
) -> None:
    state, _ = await start(table)
    admin = await make_user(session, email="boss@example.com", role="admin", player_name="Boss")
    body = {"actionId": str(uuid.uuid4()), "rev": state["rev"], "type": "resign", "team": "sun"}

    response = await (await client_for(admin)).post(
        f"/api/ishaara/games/{state['id']}/actions", json=body
    )

    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    await _unchanged(table, state)


@pytest.mark.usefixtures("clock")
async def test_rule_errors_are_invalid_action_with_no_change(table: Table) -> None:
    waiting = (await create(table))["game"]
    not_started = await act(table, "host", waiting, "guess", card=0)
    state, key = await start(table)
    no_clue = await act(table, "host", state, "guess", card=0)
    state = await _clue(table, state)
    card = cards_of(key, state["turn"], state)[0]
    state = await _guess(table, state, card)
    revealed = await act(table, "host", state, "guess", card=card)
    too_late = await act(table, "moon" if state["turn"] == "sun" else "sun", state, "notAllowed")
    early_timeout = await act(table, "host", state, "timeout")
    bad_card = await act(table, "host", state, "guess", card=25)

    _error(not_started, 422, "invalid_action", "not-started")
    _error(no_clue, 422, "invalid_action", "no-clue")
    _error(revealed, 422, "invalid_action", "revealed")
    _error(too_late, 422, "invalid_action", "too-late")
    _error(early_timeout, 422, "invalid_action", "too-early")
    _error(bad_card, 422, "invalid_action", "bad-card")
    for response in (revealed, too_late, early_timeout, bad_card):
        assert hides_the_key(response.text, response.json()["game"]["game"], key)
    await _unchanged(table, state)


@pytest.mark.usefixtures("clock")
async def test_a_finished_game_takes_no_more_actions(table: Table) -> None:
    state, key = await start(table)
    resigned = await act(table, "host", state, "resign", team="sun")
    state = resigned.json()["game"]

    after = await act(table, "host", state, "endTurn")

    assert (state["status"], state["winner"], state["endedBy"]) == ("finished", "moon", "resign")
    assert state["solution"] == key
    _error(after, 422, "invalid_action", "finished")
    await _unchanged(table, state)


async def test_undo_works_within_ten_seconds_only(table: Table, clock: Clock) -> None:
    state, key = await start(table)
    state = await _clue(table, state)
    first, second = cards_of(key, state["turn"], state)[:2]
    state = await _guess(table, state, first)
    clock.advance(5)

    undone = await act(table, "host", state, "undoLast")
    state = undone.json()["game"]
    state = await _guess(table, state, second)
    clock.advance(11)
    late = await act(table, "host", state, "undoLast")

    assert undone.status_code == 200 and state["revealed"][first] is None
    _error(late, 422, "invalid_action", "too-late")


async def test_a_ruled_out_clue_gives_a_penalty_pick_and_skip_waits_a_minute(
    table: Table, clock: Clock
) -> None:
    state, key = await start(table)
    offender = state["turn"]
    picker = "moon" if offender == "sun" else "sun"
    state = await _clue(table, state, 1)
    ruled = await act(table, picker, state, "notAllowed")
    state = ruled.json()["game"]
    early = await act(table, "host", state, "skip")
    wrong_card = await act(
        table, picker, state, "penaltyReveal", card=cards_of(key, offender, state)[0]
    )
    picked = await act(table, picker, state, "penaltyReveal", card=cards_of(key, picker, state)[0])
    after_pick = picked.json()["game"]
    clue = await _clue(table, after_pick, 1)
    ruled_again = await act(table, offender, clue, "notAllowed")
    state = ruled_again.json()["game"]
    clock.advance(61)
    skipped = await act(table, "host", state, "skip")

    assert ruled.status_code == 200
    _error(early, 422, "invalid_action", "too-early")
    _error(wrong_card, 422, "invalid_action", "not-yours")
    assert picked.status_code == 200 and after_pick["pending"] is None
    assert ruled_again.status_code == 200
    assert skipped.status_code == 200 and skipped.json()["game"]["pending"] is None


async def test_an_overdue_timer_ends_the_turn_even_when_the_action_fails(
    table: Table, clock: Clock
) -> None:
    rules = {"timerSec": 60, "clueEntry": "typed", "packs": ["test"], "bestOf": 1}
    state, _ = await start(table, rules=rules)
    turn = state["turn"]
    state = await _clue(table, state, 1)
    early = await act(table, "host", state, "timeout")
    clock.advance(61)

    failed = await act(table, "host", state, "endTurn")
    settled = failed.json()["game"]["game"]
    timed = await act(table, "host", settled, "timeout")

    _error(early, 422, "invalid_action", "too-early")
    _error(failed, 422, "invalid_action", "no-clue")
    assert settled["rev"] == state["rev"] + 1 and settled["turn"] != turn
    assert (await read(table, state["id"]))["rev"] == settled["rev"]
    assert settled["log"][-1]["type"] == "timeout"
    _error(timed, 422, "invalid_action", "too-early")


@pytest.mark.usefixtures("clock")
async def test_a_malformed_action_is_invalid(table: Table) -> None:
    state, _ = await start(table)
    path = f"/api/ishaara/games/{state['id']}/actions"

    no_id = await table.clients["host"].post(path, json={"rev": state["rev"], "type": "endTurn"})
    bad_type = await table.clients["host"].post(
        path, json={"actionId": str(uuid.uuid4()), "rev": state["rev"], "type": "cheat"}
    )

    assert (no_id.status_code, no_id.json()["code"]) == (422, "invalid")
    assert (bad_type.status_code, bad_type.json()["code"]) == (422, "invalid")
    await _unchanged(table, state)


@pytest.mark.usefixtures("clock")
async def test_actions_are_rate_limited_per_game(
    table: Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, _ = await start(table)
    monkeypatch.setattr(rate_limit, "ISHAARA_ACTION", rate_limit.Limit("ishaara-action", 2, 60))

    codes = [(await act(table, "host", state, "timeout")).status_code for _ in range(3)]

    assert codes == [422, 422, 429]
