import argparse
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import AppState, Game, RateLimit, Session, User
from app.domain.time import now_utc
from app.jobs import backup, prune, seed_demo, seed_load
from app.jobs.backup import to_delete
from app.security.tokens import hash_token, new_token
from app.settings import settings
from tests.factories import make_user


async def test_prune_removes_expired_sessions_and_old_windows_only(
    session: AsyncSession, rate_limit_factory: async_sessionmaker[AsyncSession]
) -> None:
    user = await make_user(session)
    now = now_utc()
    for hours in (-1, 24):
        session.add(
            Session(
                user_id=user.id,
                token_hash=hash_token(new_token()),
                expires_at=now + timedelta(hours=hours),
            )
        )
    await session.flush()
    async with rate_limit_factory() as other:
        other.add(RateLimit(key="login:old", window_start=now - timedelta(days=2), count=3))
        other.add(RateLimit(key="login:new", window_start=now, count=1))
        await other.commit()
    Path(settings.OG_CACHE_DIR, "gone-token-3.png").write_bytes(b"x")

    assert await prune.run(argparse.Namespace()) == 0

    left = (await session.execute(select(func.count()).select_from(Session))).scalar_one()
    assert left == 1
    # Prune ran inside the test transaction, so its deletes are visible only from there.
    keys = (await session.execute(select(RateLimit.key))).scalars().all()
    assert keys == ["login:new"]


def test_prune_deletes_cached_images_of_revoked_links_only(tmp_path: Path) -> None:
    (tmp_path / "revoked0000000000000000-2.png").write_bytes(b"x")
    (tmp_path / "keepme0000000000000000-1.png").write_bytes(b"x")

    removed = prune.remove_images(tmp_path, {"revoked0000000000000000"})

    assert removed == 1
    assert [p.name for p in tmp_path.iterdir()] == ["keepme0000000000000000-1.png"]


def test_backup_rotation_keeps_14_days_and_8_sundays() -> None:
    today = date(2026, 10, 31)
    names = [backup.backup_name(today - timedelta(days=d)) for d in range(80)]

    doomed = set(to_delete(names, today))
    kept = [n for n in names if n not in doomed]

    assert len([n for n in kept if (today - date.fromisoformat(n[9:19])).days < 14]) == 14
    old_kept = [
        date.fromisoformat(n[9:19])
        for n in kept
        if (today - date.fromisoformat(n[9:19])).days >= 14
    ]
    assert all(d.weekday() == 6 for d in old_kept)
    assert all((today - d).days < 56 for d in old_kept)


@pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump is not installed")
async def test_backup_writes_a_restorable_dump_and_records_the_time(session: AsyncSession) -> None:
    assert await backup.run(argparse.Namespace()) == 0

    dumps = list(Path(settings.BACKUP_DIR).glob("kachuful-*.dump"))
    assert dumps and dumps[0].read_bytes()[:5] == b"PGDMP"
    recorded = (await session.execute(select(AppState.last_backup_at))).scalar_one()
    assert recorded is not None


def test_backup_reads_credentials_from_the_database_url() -> None:
    env = backup.libpq_env()

    assert env["PGDATABASE"] == "kachuful_test"
    assert env["PGUSER"] == "kachuful"
    assert "PGPASSWORD" in env


async def test_seed_demo_creates_accounts_games_and_links(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await seed_demo.run(argparse.Namespace(reset=False)) == 0

    out = capsys.readouterr().out
    users = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    games = (await session.execute(select(func.count()).select_from(Game))).scalar_one()
    assert users == 3
    assert games == seed_demo.GAMES
    assert "?invite=" in out and "?reset=" in out and "riya-demo" in out
    assert await seed_demo.run(argparse.Namespace(reset=False)) == 2


async def test_seed_load_builds_the_dataset_through_uploads(session: AsyncSession) -> None:
    code = await seed_load.run(argparse.Namespace(players=8, games=12, days=10, reset=False))

    games = (await session.execute(select(func.count()).select_from(Game))).scalar_one()
    assert code == 0
    assert games == 12
