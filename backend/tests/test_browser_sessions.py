from __future__ import annotations

import asyncio
from threading import Event

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.main as application
from app.services.browser_sessions import BrowserSessions


class FakeShutdown:
    def __init__(self, *, accepted: bool = True):
        self.accepted = accepted
        self.requests = 0
        self.executions = 0
        self.stopped = Event()

    def request(self):
        self.requests += 1
        return self.accepted

    def execute(self):
        self.executions += 1
        self.stopped.set()


def test_pages_close_refresh_and_reopen_without_premature_shutdown():
    async def run():
        shutdown = FakeShutdown()
        sessions = BrowserSessions(shutdown, close_delay=0.02)
        await asyncio.sleep(0.03)  # Startup without a connected browser is allowed.
        assert shutdown.requests == 0
        first, second, refreshed = object(), object(), object()
        assert sessions.connect(first) and sessions.connect(second)
        sessions.disconnect(first)
        await asyncio.sleep(0.03)
        assert shutdown.requests == 0  # Another tab remains open.
        sessions.disconnect(second)
        assert sessions.connect(refreshed)  # Refresh cancels the pending exit.
        await asyncio.sleep(0.03)
        assert shutdown.requests == 0
        sessions.disconnect(refreshed)
        sessions.disconnect(refreshed)  # A repeated close must not reset the grace period.
        assert await asyncio.to_thread(shutdown.stopped.wait, 1)
        assert shutdown.requests == shutdown.executions == 1
        assert not sessions.connect(object())
        await sessions.close()
    asyncio.run(run())


@pytest.mark.parametrize("already_stopping", [False, True])
def test_app_teardown_and_existing_shutdown_do_not_double_execute(already_stopping):
    async def run():
        shutdown = FakeShutdown(accepted=not already_stopping)
        sessions = BrowserSessions(shutdown, close_delay=0.01)
        page = object()
        sessions.connect(page)
        sessions.disconnect(page)
        if already_stopping:
            await asyncio.sleep(0.03)
            assert shutdown.requests == 1
        await sessions.close()
        await asyncio.sleep(0.02)
        assert shutdown.executions == 0
    asyncio.run(run())


def test_actual_websocket_disconnect_waits_for_last_page(monkeypatch):
    shutdown = FakeShutdown()
    monkeypatch.setattr(application, "BrowserSessions", lambda _: BrowserSessions(shutdown, close_delay=0.02))
    with TestClient(application.app, client=("127.0.0.1", 50000)) as client:
        with client.websocket_connect("ws://127.0.0.1:8000/api/browser-session", headers={"origin": "http://127.0.0.1:8000"}):
            with client.websocket_connect("ws://127.0.0.1:8000/api/browser-session", headers={"origin": "http://127.0.0.1:8000"}):
                assert not shutdown.stopped.is_set()
            assert not shutdown.stopped.wait(0.05)
        assert shutdown.stopped.wait(1)
    assert shutdown.executions == 1


@pytest.mark.parametrize("origin, client_host, fetch_site", [
    (None, "127.0.0.1", "same-origin"),
    ("http://attacker.example", "127.0.0.1", "same-origin"),
    ("http://127.0.0.1:8001", "127.0.0.1", "same-origin"),
    ("http://127.0.0.1:8000", "203.0.113.1", "same-origin"),
    ("http://127.0.0.1:8000", "127.0.0.1", "cross-site"),
    ("http://user@127.0.0.1:8000", "127.0.0.1", "same-origin"),
])
def test_external_page_cannot_arm_or_close_application(monkeypatch, origin, client_host, fetch_site):
    shutdown = FakeShutdown()
    monkeypatch.setattr(application, "BrowserSessions", lambda _: BrowserSessions(shutdown, close_delay=0.01))
    headers = {"sec-fetch-site": fetch_site}
    if origin is not None:
        headers["origin"] = origin
    with TestClient(application.app, client=(client_host, 50000)) as client:
        with pytest.raises(WebSocketDisconnect) as rejected:
            with client.websocket_connect("ws://127.0.0.1:8000/api/browser-session", headers=headers):
                pytest.fail("Untrusted page connected")
        assert rejected.value.code == 1008
        assert not shutdown.stopped.wait(0.03)
    assert shutdown.requests == 0
