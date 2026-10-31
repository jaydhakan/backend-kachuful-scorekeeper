"""Player photos: permissions, limits, re-encoding, caching and where photoUrl shows up."""

import io
import uuid

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Player, User
from app.domain.time import now_utc, to_ms
from app.services.merge import MergeService
from tests.factories import make_player, make_user, sign_in
from tests.game_builder import MINUTE, make_payload


def _jpeg_with_exif(size: tuple[int, int] = (800, 600)) -> bytes:
    image = Image.new("RGB", size, (200, 80, 40))
    exif = Image.Exif()
    exif[0x010E] = "secret-description"
    exif[0x010F] = "SecretCam"
    exif[0x8825] = {1: "N", 2: (12.0, 58.0, 0.0), 3: "E", 4: (77.0, 35.0, 0.0)}
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes())
    return out.getvalue()


def _png(size: tuple[int, int] = (300, 300), mode: str = "RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size).save(out, format="PNG")
    return out.getvalue()


async def _put(
    client: httpx.AsyncClient, player_id: uuid.UUID, data: bytes, kind: str = "image/jpeg"
) -> httpx.Response:
    return await client.put(
        f"/api/players/{player_id}/photo", content=data, headers={"Content-Type": kind}
    )


async def _creator_and_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> tuple[User, Player]:
    user = await make_user(session, email="creator@example.com", player_name="Creator")
    await sign_in(client, user)
    return user, await make_player(session, name="Pic", created_by=user.id)


async def test_upload_reencodes_to_a_small_256px_webp_without_metadata(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, player = await _creator_and_player(client, session)

    response = await _put(client, player.id, _jpeg_with_exif())
    fetched = await client.get(response.json()["photoUrl"])

    assert response.status_code == 200
    assert response.json()["photoUrl"] == f"/api/players/{player.id}/photo?v=1"
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/webp"
    assert fetched.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert len(fetched.content) <= 60_000
    assert b"secret" not in fetched.content.lower() and b"SecretCam" not in fetched.content
    with Image.open(io.BytesIO(fetched.content)) as stored:
        assert stored.format == "WEBP"
        assert stored.size == (256, 256)
        assert len(stored.getexif()) == 0


async def test_each_upload_gets_a_new_url_and_shows_on_the_player(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, player = await _creator_and_player(client, session)

    first = await _put(client, player.id, _png(), "image/png")
    second = await _put(client, player.id, _png(mode="RGB"), "image/png")
    listed = (await client.get("/api/players", params={"limit": 200})).json()["items"]

    assert first.json()["photoUrl"].endswith("?v=1")
    assert second.json()["photoUrl"].endswith("?v=2")
    mine = next(p for p in listed if p["id"] == str(player.id))
    assert mine["photoUrl"] == second.json()["photoUrl"]


async def test_photos_are_public_but_only_editors_can_change_them(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, player = await _creator_and_player(client, session)
    await _put(client, player.id, _png(), "image/png")
    client.cookies.clear()

    public = await client.get(f"/api/players/{player.id}/photo")
    anonymous = await _put(client, player.id, _png(), "image/png")
    await sign_in(client, await make_user(session, email="x@example.com", player_name="X"))
    stranger_put = await _put(client, player.id, _png(), "image/png")
    stranger_delete = await client.delete(f"/api/players/{player.id}/photo")
    client.cookies.clear()
    await sign_in(
        client, await make_user(session, email="a@example.com", role="admin", player_name="A")
    )
    admin_put = await _put(client, player.id, _png(), "image/png")

    assert public.status_code == 200
    assert anonymous.status_code == 401
    assert stranger_put.status_code == 403
    assert stranger_delete.status_code == 403
    assert admin_put.status_code == 200


async def test_the_linked_user_can_set_their_own_photo(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    user = await make_user(session, email="me@example.com", player_name="Me")
    await sign_in(client, user)
    assert user.player_id is not None

    response = await _put(client, user.player_id, _png(), "image/png")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"hello", "text/plain"),
        (b"definitely not an image", "image/png"),
        (_png((8000, 8000), mode="1"), "image/png"),
        (b"GIF89a" + b"\0" * 64, "image/gif"),
    ],
    ids=["text", "garbage", "pixel-bomb", "gif"],
)
async def test_unsupported_or_hostile_images_return_415(
    client: httpx.AsyncClient, session: AsyncSession, data: bytes, kind: str
) -> None:
    _, player = await _creator_and_player(client, session)

    response = await _put(client, player.id, data, kind)

    assert response.status_code == 415
    assert response.json()["code"] == "unsupported_media_type"


async def test_photos_over_two_megabytes_return_413(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, player = await _creator_and_player(client, session)

    response = await _put(client, player.id, b"\xff" * (2 * 1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.json()["code"] == "too_large"


async def test_deleting_the_photo_brings_back_the_emoji(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, player = await _creator_and_player(client, session)
    await _put(client, player.id, _png(), "image/png")

    response = await client.delete(f"/api/players/{player.id}/photo")
    fetched = await client.get(f"/api/players/{player.id}/photo")
    listed = (await client.get("/api/players", params={"limit": 200})).json()["items"]

    assert response.status_code == 204
    assert fetched.status_code == 404
    assert next(p for p in listed if p["id"] == str(player.id))["photoUrl"] is None


async def test_game_summaries_and_public_views_carry_photo_urls(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, pic = await _creator_and_player(client, session)
    others = [await make_player(session, name=f"O{k}") for k in range(2)]
    await _put(client, pic.id, _png(), "image/png")
    payload = make_payload(
        [str(pic.id), *(str(p.id) for p in others)], start_ms=to_ms(now_utc()) - 60 * MINUTE
    )
    await client.put(f"/api/games/{payload['syncId']}", json=payload)
    recap = (
        await client.post(f"/api/games/{payload['syncId']}/links", json={"kind": "recap"})
    ).json()["token"]

    summary = (await client.get(f"/api/games/{payload['syncId']}")).json()["summary"]
    public = (await client.get(f"/api/public/recap/{recap}")).json()["summary"]
    image = await client.get(f"/api/public/recap/{recap}/og.png")

    seats = {s["playerId"]: s["photoUrl"] for s in summary["players"]}
    assert seats[str(pic.id)] == f"/api/players/{pic.id}/photo?v=1"
    assert seats[str(others[0].id)] is None
    assert {s["playerId"]: s["photoUrl"] for s in public["players"]} == seats
    assert image.status_code == 200 and image.content[:4] == b"\x89PNG"


async def test_merge_moves_the_photo_when_the_target_has_none(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    _, dupe = await _creator_and_player(client, session)
    await _put(client, dupe.id, _png(), "image/png")
    keeper = await make_player(session, name="Keeper")
    admin = await make_user(session, email="a@example.com", role="admin", player_name="A")

    merged = await MergeService(session).merge(dupe.id, keeper.id, admin)

    assert merged.player.photo_url == f"/api/players/{keeper.id}/photo?v=1"
    assert (await client.get(f"/api/players/{keeper.id}/photo")).status_code == 200
