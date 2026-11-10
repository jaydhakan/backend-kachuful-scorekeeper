"""Ishaara through the service: over random action sequences, the key never leaks (property)."""

import asyncio
import json
import uuid
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import Game
from app.db.session import build_engine
from app.exceptions import ApiError
from app.schemas.ishaara import ActionIn
from app.services.ishaara_games import IshaaraGamesService
from tests.ishaara_builder import TEAM_WORDS, hides_the_key, service_game, service_table

TYPES = ["clue", "guess", "endTurn", "timeout", "undoLast", "resign", "skip", "notAllowed",
         "penaltyReveal"]  # fmt: skip
WHO = ["host", "sun", "moon"]
_steps = st.lists(
    st.tuples(
        st.sampled_from(TYPES),
        st.integers(min_value=0, max_value=24),
        st.sampled_from([0, 1, 2, 3, "inf"]),
        st.sampled_from(WHO),
    ),
    max_size=40,
)


def _action(step: tuple[str, int, Any, str], state: dict[str, Any]) -> ActionIn:
    type_, card, number, _ = step
    fields: dict[str, Any] = {"actionId": uuid.uuid4(), "rev": state["rev"], "type": type_}
    if type_ == "clue":
        fields |= {"word": TEAM_WORDS[state["turn"]], "number": number}
    elif type_ in ("guess", "penaltyReveal"):
        fields["card"] = card
    elif type_ == "resign":
        fields["team"] = "sun" if card % 2 else "moon"
    return ActionIn.model_validate(fields)


async def _play(steps: list[tuple[str, int, Any, str]]) -> None:
    engine = build_engine(statement_timeout_ms=10000)
    async with engine.connect() as conn:
        trans = await conn.begin()
        factory = async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        async with factory() as session:
            table = await service_table(session)
            game_id, key = await service_game(session, table)
            users = {"host": table.host, "sun": table.sun, "moon": table.moon}
            service = IshaaraGamesService(session)
            for step in steps:
                state = (await service.get(game_id, table.host)).game
                try:
                    out = await service.act(game_id, _action(step, state), users[step[3]])
                    text = out.model_dump_json()
                    shown = out.game
                except ApiError as error:
                    text, shown = json.dumps(error.extra), error.extra["game"]["game"]
                assert hides_the_key(text, shown, key)
                row = await session.get(Game, game_id, populate_existing=True)
                assert row is not None
                assert hides_the_key(json.dumps([row.payload, row.summary]), row.payload, key)
        await trans.rollback()
    await engine.dispose()


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(steps=_steps)
def test_public_state_never_holds_a_face_down_identity(
    steps: list[tuple[str, int, Any, str]],
) -> None:
    asyncio.run(_play(steps))
