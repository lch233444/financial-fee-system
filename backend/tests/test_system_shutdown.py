from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import shutdown as shutdown_service
from app.services.shutdown import ShutdownCoordinator, get_shutdown_coordinator


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


class FakeShutdownCoordinator:
    def __init__(self, *, first_request: bool = True) -> None:
        self.first_request = first_request
        self.events: list[str] = []

    def request(self) -> bool:
        self.events.append("request")
        return self.first_request

    def execute(self) -> None:
        self.events.append("execute")


def _with_fake_coordinator(fake: FakeShutdownCoordinator, test: Callable[[], None]) -> None:
    app.dependency_overrides[get_shutdown_coordinator] = lambda: fake
    try:
        test()
    finally:
        app.dependency_overrides.pop(get_shutdown_coordinator, None)


def test_shutdown_returns_success_and_runs_teardown_as_response_background_task() -> None:
    fake = FakeShutdownCoordinator()

    def run() -> None:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.post(
                "/api/shutdown",
                headers={
                    **WRITE_HEADERS,
                    "Origin": "http://127.0.0.1:8000",
                    "Sec-Fetch-Site": "same-origin",
                },
            )
        assert response.status_code == 200
        assert response.json() == {
            "status": "shutting_down",
            "accepted": True,
            "already_requested": False,
            "message": "系统正在安全退出，本地服务与Sol识别进程将停止。",
        }
        # TestClient waits for Starlette response background tasks.  Seeing
        # execute here verifies the endpoint registered (rather than called)
        # the teardown and that it completed after the response body stage.
        assert fake.events == ["request", "execute"]

    _with_fake_coordinator(fake, run)


def test_duplicate_shutdown_request_is_idempotent() -> None:
    fake = FakeShutdownCoordinator(first_request=False)

    def run() -> None:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.post("/api/shutdown", headers=WRITE_HEADERS)
        assert response.status_code == 200
        assert response.json()["already_requested"] is True
        assert fake.events == ["request"]

    _with_fake_coordinator(fake, run)


def test_shutdown_requires_financial_write_marker() -> None:
    fake = FakeShutdownCoordinator()

    def run() -> None:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.post("/api/shutdown")
        assert response.status_code == 403
        assert fake.events == []

    _with_fake_coordinator(fake, run)


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://attacker.example"},
        {
            "Origin": "http://127.0.0.1:8000",
            "Sec-Fetch-Site": "cross-site",
        },
    ],
)
def test_shutdown_rejects_cross_site_browser_requests(headers: dict[str, str]) -> None:
    fake = FakeShutdownCoordinator()

    def run() -> None:
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            response = client.post("/api/shutdown", headers={**WRITE_HEADERS, **headers})
        assert response.status_code == 403
        assert fake.events == []

    _with_fake_coordinator(fake, run)


def test_shutdown_rejects_non_loopback_client() -> None:
    fake = FakeShutdownCoordinator()

    def run() -> None:
        with TestClient(app, client=("203.0.113.20", 50000)) as client:
            response = client.post("/api/shutdown", headers=WRITE_HEADERS)
        assert response.status_code == 403
        assert fake.events == []

    _with_fake_coordinator(fake, run)


def test_shutdown_coordinator_closes_owned_codex_before_stopping_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeCodex:
        def close(self) -> None:
            events.append("codex-closed")

    monkeypatch.setattr(shutdown_service, "get_codex_app_server", lambda: FakeCodex())
    coordinator = ShutdownCoordinator()
    coordinator.register_server_stop_hook(lambda: events.append("server-stopped"))

    assert coordinator.request() is True
    assert coordinator.request() is False
    coordinator.execute()
    coordinator.execute()

    assert events == ["codex-closed", "server-stopped"]


def test_shutdown_coordinator_signals_cli_server_when_no_direct_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeCodex:
        def close(self) -> None:
            events.append("codex-closed")

    monkeypatch.setattr(shutdown_service, "get_codex_app_server", lambda: FakeCodex())
    monkeypatch.setattr(shutdown_service, "_signal_running_server", lambda: events.append("server-signalled"))
    coordinator = ShutdownCoordinator()

    assert coordinator.request() is True
    coordinator.execute()

    assert events == ["codex-closed", "server-signalled"]
