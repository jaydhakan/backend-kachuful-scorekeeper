import uuid
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminAudit, Game, GamePlayer, Player, Session, User
from app.domain.time import now_utc, to_ms
from app.services.games import GamesService
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import make_payload


async def _admin(client: httpx.AsyncClient, session: AsyncSession) -> User:
    admin = await make_user(session, email="admin@example.com", role="admin", player_name="Jay")
    await sign_in(client, admin)
    return admin


async def _score(session: AsyncSession, owner: User, seats: list[uuid.UUID], **kw: Any) -> Game:
    payload = make_payload([str(s) for s in seats], start_ms=to_ms(now_utc()) - 3_600_000, **kw)
    await GamesService(session).upload(uuid.UUID(payload["syncId"]), payload, owner)
    row = await session.get(Game, uuid.UUID(payload["syncId"]))
    assert row is not None
    return row


async def _table(session: AsyncSession) -> tuple[User, User, list[Player]]:
    """Riya scores two games she plays in; Kabir scores one she plays in."""
    riya = await make_user(session, email="riya@example.com", player_name="Riya")
    kabir = await make_user(session, email="kabir@example.com", player_name="Kabir")
    others = [await make_player(session, name=n) for n in ("Asha", "Bo")]
    assert riya.player_id and kabir.player_id
    await _score(session, riya, [riya.player_id, others[0].id, others[1].id], rounds=3, seed=1)
    await _score(session, riya, [riya.player_id, others[0].id, kabir.player_id], rounds=2, seed=2)
    await _score(session, kabir, [kabir.player_id, riya.player_id, others[1].id], rounds=4, seed=3)
    return riya, kabir, others


async def test_deletion_preview_counts_what_would_go(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya, _, _ = await _table(session)
    await sign_in(client, riya)
    await _admin(client, session)

    response = await client.get(f"/api/admin/users/{riya.id}/deletion-preview")

    assert response.status_code == 200
    assert response.json() == {
        "games": 2,
        "rounds": 5,
        "otherPlayers": 3,
        "sessions": 1,
        "invites": 0,
        "hasPhoto": False,
        "playerInOthersGames": True,
    }


async def test_clearing_a_users_games_soft_deletes_them_and_keeps_the_account(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya, kabir, _ = await _table(session)
    await _admin(client, session)

    response = await client.delete(f"/api/admin/users/{riya.id}/games")

    assert response.status_code == 200
    assert response.json() == {"games": 2}
    live = select(func.count()).select_from(Game).where(Game.deleted_at.is_(None))
    assert (await session.execute(live.where(Game.owner_id == riya.id))).scalar_one() == 0
    assert (await session.execute(live.where(Game.owner_id == kabir.id))).scalar_one() == 1
    assert await session.get(User, riya.id) is not None
    audit = (await session.execute(select(AdminAudit))).scalars().one()
    assert (audit.action, audit.target, audit.details) == (
        "clear_games",
        "riya@example.com",
        {"games": 2},
    )


async def test_deleting_a_user_removes_the_account_and_anonymises_a_player_others_use(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    riya, kabir, _ = await _table(session)
    riya_id, player_id = riya.id, riya.player_id
    assert player_id is not None
    kabirs = (await session.execute(select(Game).where(Game.owner_id == kabir.id))).scalar_one()
    totals_before = kabirs.summary
    admin = await _admin(client, session)

    admin_id = admin.id

    response = await client.delete(f"/api/admin/users/{riya_id}")

    assert response.status_code == 204
    session.expire_all()
    assert await session.get(User, riya_id) is None
    sessions = select(func.count()).select_from(Session).where(Session.user_id == riya_id)
    assert (await session.execute(sessions)).scalar_one() == 0
    hers = (await session.execute(select(Game).where(Game.owner_id == admin_id))).scalars().all()
    assert len(hers) == 2 and all(g.deleted_at is not None for g in hers)
    player = await session.get(Player, player_id)
    assert player is not None
    assert (player.display_name, player.email, player.anonymised_at is not None) == (
        "Deleted player",
        None,
        True,
    )
    # Kabir's game is untouched: same seat, same results.
    await session.refresh(kabirs)
    assert kabirs.deleted_at is None and kabirs.summary == totals_before
    seats = select(func.count()).select_from(GamePlayer).where(GamePlayer.player_id == player_id)
    assert (await session.execute(seats)).scalar_one() == 1
    login = await client.post(
        "/api/auth/login", json={"email": "riya@example.com", "password": "x" * 12}
    )
    assert login.status_code == 401
    audit = (await session.execute(select(AdminAudit))).scalars().one()
    assert audit.action == "delete_user" and audit.details["anonymised"] is True


async def test_deleting_a_user_whose_player_nobody_else_uses_deletes_the_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    loner = await make_user(session, email="loner@example.com", player_name="Loner")
    player_id = loner.player_id
    await _admin(client, session)

    response = await client.delete(f"/api/admin/users/{loner.id}")

    assert response.status_code == 204
    session.expire_all()
    assert player_id is not None and await session.get(Player, player_id) is None


async def test_an_admin_cant_delete_themself_another_admin_or_nobody(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    other = await make_user(session, email="other@example.com", role="admin", player_name="Oz")
    admin = await _admin(client, session)

    own = await client.delete(f"/api/admin/users/{admin.id}")
    second = await client.delete(f"/api/admin/users/{other.id}")
    nobody = await client.delete(f"/api/admin/users/{uuid.uuid4()}")
    preview = await client.get(f"/api/admin/users/{uuid.uuid4()}/deletion-preview")

    assert (own.status_code, own.json()["code"]) == (409, "cannot_delete_self")
    assert (second.status_code, second.json()["code"]) == (409, "cannot_delete_admin")
    assert nobody.status_code == 404 and preview.status_code == 404
