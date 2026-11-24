"""Gifts (rewards finish §5): items one player sends another from their own balance.

Sending locks both players' rows (fixed order), so neither can spend the same balance twice or
come to own one pair twice. The receiver owns the item at once; opening only marks it shown.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Gift, Player, SikkeSpend, User
from app.domain.time import now_utc, to_ms, to_ms_or_none
from app.domain.wardrobe import price_of, variants_owned
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify_user
from app.logger import get_logger
from app.repositories.achievements import achievements_repository
from app.repositories.circle import circle_repository
from app.repositories.players import PlayerView, players_repository
from app.repositories.users import users_repository
from app.repositories.wardrobe import wardrobe_repository
from app.schemas.common import Page
from app.schemas.wardrobe import GiftIn, GiftItemOut, GiftListOut, GiftOut
from app.services.bazaar import already_owned, not_enough, sellable_item
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import player_summary_out
from app.services.wardrobe import holdings, player_of

logger = get_logger(__name__)

MAX_PENDING_PLAYERS = 12
GIFT_RECEIVED = "gift-received"
# Feed items need a game; a gift belongs to none, so it sits with the app's first game.
FEED_GAME = "kachuful"


def gift_key(gift_id: uuid.UUID) -> str:
    """A gift spend's idempotency key."""
    return f"gift:{gift_id}"


def _not_found() -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, "No such gift.")


