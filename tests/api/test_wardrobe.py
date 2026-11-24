"""The Wardrobe, the Bazaar and gifts (rewards finish §3-§5)."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, Activity, Player, SikkeEntry, SikkeSpend, User
from app.domain.time import now_utc, to_ms
from app.domain.wardrobe import CATALOGUE, Rarity, week_of, weekly_stock
from app.services.merge import MergeService
from app.services.sikke import SikkeService
from app.settings import settings
from tests.factories import make_player, make_user, sign_in


async def _earn(session: AsyncSession, player_id: uuid.UUID, amount: int) -> None:
    session.add(
        SikkeEntry(
            player_id=player_id,
            key=f"test:{uuid.uuid4()}",
            kind="finish",
            amount=amount,
            earned_at=now_utc(),
        )
    )
    await session.flush()


async def _me(
    client: httpx.AsyncClient, session: AsyncSession, *, sikke: int = 0, email: str = "me@x.com"
) -> User:
    user = await make_user(session, email=email, player_name=f"P{uuid.uuid4().hex[:6]}")
    assert user.player_id is not None
    if sikke:
        await _earn(session, user.player_id, sikke)
    await sign_in(client, user)
    return user


def _week_items(rarity: Rarity) -> list[str]:
    week = week_of(to_ms(now_utc()), settings.tz)
    return [i.id for i in weekly_stock(week) if i.rarity == rarity]


def _off_week_rare() -> str:
    stocked = set(_week_items(Rarity.RARE))
    return next(i.id for i in CATALOGUE if i.rarity == Rarity.RARE and i.id not in stocked)


async def _buy(client: httpx.AsyncClient, item: str, v: int = 0, **extra: Any) -> httpx.Response:
    body = {"itemId": item, "v": v, "purchaseId": str(extra.pop("pid", uuid.uuid4()))}
    return await client.post("/api/bazaar/purchases", json=body)


# --- Wardrobe ---------------------------------------------------------------------------------


async def test_wardrobe_needs_a_session(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/me/wardrobe")).status_code == 401
    assert (await client.get("/api/bazaar")).status_code == 401
    assert (await client.get("/api/me/gifts")).status_code == 401


async def test_an_account_without_a_player_gets_no_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="np@x.com", player_name=None)
    await sign_in(client, user)

    for response in (
        await client.get("/api/me/wardrobe"),
        await client.put("/api/me/wardrobe/look", json={}),
        await client.get("/api/bazaar"),
        await _buy(client, "kurta"),
        await client.get("/api/me/gifts"),
    ):
        assert (response.status_code, response.json()["code"]) == (409, "no_player")


async def test_a_new_player_owns_nothing_and_has_three_empty_slots(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session)

    body = (await client.get("/api/me/wardrobe")).json()

    assert body == {
        "owned": [],
        "look": {},
        "saved": [None, None, None],
        "balance": {"earned": 0, "spent": 0, "balance": 0},
    }


async def test_level_gifts_and_honours_are_owned(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _me(client, session, sikke=1600)
    assert user.player_id is not None
    session.add(
        Achievement(
            game="spades", player_id=user.player_id, code="season_champion", earned_at=now_utc()
        )
    )
    await session.flush()

    owned = (await client.get("/api/me/wardrobe")).json()["owned"]

    assert owned == [
        {"id": "kurta", "v": 0, "source": "level", "at": None},
        {"id": "cricket_cap", "v": 0, "source": "level", "at": None},
        {"id": "champions_shaal", "v": 0, "source": "honour", "at": None},
    ]


async def test_wearing_a_look_shows_on_the_player_and_bumps_it(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _me(client, session, sikke=600)
    before = (await client.get(f"/api/players/{user.player_id}")).json()["player"]
    look = {"outfit": {"id": "kurta", "v": 0}}

    response = await client.put("/api/me/wardrobe/look", json=look)

    assert (response.status_code, response.json()) == (200, look)
    player = (await client.get(f"/api/players/{user.player_id}")).json()["player"]
    assert player["look"] == look
    assert player["updatedAt"] >= before["updatedAt"]
    assert before["look"] is None
    assert (await client.get("/api/me/wardrobe")).json()["look"] == look


@pytest.mark.parametrize(
    ("look", "slot"),
    [
        ({"outfit": {"id": "sherwani", "v": 0}}, "outfit"),
        ({"headwear": {"id": "kurta", "v": 0}}, "headwear"),
        ({"outfit": {"id": "kurta", "v": 0, "number": 4}}, "outfit"),
    ],
)
async def test_a_look_with_an_item_not_owned_is_refused(
    client: httpx.AsyncClient, session: AsyncSession, look: dict[str, Any], slot: str
) -> None:
    await _me(client, session, sikke=600)

    response = await client.put("/api/me/wardrobe/look", json=look)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_look"
    assert response.json()["slot"] == slot


async def test_a_jersey_carries_its_number(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _me(client, session)
    assert user.player_id is not None
    session.add(
        SikkeSpend(
            player_id=user.player_id,
            kind="purchase",
            item_id="jersey",
            variant=1,
            amount=350,
            idempotency_key="seed",
        )
    )
    await session.flush()
    look = {"outfit": {"id": "jersey", "v": 1, "number": 7}}

    response = await client.put("/api/me/wardrobe/look", json=look)

    assert (response.status_code, response.json()) == (200, look)


async def test_saved_looks_may_hold_unowned_items_and_can_be_cleared(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session)
    body = {"name": "Shaadi", "look": {"outfit": {"id": "sherwani", "v": 2}}}

    saved = await client.put("/api/me/wardrobe/saved/1", json=body)
    wardrobe = (await client.get("/api/me/wardrobe")).json()
    cleared = await client.delete("/api/me/wardrobe/saved/1")

    assert (saved.status_code, saved.json()) == (200, body)
    assert wardrobe["saved"] == [None, body, None]
    assert cleared.status_code == 204
    assert (await client.get("/api/me/wardrobe")).json()["saved"] == [None, None, None]


async def test_a_saved_look_must_use_real_items_and_slots(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session)

    bad_item = await client.put(
        "/api/me/wardrobe/saved/0", json={"name": "x", "look": {"frame": {"id": "nope", "v": 0}}}
    )
    bad_slot = await client.put("/api/me/wardrobe/saved/3", json={"name": "x", "look": {}})
    long_name = await client.put("/api/me/wardrobe/saved/0", json={"name": "x" * 21, "look": {}})

    assert (bad_item.status_code, bad_item.json()["code"]) == (422, "invalid_look")
    assert (bad_slot.status_code, bad_slot.json()["code"]) == (422, "invalid")
    assert (long_name.status_code, long_name.json()["code"]) == (422, "invalid")


# --- Bazaar -----------------------------------------------------------------------------------


async def test_the_bazaar_lists_this_weeks_six_and_every_common(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session, sikke=600)
    week = week_of(to_ms(now_utc()), settings.tz)

    body = (await client.get("/api/bazaar")).json()

    assert body["week"] == week.key
    assert body["refreshesAt"] == week.refreshes_at
    assert [i["id"] for i in body["weekly"]] == [i.id for i in weekly_stock(week)]
    assert {i["id"] for i in body["always"]} == {
        i.id for i in CATALOGUE if i.rarity == Rarity.COMMON
    }
    kurta = next(i for i in body["always"] if i["id"] == "kurta")
    assert kurta == {"id": "kurta", "price": 100, "owned": [0]}
    assert body["balance"] == {"earned": 600, "spent": 0, "balance": 600}


async def test_buying_spends_the_balance_and_owns_the_item(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _me(client, session, sikke=1000)
    epic = _week_items(Rarity.EPIC)[0]

    response = await _buy(client, epic)

    assert response.status_code == 200
    body = response.json()
    assert body["item"]["id"] == epic
    assert (body["item"]["v"], body["item"]["source"]) == (0, "bought")
    assert body["balance"] == {"earned": 1000, "spent": 750, "balance": 250}
    owned = (await client.get("/api/me/wardrobe")).json()["owned"]
    assert {"id": epic, "v": 0} in [{"id": o["id"], "v": o["v"]} for o in owned]
    wallet = (await client.get(f"/api/players/{user.player_id}/sikke")).json()
    assert (wallet["balance"], wallet["spent"], wallet["lifetime"]) == (250, 750, 1000)


async def test_the_same_purchase_id_returns_the_first_purchase(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session, sikke=1000)
    pid = uuid.uuid4()

    first = await _buy(client, "hoodie", pid=pid)
    again = await _buy(client, "headphones", pid=pid)

    assert first.status_code == again.status_code == 200
    assert again.json()["item"] == first.json()["item"]
    assert again.json()["balance"]["spent"] == 100
    assert await session.scalar(select(func.count()).select_from(SikkeSpend)) == 1


async def test_a_colour_of_a_new_item_brings_variant_zero_and_costs_fifty_more(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session, sikke=1000)

    response = await _buy(client, "hoodie", 2)
    another = await _buy(client, "hoodie", 3)

    assert response.json()["balance"]["spent"] == 150
    assert another.json()["balance"]["spent"] == 200
    owned = (await client.get("/api/bazaar")).json()["always"]
    assert next(i for i in owned if i["id"] == "hoodie")["owned"] == [0, 2, 3]


async def test_buying_an_owned_pair_is_refused(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session, sikke=1000)
    await _buy(client, "hoodie", 1)

    owned_v0 = await _buy(client, "hoodie", 0)
    owned_v1 = await _buy(client, "hoodie", 1)

    assert (owned_v0.status_code, owned_v0.json()["code"]) == (409, "already_owned")
    assert (owned_v1.status_code, owned_v1.json()["code"]) == (409, "already_owned")


async def test_a_rare_out_of_stock_is_refused_unless_another_colour_is_owned(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _me(client, session, sikke=1000)
    assert user.player_id is not None
    rare = _off_week_rare()

    refused = await _buy(client, rare)

    assert (refused.status_code, refused.json()["code"]) == (422, "not_in_stock")
    multi = next(
        i.id
        for i in CATALOGUE
        if i.rarity == Rarity.RARE and i.variants > 1 and i.id not in _week_items(Rarity.RARE)
    )
    session.add(
        SikkeSpend(
            player_id=user.player_id,
            kind="purchase",
            item_id=multi,
            variant=0,
            amount=300,
            idempotency_key="seed",
        )
    )
    await session.flush()
    variant = await _buy(client, multi, 1)
    assert variant.status_code == 200, variant.text
    assert variant.json()["balance"]["spent"] == 350


async def test_buying_without_enough_sikke_says_how_much(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session, sikke=99)

    response = await _buy(client, "hoodie")

    assert response.status_code == 422
    assert response.json() | {"message": ""} == {
        "code": "not_enough_sikke",
        "message": "",
        "balance": 99,
        "price": 100,
    }


@pytest.mark.parametrize("item", ["champions_shaal", "pehle_waale", "no_such_thing"])
async def test_honour_and_unknown_items_are_not_for_sale(
    client: httpx.AsyncClient, session: AsyncSession, item: str
) -> None:
    await _me(client, session, sikke=5000)

    response = await _buy(client, item)

    assert (response.status_code, response.json()["code"]) == (422, "not_for_sale")


async def test_a_variant_the_item_lacks_is_invalid(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await _me(client, session, sikke=5000)

    response = await _buy(client, "whistle", 1)

    assert (response.status_code, response.json()["code"]) == (422, "invalid")


async def test_a_recompute_never_touches_spends_and_a_negative_balance_blocks_buying(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await _me(client, session, sikke=300)
    assert user.player_id is not None
    await _buy(client, "hoodie")
    await _buy(client, "headphones")

    # The seeded entry isn't backed by any game, so a recompute removes it.
    await SikkeService(session).recompute_players({user.player_id})

    body = (await client.get("/api/bazaar")).json()
    assert body["balance"] == {"earned": 0, "spent": 200, "balance": -200}
    refused = await _buy(client, "smoke")
    assert refused.json()["code"] == "not_enough_sikke"
    assert refused.json()["balance"] == -200


# --- Gifts ------------------------------------------------------------------------------------


async def _friend(session: AsyncSession, sender: User, *, linked: bool = True) -> Player:
    """A player in the sender's circle (created by them), linked to an account unless not."""
    if not linked:
        return await make_player(session, name=f"F{uuid.uuid4().hex[:6]}", created_by=sender.id)
    other = await make_user(session, email=f"{uuid.uuid4().hex[:8]}@x.com", player_name="Dev")
    assert other.player_id is not None
    player = await session.get(Player, other.player_id)
    assert player is not None
    player.created_by = sender.id
    await session.flush()
    return player


