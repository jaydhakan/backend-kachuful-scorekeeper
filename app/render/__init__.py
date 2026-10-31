"""Images: OG recap cards and QR codes. Pillow work never runs on the event loop."""

import asyncio
from collections.abc import Callable

# At most two renders at once per worker, so a burst of previews can't starve requests.
_RENDER_SLOTS = asyncio.Semaphore(2)


async def run_in_thread[T](fn: Callable[..., T], *args: object) -> T:
    """Run blocking, CPU-heavy work in the thread pool."""
    async with _RENDER_SLOTS:
        return await asyncio.to_thread(fn, *args)
