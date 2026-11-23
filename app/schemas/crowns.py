"""Crowns, their reigns, and a write's status changes and bounties (rewards plan §6)."""

import uuid
from typing import Literal

from pydantic import Field

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


class StatusOut(CamelModel):
    """What one Kachuful write changed: titles and bounties (empty lists when nothing moved)."""

    changes: list[StatusChangeOut] = Field(..., description="Titles that changed hands")
    bounties: list[BountyOut] = Field(..., description="Bounties paid by this game")