def parse_players(text: str) -> list[uuid.UUID]:
    """`?players=<id,id,...>`: 1 to 12 player ids; 422 `invalid {fields}` otherwise."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    try:
        ids = list(dict.fromkeys(uuid.UUID(p) for p in parts))
    except ValueError as exc:
        raise _bad_players("not a list of player ids") from exc
    if not 1 <= len(ids) <= MAX_PENDING_PLAYERS:
        raise _bad_players(f"1 to {MAX_PENDING_PLAYERS} player ids")
    return ids


def _bad_players(reason: str) -> ApiError:
    return ApiError(422, ErrorCode.INVALID, "Bad players.", fields={"players": reason})


def _gift_out(gift: Gift, views: dict[uuid.UUID, PlayerView]) -> GiftOut:
    return GiftOut.model_validate(
        {
            "id": gift.id,
            "item": GiftItemOut(id=gift.item_id, v=gift.variant),
            "from": player_summary_out(views[gift.from_player_id]),
            "to": player_summary_out(views[gift.to_player_id]),
            "sent_at": to_ms(gift.sent_at),
            "opened_at": to_ms_or_none(gift.opened_at),
        }
    )


def _parse_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    at, key = decode_cursor(cursor)
    try:
        return at, uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc


class GiftsService:
    """`/api/gifts` and `/api/me/gifts`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _outs(self, gifts: Sequence[Gift]) -> list[GiftOut]:
        """Gifts on the wire, both players of each read in one query."""
        ids = {p for g in gifts for p in (g.from_player_id, g.to_player_id)}
        views = await players_repository.get_views(self.session, list(ids))
        return [_gift_out(g, views) for g in gifts]

    async def _lock_players(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Player | None]:
        # A fixed order, so a gift each way between two players can't deadlock.
        return {
            pid: await players_repository.get_for_update(self.session, pid) for pid in sorted(ids)
        }

    async def _receiver_id(self, player_id: uuid.UUID) -> uuid.UUID:
        """The receiver (a merged-away id answers for its target); 422 `not_in_circle` if unknown."""
        player = await players_repository.get(self.session, player_id)
        if player is None:
            raise _not_in_circle()
        return player.merged_into or player.id

    async def send(self, user: User, body: GiftIn) -> GiftOut:
        """Send a pair to a circle member; idempotent on `giftId`."""
        sender = player_of(user)
        receiver = await self._receiver_id(body.to_player_id)
        await self._lock_players({sender, receiver})
        first = await wardrobe_repository.gift(self.session, body.gift_id)
        if first is not None:
            if first.from_player_id != sender:
                raise ApiError(409, ErrorCode.ID_CONFLICT, "This id is already used.")
            return (await self._outs([first]))[0]
        await self._check_receiver(user, sender, receiver)
        item = sellable_item(body.item_id, body.v)
        receiving = variants_owned((await holdings(self.session, receiver)).pairs, item.id)
        if body.v in receiving:
            raise already_owned()
        # Priced on the receiver's wardrobe, as if they bought it themselves.
        price = price_of(item, body.v, owns_item=bool(receiving))
        paying = await holdings(self.session, sender)
        if paying.balance < price:
            raise not_enough(paying.balance, price)
        gift = await wardrobe_repository.add_gift(
            self.session,
            Gift(
                id=body.gift_id,
                from_player_id=sender,
                to_player_id=receiver,
                sent_by=user.id,
                item_id=item.id,
                variant=body.v,
            ),
        )
        await wardrobe_repository.add_spend(
            self.session,
            SikkeSpend(
                player_id=sender,
                kind="gift",
                item_id=item.id,
                variant=body.v,
                amount=price,
                gift_id=gift.id,
                idempotency_key=gift_key(gift.id),
            ),
        )
        out = (await self._outs([gift]))[0]
        await self._tell_receiver(receiver, out)
        await self.session.commit()
        logger.info("gift sent", extra={"gift_id": str(gift.id), "item": item.id, "amount": price})
        return out

    async def _check_receiver(self, user: User, sender: uuid.UUID, receiver: uuid.UUID) -> None:
        if receiver == sender:
            raise ApiError(422, ErrorCode.SELF_GIFT, "You can't send yourself a gift.")
        if not await circle_repository.contains(self.session, user.id, sender, receiver):
            raise _not_in_circle()

    async def _tell_receiver(self, receiver: uuid.UUID, gift: GiftOut) -> None:
        data = {"gift": gift.model_dump(mode="json")}
        for user_id in await users_repository.ids_for_players(self.session, [receiver]):
            await notify_user(self.session, user_id, GIFT_RECEIVED, data)

    async def pending(self, players: str) -> GiftListOut:
        """Unopened gifts to any of up to 12 players (the table device, as a game starts)."""
        gifts = await wardrobe_repository.pending(self.session, parse_players(players))
        return GiftListOut(items=await self._outs(gifts))

    async def open(self, gift_id: uuid.UUID, user: User) -> GiftOut:
        """Mark a gift shown (idempotent): the receiver's account, or one whose circle has them.
        The first open posts the `gift` feed item."""
        gift = await wardrobe_repository.gift_for_update(self.session, gift_id)
        if gift is None or not await self._may_open(gift, user):
            raise _not_found()
        if gift.opened_at is None:
            gift.opened_at = now_utc()
            await achievements_repository.add_activity(
                self.session,
                [
                    {
                        "game": FEED_GAME,
                        "kind": "gift",
                        "player_id": gift.from_player_id,
                        "data": {
                            "giftId": str(gift.id),
                            "itemId": gift.item_id,
                            "v": gift.variant,
                            "toPlayerId": str(gift.to_player_id),
                        },
                    }
                ],
            )
            await self.session.flush()
        out = (await self._outs([gift]))[0]
        await self.session.commit()
        return out

    async def _may_open(self, gift: Gift, user: User) -> bool:
        if user.player_id == gift.to_player_id:
            return True
        return await circle_repository.contains(
            self.session, user.id, user.player_id, gift.to_player_id
        )

    async def mine(self, user: User, *, cursor: str | None, limit: int) -> Page[GiftOut]:
        """The user's player's sent and received gifts, newest first."""
        player_id = player_of(user)
        before = None if cursor is None else _parse_cursor(cursor)
        rows = await wardrobe_repository.page_for(
            self.session, player_id, before=before, limit=limit + 1
        )
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = encode_cursor(last.sent_at, str(last.id))
        return Page(items=await self._outs(page), next_cursor=next_cursor)


def _not_in_circle() -> ApiError:
    return ApiError(422, ErrorCode.NOT_IN_CIRCLE, "Only people you've played with.")
