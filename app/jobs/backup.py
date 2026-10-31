"""`backup` (nightly 03:00): pg_dump -Fc, keep 14 daily and 8 weekly, copy off the VPS with rclone."""

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlsplit

from app.db.session import get_session_factory
from app.domain.time import now_utc
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.settings import settings

logger = get_logger(__name__)

HELP = "Dump the database, rotate local copies and copy off the VPS (BACKUP_REMOTE)"
KEEP_DAILY = 14
KEEP_WEEKLY = 8
PREFIX = "kachuful-"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """No options."""


def libpq_env() -> dict[str, str]:
    """PG* variables for pg_dump from DATABASE_URL (the password never goes on a command line)."""
    url = urlsplit(settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1))
    env = {
        "PGHOST": url.hostname or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": unquote(url.username or ""),
        "PGDATABASE": url.path.lstrip("/"),
    }
    if url.password:
        env["PGPASSWORD"] = unquote(url.password)
    return env


def backup_name(day: date) -> str:
    """Daily files are kachuful-YYYY-MM-DD.dump; Sundays are also the weekly copies."""
    return f"{PREFIX}{day.isoformat()}.dump"


def to_delete(names: list[str], today: date) -> list[str]:
    """Files outside the last 14 days, except Sundays within the last 8 weeks."""
    doomed = []
    for name in names:
        try:
            day = date.fromisoformat(name.removeprefix(PREFIX).removesuffix(".dump"))
        except ValueError:
            continue
        age = (today - day).days
        weekly = day.weekday() == 6 and age < KEEP_WEEKLY * 7
        if age >= KEEP_DAILY and not weekly:
            doomed.append(name)
    return sorted(doomed)


async def _run(*command: str, env: dict[str, str] | None = None) -> None:
    process = await asyncio.create_subprocess_exec(
        *command,
        env={**os.environ, **(env or {})},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"{command[0]} failed: {stderr.decode().strip()[:500]}")


def _finish(folder: Path, partial: Path, target: Path, today: date) -> float:
    """Move the finished dump into place, drop old ones; returns its size in MB."""
    partial.replace(target)
    for name in to_delete([p.name for p in folder.glob(f"{PREFIX}*.dump")], today):
        (folder / name).unlink(missing_ok=True)
    return target.stat().st_size / 1_000_000


async def run(_: argparse.Namespace) -> int:
    """Dump, rotate, copy; records lastBackupAt only after the dump succeeds."""
    folder = Path(settings.BACKUP_DIR)
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    today = now_utc().astimezone(settings.tz).date()
    target = folder / backup_name(today)
    partial = target.with_suffix(".partial")
    await _run("pg_dump", "-Fc", "--no-owner", "-f", str(partial), env=libpq_env())
    size_mb = await asyncio.to_thread(_finish, folder, partial, target, today)
    if settings.BACKUP_REMOTE:
        await _run(
            "rclone", "sync", str(folder), settings.BACKUP_REMOTE, "--include", f"{PREFIX}*.dump"
        )
    async with get_session_factory()() as session:
        await app_state_repository.set_last_backup_at(session, now_utc())
        await session.commit()
    logger.info("backup finished", extra={"file": target.name, "mb": round(size_mb, 2)})
    sys.stdout.write(f"backup: {target} ({size_mb:.1f} MB)\n")
    return 0
