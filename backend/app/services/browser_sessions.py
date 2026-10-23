from __future__ import annotations

import asyncio
import json
from uuid import uuid4
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
        self._sockets = {}
        self._switch_waiters = {}
        self._switch_nonce = None

    def connect(self, page: object) -> bool:
        if self._closing or self._switch_nonce:
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
        self._sockets.pop(page, None)
        waiter = self._switch_waiters.get(page)
        if waiter is not None and not waiter.done():
            waiter.set_result(False)
        if not self._pages and not self._closing:
            self._pending = asyncio.create_task(self._exit_after_last_page())

    def attach_socket(self, page, socket) -> None:
        self._sockets[page] = socket

    def receive(self, page, text: str) -> None:
        try:
            message = json.loads(text)
        except (ValueError, TypeError):
            return
        waiter = self._switch_waiters.get(page)
        if (isinstance(message, dict) and message.get("type") == "dataset-ready"
                and message.get("nonce") == self._switch_nonce and waiter is not None and not waiter.done()):
            waiter.set_result(message.get("ready") is True)

    async def prepare_switch(self) -> None:
        self._switch_nonce = uuid4().hex
        loop = asyncio.get_running_loop()
        self._switch_waiters = {page: loop.create_future() for page in self._pages}
        try:
            for page in list(self._pages):
                socket = self._sockets.get(page)
                if socket is None:
                    raise ValueError("有页面尚未支持数据包切换，请关闭旧页面后重试")
                await socket.send_json({"type": "dataset-prepare", "nonce": self._switch_nonce})
            results = await asyncio.wait_for(asyncio.gather(*self._switch_waiters.values()), timeout=3)
            if not all(results):
                raise ValueError("有页面存在未保存资料或尚未完成的操作，请先保存或放弃修改")
        except asyncio.TimeoutError as exc:
            raise ValueError("有页面未响应，请关闭无响应的系统页面后重试") from exc

    async def finish_switch(self, token: str) -> None:
        for socket in list(self._sockets.values()):
            try:
                await socket.send_json({"type": "dataset-finish", "token": token})
            except (OSError, RuntimeError):
                pass
        self._switch_waiters.clear()
        self._switch_nonce = None

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
