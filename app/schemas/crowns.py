"""Crowns, their reigns, and a write's status: changes, bounties and milestones (rewards §6)."""

import uuid
from typing import Any, Literal

from pydantic import Field, SerializationInfo, SerializerFunctionWrapHandler, model_serializer

from app.domain.enums import ProgressionGame
from app.schemas.common import CamelModel
from app.schemas.players import PlayerSummaryOut

CrownKindOut = Literal["medal1", "medal2", "medal3", "sitara", "legend"]


class CrownOut(CamelModel):
    """One title now; `holders` is empty when nobody holds it."""

    kind: CrownKindOut = Field(..., description="medal1 | medal2 | medal3 | sitara | legend")
    holders: list[PlayerSummaryOut] = Field(..., description="Who holds it (ties share)")
    since: int | None = Field(..., description="Epoch ms the holders took it; null if never held")
    since_game: uuid.UUID | None = Field(..., description="The game that changed it, if any")
    defences: int = Field(..., description="Always 0: no title counts defences")


class CrownsOut(CamelModel):
    """`GET /api/crowns`: every title of a game, in a fixed order."""

    items: list[CrownOut] = Field(..., description="All five titles")


class CrownReignOut(CamelModel):
    """One closed reign."""

    kind: CrownKindOut = Field(..., description="The title")
    holders: list[PlayerSummaryOut] = Field(..., description="Who held it")
    since: int = Field(..., description="Epoch ms the reign began")
    until: int = Field(..., description="Epoch ms it ended")
    defences: int = Field(..., description="Always 0: no title counts defences")


class StatusChangeOut(CamelModel):
    """A title changing hands in this write; `to` is empty when nobody holds it now."""

    kind: CrownKindOut = Field(..., description="The title")
    from_: list[PlayerSummaryOut] = Field(..., alias="from", description="Who held it before")
    to: list[PlayerSummaryOut] = Field(..., description="Who holds it now")
    defences: int = Field(..., description="Always 0: no title counts defences")


class BountyOut(CamelModel):
    """A bounty this write paid: `winner` finished above `holder`."""

    holder: PlayerSummaryOut = Field(..., description="The Taj holder beaten")
    winner: PlayerSummaryOut = Field(..., description="Who beat them")
    amount: int = Field(..., description="Sikke paid")


class MilestoneOut(CamelModel):
    """A group milestone (the nth finished game) or a first, app-wide."""

    key: str = Field(..., description="`group:all:500`, `group:spades:100`, `first:spades:boston`")
    kind: Literal["group", "first"] = Field(..., description="group | first")
    game: ProgressionGame | None = Field(..., description="The game; null for `group:all`")
    game_id: uuid.UUID = Field(..., description="The game that made it")
    n: int | None = Field(..., description="The game count reached (group only); null for firsts")
    players: list[PlayerSummaryOut] = Field(..., description="Who sat in (or earned) that game")
    at: int = Field(..., description="Epoch ms that game finished")

    @model_serializer(mode="wrap")
    def _keep_nulls(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        # `game` and `n` are null by meaning (`group:all`, a first): kept even when a PUT's
        # response drops its other null fields.
        out: dict[str, Any] = handler(self)
        out.setdefault("game", None if self.game is None else self.game.value)
        out.setdefault("n", self.n)
        return out


class StatusOut(CamelModel):
    """What one write changed: titles, bounties and milestones (empty lists when nothing moved)."""

    changes: list[StatusChangeOut] = Field(..., description="Titles that changed hands")
    bounties: list[BountyOut] = Field(..., description="Bounties paid by this game")
    milestones: list[MilestoneOut] = Field(
        default_factory=list, description="Milestones this write created"
    )