async def _gift(
    client: httpx.AsyncClient, item: str, to: uuid.UUID, v: int = 0, gid: uuid.UUID | None = None
) -> httpx.Response:
    body = {"itemId": item, "v": v, "toPlayerId": str(to), "giftId": str(gid or uuid.uuid4())}
    return await client.post("/api/gifts", json=body)


async def test_a_gift_is_owned_at_once_and_paid_by_the_sender(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[tuple[uuid.UUID, str, dict[str, Any]]] = []

    async def _notify(_: AsyncSession, user_id: uuid.UUID, event: str, data: Any) -> None:
        told.append((user_id, event, data))

    monkeypatch.setattr("app.services.gifts.notify_user", _notify)
    sender = await _me(client, session, sikke=1000)
    friend = await _friend(session, sender)
    gid = uuid.uuid4()

    response = await _gift(client, "sherwani", friend.id, 1, gid)

    assert response.status_code == 200, response.text
    gift = response.json()
    assert gift["id"] == str(gid)
    assert gift["item"] == {"id": "sherwani", "v": 1}
    assert gift["from"]["id"] == str(sender.player_id)
    assert gift["to"]["id"] == str(friend.id)
    assert gift["openedAt"] is None
    balance = (await client.get("/api/bazaar")).json()["balance"]
    assert balance == {"earned": 1000, "spent": 800, "balance": 200}
    friend_user = await session.scalar(select(User).where(User.player_id == friend.id))
    assert friend_user is not None
    assert told == [(friend_user.id, "gift-received", {"gift": gift})]
    again = await _gift(client, "kurta", friend.id, 0, gid)
    assert again.json() == gift


async def test_a_gift_is_priced_on_the_receivers_wardrobe(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    sender = await _me(client, session, sikke=1000)
    friend = await _friend(session, sender, linked=False)
    session.add(
        SikkeSpend(
            player_id=friend.id,
            kind="purchase",
            item_id="sherwani",
            variant=0,
            amount=750,
            idempotency_key="seed",
        )
    )
    await session.flush()

    response = await _gift(client, "sherwani", friend.id, 2)

    assert response.status_code == 200, response.text
    assert (await client.get("/api/bazaar")).json()["balance"]["spent"] == 50


async def test_gift_rules(client: httpx.AsyncClient, session: AsyncSession) -> None:
    sender = await _me(client, session, sikke=200)
    assert sender.player_id is not None
    friend = await _friend(session, sender, linked=False)
    stranger = await make_player(session, name="Stranger")
    await _gift(client, "kurta", friend.id)

    cases = {
        "not_in_circle": await _gift(client, "kurta", stranger.id),
        "unknown": await _gift(client, "kurta", uuid.uuid4()),
        "self_gift": await _gift(client, "kurta", sender.player_id),
        "already_owned": await _gift(client, "kurta", friend.id),
        "not_enough_sikke": await _gift(client, "sherwani", friend.id),
        "not_for_sale": await _gift(client, "pehle_waale", friend.id),
    }

    codes = {k: (r.status_code, r.json()["code"]) for k, r in cases.items()}
    assert codes == {
        "not_in_circle": (422, "not_in_circle"),
        "unknown": (422, "not_in_circle"),
        "self_gift": (422, "self_gift"),
        "already_owned": (409, "already_owned"),
        "not_enough_sikke": (422, "not_enough_sikke"),
        "not_for_sale": (422, "not_for_sale"),
    }


async def test_another_senders_gift_id_is_a_conflict(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    sender = await _me(client, session, sikke=500)
    friend = await _friend(session, sender, linked=False)
    gid = uuid.uuid4()
    await _gift(client, "kurta", friend.id, gid=gid)
    other = await _me(client, session, sikke=500, email="other@x.com")
    other_friend = await _friend(session, other, linked=False)

    response = await _gift(client, "kurta", other_friend.id, gid=gid)

    assert (response.status_code, response.json()["code"]) == (409, "id_conflict")


async def test_pending_gifts_and_opening_one_posts_the_feed_item(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    sender = await _me(client, session, sikke=500)
    friend = await _friend(session, sender, linked=False)
    gift = (await _gift(client, "smoke", friend.id, 1)).json()

    pending = await client.get("/api/gifts/pending", params={"players": f"{friend.id}"})
    opened = await client.post(f"/api/gifts/{gift['id']}/open")
    twice = await client.post(f"/api/gifts/{gift['id']}/open")
    after = await client.get("/api/gifts/pending", params={"players": f"{friend.id}"})

    assert [g["id"] for g in pending.json()["items"]] == [gift["id"]]
    assert opened.status_code == 200
    assert opened.json()["openedAt"] is not None
    assert twice.json()["openedAt"] == opened.json()["openedAt"]
    assert after.json() == {"items": []}
    feed = list((await session.execute(select(Activity).where(Activity.kind == "gift"))).scalars())
    assert len(feed) == 1
    assert feed[0].player_id == sender.player_id
    assert feed[0].data == {
        "giftId": gift["id"],
        "itemId": "smoke",
        "v": 1,
        "toPlayerId": str(friend.id),
    }


async def test_only_the_receiver_or_their_circle_opens_a_gift(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    sender = await _me(client, session, sikke=500)
    friend = await _friend(session, sender, linked=False)
    gift = (await _gift(client, "smoke", friend.id)).json()
    await _me(client, session, email="outsider@x.com")

    response = await client.post(f"/api/gifts/{gift['id']}/open")
    unknown = await client.post(f"/api/gifts/{uuid.uuid4()}/open")

    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert unknown.status_code == 404


@pytest.mark.parametrize("players", ["", "nope", ",".join(str(uuid.uuid4()) for _ in range(13))])
async def test_pending_gifts_take_one_to_twelve_ids(
    client: httpx.AsyncClient, session: AsyncSession, players: str
) -> None:
    await _me(client, session)

    response = await client.get("/api/gifts/pending", params={"players": players})

    assert (response.status_code, response.json()["code"]) == (422, "invalid")


async def test_my_gifts_list_sent_and_received_newest_first(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    sender = await _me(client, session, sikke=1000)
    friends = [await _friend(session, sender, linked=False) for _ in range(3)]
    sent = [(await _gift(client, "smoke", f.id)).json()["id"] for f in friends]

    first = (await client.get("/api/me/gifts", params={"limit": 2})).json()
    rest = (
        await client.get("/api/me/gifts", params={"limit": 2, "cursor": first["nextCursor"]})
    ).json()

    ids = [g["id"] for g in first["items"] + rest["items"]]
    assert sorted(ids) == sorted(sent)
    assert len(first["items"]) == 2
    assert rest["nextCursor"] is None


async def test_a_merge_moves_spends_and_gifts_to_the_kept_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    sender = await _me(client, session, sikke=500)
    friend = await _friend(session, sender, linked=False)
    await _gift(client, "smoke", friend.id)
    keep = await make_player(session, name="Kept")
    admin = await make_user(session, email="admin@x.com", role="admin", player_name=None)

    await MergeService(session).merge(friend.id, keep.id, admin)

    pending = await client.get("/api/gifts/pending", params={"players": str(keep.id)})
    assert [g["to"]["id"] for g in pending.json()["items"]] == [str(keep.id)]
