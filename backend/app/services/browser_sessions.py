from __future__ import annotations

import asyncio
from contextlib import suppress

from starlette.concurrency import run_in_threadpool

from .shutdown import ShutdownCoordinator


class BrowserSessions:
    """Keep the local app alive while at least one browser page is connected."""

    def __init__(self, shutdown: ShutdownCoordinator, *, close_delay: float = 3.0) -> None:
        self._shutdown = shutdown
        self._close_delay = close_delay
        self._pages: set[object] = set()
        self._pending: asyncio.Task[None] | None = None
        self._closing = False

    def connect(self, page: object) -> bool:
        if self._closing:
            return False
        self._pages.add(page)
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        return True

    def disconnect(self, page: object) -> None:
        if page not in self._pages:
            return
        self._pages.remove(page)
        if not self._pages and not self._closing:
            self._pending = asyncio.create_task(self._exit_after_last_page())

    async def _exit_after_last_page(self) -> None:
        await asyncio.sleep(self._close_delay)
        if self._pages or self._closing:
            return
        # No await between the last-page check and shutdown claim: a new
        # page either cancels the grace period or observes shutdown started.
        self._closing = True
        if self._shutdown.request():
            await run_in_threadpool(self._shutdown.execute)

    async def close(self) -> None:
        self._closing = True
        self._pages.clear()
        if self._pending is not None:
            self._pending.cancel()
            with suppress(asyncio.CancelledError):
                await self._pending
            self._pending = None
