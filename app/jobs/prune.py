"""`prune` (nightly): delete expired sessions, dead invites, resets and rate-limit windows,
and the cached preview images of revoked links."""

import argparse
import sys
from pathlib import Path

from app.db.session import get_session_factory
from app.domain.time import now_utc
from app.logger import get_logger
from app.repositories.maintenance import maintenance_repository
from app.repositories.share_links import share_links_repository
from app.settings import settings

logger = get_logger(__name__)

HELP = "Delete expired sessions, invites, resets, rate-limit windows and revoked OG images"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """No options."""


def remove_images(cache_dir: Path, tokens: set[str]) -> int:
    """Delete cached recap images whose token is revoked; returns how many."""
    if not cache_dir.is_dir():
        return 0
    removed = 0
    for image in cache_dir.glob("*.png"):
        if image.stem.rsplit("-", 1)[0] in tokens:
            image.unlink(missing_ok=True)
            removed += 1
    return removed


async def run(_: argparse.Namespace) -> int:
    """Safe to run at any time."""
    async with get_session_factory()() as session:
        pruned = await maintenance_repository.prune(session, now_utc())
        revoked = set(await share_links_repository.revoked_tokens(session))
        await session.commit()
    images = remove_images(Path(settings.OG_CACHE_DIR), revoked)
    logger.info("prune finished", extra={**pruned.__dict__, "images": images})
    sys.stdout.write(
        f"prune: {pruned.sessions} sessions, {pruned.invites} invites, {pruned.resets} resets, "
        f"{pruned.rate_limits} rate-limit windows, {images} images\n"
    )
    return 0
