"""Parity with the app: Kachuful's Giant Killer per case of the `giant-killer` fixture."""

from typing import Any

import pytest

from app.domain.achievements import giant_killers
from app.domain.derive import build_facts
from app.domain.game import game_from_payload
from tests.domain.golden import GAME_NOW, camel, load

CASES: list[dict[str, Any]] = load("giant-killer")["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_giant_killers_match_the_app(case: dict[str, Any]) -> None:
    facts = build_facts([game_from_payload(case["game"])], GAME_NOW)
    held = {pid: m["medal"] for pid, m in case["held"].items()}

    assert {p.player_id: p.place for p in facts.players} == case["places"]
    assert camel(giant_killers(facts.players, held)) == case["expect"]
