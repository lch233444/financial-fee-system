from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.responses import FileResponse
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import Attachment, StatementImport
from app.routes.attachments import download_attachment
from app.routes.statements import get_statement_file
from app.services.entity_ids import allocate_entity_id


@pytest.fixture
def client():
    with TestClient(app) as http:
        yield http


def _evidence(kind: str, *, superseded: bool = False, content: bytes | None = None):
    content = content or b"%PDF-1.7\nsynthetic-evidence-" + uuid4().hex.encode() + b"\n%%EOF"
    root = get_settings().data_root / ("attachments" if kind == "attachment" else "statement_imports")
    path = root / f"{uuid4().hex}.pdf"
    path.write_bytes(content)
    values = dict(original_name="原始凭证.pdf", stored_path=str(path),
                  sha256=hashlib.sha256(content).hexdigest(), mime_type="application/pdf")
    if kind == "attachment":
        row = Attachment(**values, entity_type="TRANSACTION", entity_id=None,
                         size_bytes=len(content), superseded=superseded)
    else:
        # StatementImport deliberately has no recorded size; SHA-256 must suffice.
        assert "size_bytes" not in StatementImport.__table__.columns
        row = StatementImport(**values)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        if kind == "statement":
            row.id = allocate_entity_id(db, StatementImport)
        db.add(row)
        db.commit()
        row_id = row.id
    endpoint = "attachments" if kind == "attachment" else "statement-imports"
    return f"/api/{endpoint}/{row_id}/file", path, content, row_id


@pytest.fixture
def evidence(client):
    sources = []

    def create(kind, **kwargs):
        result = _evidence(kind, **kwargs)
        sources.append((result[1], result[2]))
        return result

    yield create
    # Later backup tests share this synthetic database. Restore test mutations
    # only after asserting that the GET itself preserved the damaged scene.
    for path, content in sources:
        path.write_bytes(content)


@pytest.mark.parametrize("kind", ["attachment", "statement"])
def test_verified_file_preserves_content_headers_and_ranges(client, evidence, kind):
    url, _path, content, _row_id = evidence(kind)
    temporary_root = get_settings().data_root / "tmp"
    before = set(temporary_root.iterdir())
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-length"] == str(len(content))
    disposition = "attachment" if kind == "attachment" else "inline"
    assert response.headers["content-disposition"].startswith(disposition + "; filename*=utf-8''")
    if kind == "statement":
        assert response.headers["x-content-type-options"] == "nosniff"
    partial = client.get(url, headers={"Range": "bytes=0-7"})
    assert partial.status_code == 206
    assert partial.content == content[:8]
    assert partial.headers["content-range"] == f"bytes 0-7/{len(content)}"
    assert client.get(url, headers={"Range": "bytes=999999-"}).status_code == 416
    assert set(temporary_root.iterdir()) == before


@pytest.mark.parametrize("kind", ["attachment", "statement"])
@pytest.mark.parametrize("damage", ["same_size", "truncated", "empty", "missing"])
def test_file_access_rejects_damaged_evidence_and_preserves_scene(client, evidence, kind, damage):
    url, path, content, row_id = evidence(kind)
    before = set((get_settings().data_root / "tmp").iterdir())
    damaged = {"same_size": content[:-1] + b"X", "truncated": content[:-5], "empty": b""}.get(damage)
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(damaged)
    response = client.get(url)
    assert response.status_code == (404 if damage == "missing" else 409), response.text
    assert set((get_settings().data_root / "tmp").iterdir()) == before
    if damage != "missing":
        assert path.read_bytes() == damaged
    else:
        assert not path.exists()
    with SessionLocal() as db:
        row = db.get(Attachment if kind == "attachment" else StatementImport, row_id)
        assert row.sha256 == hashlib.sha256(content).hexdigest()


def test_superseded_attachment_remains_viewable_but_must_be_intact(client, evidence):
    url, path, content, _row_id = evidence("attachment", superseded=True)
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == content
    path.write_bytes(content[:-1] + b"X")
    assert client.get(url).status_code == 409


@pytest.mark.parametrize("kind", ["attachment", "statement"])
def test_file_response_uses_verified_bytes_after_source_changes(client, evidence, monkeypatch, kind):
    url, path, content, _row_id = evidence(kind)
    original_call = FileResponse.__call__

    async def change_source_before_sending(response, scope, receive, send):
        path.write_bytes(content[:-1] + b"X")
        return await original_call(response, scope, receive, send)

    monkeypatch.setattr(FileResponse, "__call__", change_source_before_sending)
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == content
    assert path.read_bytes() == content[:-1] + b"X"


@pytest.mark.parametrize("kind", ["attachment", "statement"])
def test_large_file_access_never_reads_entire_source_into_memory(client, evidence, monkeypatch, kind):
    content = b"%PDF-1.7\n" + b"synthetic-payload\n" * 100_000
    url, path, _content, _row_id = evidence(kind, content=content)
    original_read_bytes = Path.read_bytes
    original_open = Path.open
    read_sizes = []

    class BoundedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            assert 0 < size <= 64 * 1024, "Source reads must be bounded"
            read_sizes.append(size)
            return self.stream.read(size)

    def bounded_open(self, mode="r", *args, **kwargs):
        stream = original_open(self, mode, *args, **kwargs)
        return BoundedReader(stream) if self == path and mode == "rb" else stream

    def forbid_source_read_bytes(self):
        assert self != path, "Source must be read in bounded chunks"
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", forbid_source_read_bytes)
    monkeypatch.setattr(Path, "open", bounded_open)
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == content
    assert len(read_sizes) > 1


@pytest.mark.parametrize("kind", ["attachment", "statement"])
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
def test_verified_file_temporary_copy_is_cleaned_after_send_failure(client, evidence, kind, failure):
    _url, path, content, row_id = evidence(kind)
    temporary_root = get_settings().data_root / "tmp"
    before = set(temporary_root.iterdir())
    with SessionLocal() as db:
        response = (download_attachment if kind == "attachment" else get_statement_file)(row_id, db)
    # The response must own a verified snapshot, not reopen the source path.
    assert Path(response.path) != path
    assert Path(response.path).is_file()

    async def fail_send(message):
        assert message["type"] != "http.response.pathsend"
        if message["type"] == "http.response.body":
            raise failure("synthetic client disconnect")

    async def receive():
        return {"type": "http.disconnect"}

    with pytest.raises(failure, match="synthetic client disconnect"):
        asyncio.run(response({"type": "http", "method": "GET", "headers": [],
                              "extensions": {"http.response.pathsend": {}}}, receive, fail_send))
    assert set(temporary_root.iterdir()) == before
    assert path.read_bytes() == content


def test_statement_format_detection_still_uses_verified_content(client, evidence):
    url, path, _content, row_id = evidence("statement", content=b"<html>synthetic-not-a-statement</html>")
    before = set((get_settings().data_root / "tmp").iterdir())
    response = client.get(url)
    assert response.status_code == 415
    assert path.is_file()
    assert set((get_settings().data_root / "tmp").iterdir()) == before
    with SessionLocal() as db:
        assert db.get(StatementImport, row_id) is not None
