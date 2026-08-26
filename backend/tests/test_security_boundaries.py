from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from app.config import Settings
from app.main import app
from app.routes import statements


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _blank_parse_result() -> SimpleNamespace:
    return SimpleNamespace(
        document_type="unknown",
        account_number=None,
        as_of_date=None,
        total_balance=None,
        raw_text="",
        confidence={},
        warnings=[],
        extracted_dict=lambda: {"document_type": "unknown", "document_details": {}, "holdings": []},
    )


def test_statement_upload_rejects_html_disguised_as_jpeg() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        response = client.post(
            "/api/statement-imports",
            files={"file": ("statement.jpg", b"<html><script>alert(1)</script>", "image/jpeg")},
        )

    assert response.status_code == 415
    assert "伪装" in response.json()["detail"]


def test_statement_mime_and_storage_suffix_come_from_magic_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_bytes = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image_bytes, format="PNG")
    monkeypatch.setattr(statements, "parse_empf_statement", lambda _path: _blank_parse_result())

    with TestClient(app, headers=WRITE_HEADERS) as client:
        uploaded = client.post(
            "/api/statement-imports",
            files={
                "file": (
                    "misleading.jpg",
                    image_bytes.getvalue(),
                    "text/html",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.json()["mime_type"] == "image/png"

        preview = client.get(f"/api/statement-imports/{uploaded.json()['id']}/file")

    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert preview.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_settings_reject_non_loopback_host(host: str, tmp_path) -> None:
    with pytest.raises(ValidationError):
        Settings(host=host, data_root=tmp_path / "data")


def test_settings_rejects_desktop_codex_home(tmp_path) -> None:
    with pytest.raises(ValidationError):
        Settings(data_root=tmp_path / "data", codex_home=Path.home() / ".codex")


def test_trusted_host_middleware_rejects_external_host_header() -> None:
    with TestClient(app) as client:
        rejected = client.get("/api/health", headers={"Host": "attacker.example"})
        accepted = client.get("/api/health", headers={"Host": "127.0.0.1"})

    assert rejected.status_code == 400
    assert accepted.status_code == 200
    assert accepted.json()["local_only"] is True
