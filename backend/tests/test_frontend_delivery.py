from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_exposes_running_application_version() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["version"] == app.version


def test_spa_shell_is_never_reused_across_local_release_updates() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
