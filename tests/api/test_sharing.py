import uuid
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.time import now_utc, to_ms
from app.settings import settings
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload


async def _game(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    owner = await make_user(session, email="owner@example.com", player_name="Owner")
    await sign_in(client, owner)
    players = [
        await make_player(session, name=f"Pub{k}", first_name=f"Secret{k}") for k in range(3)
    ]
    payload = make_payload(
        [str(p.id) for p in players], start_ms=to_ms(now_utc()) - 60 * MINUTE, rounds=5
    )
    assert (await client.put(f"/api/games/{payload['syncId']}", json=payload)).status_code == 200
    return payload


async def _link(client: httpx.AsyncClient, game_id: str, kind: str) -> dict[str, Any]:
    response = await client.post(f"/api/games/{game_id}/links", json={"kind": kind})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_creating_a_link_returns_token_url_and_qr_and_reuses_the_active_one(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    game_id = payload["syncId"]

    first = await _link(client, game_id, "live")
    again = await _link(client, game_id, "live")
    recap = await _link(client, game_id, "recap")

    assert len(first["token"]) == 22
    assert first["url"] == f"{settings.APP_ORIGIN}/l/{first['token']}"
    assert first["qrSvgUrl"].endswith(f"/api/games/{game_id}/links/live/qr.svg")
    assert again["token"] == first["token"]
    assert recap["url"] == f"{settings.APP_ORIGIN}/r/{recap['token']}"
    summary = (await client.get(f"/api/games/{game_id}")).json()["summary"]
    assert summary["live"] is True


async def test_qr_code_is_an_svg_of_the_link(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    missing = await client.get(f"/api/games/{payload['syncId']}/links/live/qr.svg")
    await _link(client, payload["syncId"], "live")

    response = await client.get(f"/api/games/{payload['syncId']}/links/live/qr.svg")

    assert missing.status_code == 404
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.text.startswith("<svg")


async def test_only_the_owner_or_admin_can_share(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    client.cookies.clear()
    await sign_in(client, await make_user(session, email="x@example.com", player_name="X"))

    create = await client.post(f"/api/games/{payload['syncId']}/links", json={"kind": "live"})
    revoke = await client.delete(f"/api/games/{payload['syncId']}/links/live")
    unknown = await client.post(f"/api/games/{uuid.uuid4()}/links", json={"kind": "live"})

    assert create.status_code == 403
    assert revoke.status_code == 403
    assert unknown.status_code == 404


async def test_public_live_shows_the_game_without_private_details(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    link = await _link(client, payload["syncId"], "live")
    client.cookies.clear()

    response = await client.get(f"/api/public/live/{link['token']}")

    assert response.status_code == 200
    body = response.json()
    assert body["game"]["syncId"] == payload["syncId"]
    assert body["summary"]["owner"] == {"id": None, "name": "Owner"}
    assert isinstance(body["updatedAt"], int)
    assert isinstance(body["scorerSeenAt"], int)
    assert "owner@example.com" not in response.text
    assert "Secret" not in response.text


async def test_public_links_of_the_wrong_kind_unknown_revoked_or_deleted(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    live = await _link(client, payload["syncId"], "live")
    recap = await _link(client, payload["syncId"], "recap")

    wrong_kind = await client.get(f"/api/public/recap/{live['token']}")
    unknown = await client.get("/api/public/live/notarealtoken12345")
    await client.delete(f"/api/games/{payload['syncId']}/links/live")
    revoked = await client.get(f"/api/public/live/{live['token']}")
    await client.delete(f"/api/games/{payload['syncId']}")
    deleted = await client.get(f"/api/public/recap/{recap['token']}")

    assert wrong_kind.status_code == 404
    assert unknown.status_code == 404
    assert revoked.status_code == 410
    assert revoked.json()["code"] == "gone"
    assert deleted.status_code == 410


async def test_public_recap_includes_the_games_achievements(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    link = await _link(client, payload["syncId"], "recap")

    response = await client.get(f"/api/public/recap/{link['token']}")

    body = response.json()
    assert body["summary"]["owner"]["id"] is None
    assert {a["code"] for a in body["achievements"]} >= {"winner"}


async def test_recap_image_is_a_png_cached_by_token_and_rev(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    link = await _link(client, payload["syncId"], "recap")

    response = await client.get(f"/api/public/recap/{link['token']}/og.png")
    cached = await client.get(f"/api/public/recap/{link['token']}/og.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (Path(settings.OG_CACHE_DIR) / f"{link['token']}.1.--.png").exists()
    assert cached.content == response.content


async def test_preview_pages_carry_open_graph_tags_and_redirect_into_the_app(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _game(client, session)
    recap = await _link(client, payload["syncId"], "recap")
    live = await _link(client, payload["syncId"], "live")

    recap_page = await client.get(f"/r/{recap['token']}")
    live_page = await client.get(f"/l/{live['token']}")
    missing = await client.get("/r/unknown-token")

    assert recap_page.status_code == 200
    assert 'property="og:title"' in recap_page.text
    assert f"/api/public/recap/{recap['token']}/og.png" in recap_page.text
    assert f'content="0;url=/?recap={recap["token"]}"' in recap_page.text
    assert f'content="0;url=/?live={live["token"]}"' in live_page.text
    assert 'property="og:site_name" content="Baithak"' in recap_page.text
    assert missing.status_code == 404
    assert 'content="0;url=/"' in missing.text


async def _open_game(client: httpx.AsyncClient, session: AsyncSession) -> dict[str, Any]:
    """A game still being played: reactions only make sense while it goes on."""
    owner = await make_user(session, email="host@example.com", player_name="Host")
    await sign_in(client, owner)
    players = [await make_player(session, name=f"Open{k}") for k in range(3)]
    payload = make_payload(
        [str(p.id) for p in players],
        start_ms=to_ms(now_utc()) - 20 * MINUTE,
        rounds=3,
        finished=False,
    )
    assert (await client.put(f"/api/games/{payload['syncId']}", json=payload)).status_code == 200
    return payload


async def test_the_scorer_switches_reactions_off_and_keeps_the_table_to_members(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _open_game(client, session)
    game_id = payload["syncId"]
    no_link = await client.patch(f"/api/games/{game_id}/links/live", json={"reactions": False})
    link = await _link(client, game_id, "live")
    assert (link["reactions"], link["membersOnly"], link["expiresAt"]) == (True, False, None)

    changed = await client.patch(
        f"/api/games/{game_id}/links/live", json={"reactions": False, "membersOnly": True}
    )
    member = await client.get(f"/api/public/live/{link['token']}")
    client.cookies.clear()
    stranger = await client.get(f"/api/public/live/{link['token']}")
    stranger_events = await client.get(f"/api/public/live/{link['token']}/events")
    other = await make_user(session, email="nosy@example.com", player_name="Nosy")
    await sign_in(client, other)
    not_theirs = await client.patch(f"/api/games/{game_id}/links/live", json={"reactions": True})

    assert no_link.status_code == 404
    assert changed.status_code == 200
    assert (changed.json()["reactions"], changed.json()["membersOnly"]) == (False, True)
    assert member.status_code == 200 and member.json()["reactions"] is False
    assert (stranger.status_code, stranger.json()["code"]) == (401, "members_only")
    assert stranger_events.status_code == 401
    assert not_theirs.status_code == 403


async def test_a_live_link_expires_two_hours_after_the_game_ends(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    owner = await make_user(session, email="late@example.com", player_name="Late")
    await sign_in(client, owner)
    players = [await make_player(session, name=f"Late{k}") for k in range(3)]
    payload = make_payload(
        [str(p.id) for p in players], start_ms=to_ms(now_utc()) - 300 * MINUTE, rounds=4
    )
    await client.put(f"/api/games/{payload['syncId']}", json=payload)
    link = await _link(client, payload["syncId"], "live")
    recap = await _link(client, payload["syncId"], "recap")

    live = await client.get(f"/api/public/live/{link['token']}")
    still_recap = await client.get(f"/api/public/recap/{recap['token']}")

    assert link["expiresAt"] is not None and link["expiresAt"] < to_ms(now_utc())
    assert (live.status_code, live.json()["code"]) == (410, "gone")
    assert still_recap.status_code == 200


async def test_spectator_reactions_are_checked_limited_and_off_once_the_game_ends(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    payload = await _open_game(client, session)
    link = await _link(client, payload["syncId"], "live")
    recap = await _link(client, payload["syncId"], "recap")
    url = f"/api/public/live/{link['token']}/reactions"
    client.cookies.clear()

    first = await client.post(url, json={"kind": "fire"})
    too_soon = await client.post(url, json={"kind": "clap"})
    unknown = await client.post(url, json={"kind": "poop"})
    body = (await client.get(f"/api/public/live/{link['token']}")).json()

    assert first.status_code == 204
    assert (too_soon.status_code, too_soon.json()["code"]) == (429, "rate_limited")
    assert unknown.status_code == 422
    assert body["reactions"] is True and body["recapToken"] == recap["token"]

    owner = await make_user(session, email="host2@example.com", player_name="Host2")
    await sign_in(client, owner)
    ended = await _game(client, session)
    ended_link = await _link(client, ended["syncId"], "live")
    off = await client.post(
        f"/api/public/live/{ended_link['token']}/reactions", json={"kind": "fire"}
    )
    assert (off.status_code, off.json()["code"]) == (409, "reactions_off")
