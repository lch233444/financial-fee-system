import asyncio
import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing
from threading import Event
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import config, database
from app.main import app
from app.models import Client, InvoiceMonthlySequence
from app.services import backup, datasets
from app.services.browser_sessions import BrowserSessions
from app.services.finance_meeting_contract import MEETING_REVISION
from app.services.entity_ids import allocate_entity_id
from test_deletion_guard_migration import _alembic_config


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    settings = config.Settings(data_root=tmp_path / "live", codex_home=tmp_path / "codex", testing=True)
    settings.ensure_directories()
    original_engine = database.engine
    monkeypatch.setattr(config, "_default_settings", lambda: settings)
    monkeypatch.setattr(database, "settings", settings)
    engine = datasets.Datasets.engine_for(settings)
    monkeypatch.setattr(database, "engine", engine)
    database.SessionLocal.configure(bind=engine)
    manager = datasets.Datasets(settings.data_root)
    monkeypatch.setattr(datasets, "_manager", manager)
    with TestClient(app) as client:
        yield client, manager, settings
    database.engine.dispose()
    engine.dispose()
    database.SessionLocal.configure(bind=original_engine)


def headers(manager):
    return {"X-Financial-System-Request": "1", "X-Financial-Dataset": manager.generation}


def create(client, manager, name="客户资料B"):
    response = client.post("/api/datasets", json={"name": name}, headers=headers(manager))
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_switch_isolates_same_ids_files_numbers_and_stale_requests(workspace):
    client, manager, settings = workspace
    with database.SessionLocal() as db:
        db.add(Client(id=allocate_entity_id(db, Client), name="A only"))
        db.add(InvoiceMonthlySequence(issue_month="202610", last_number=7))
        db.commit()
    original_root = settings.data_root
    (original_root / "attachments" / "synthetic.txt").write_text("A", encoding="utf-8")
    other = create(client, manager)
    old_headers = headers(manager)
    assert client.post("/api/datasets/switch", json={"id": other}, headers=old_headers).status_code == 200
    assert client.get("/api/clients", headers=headers(manager)).json() == []
    assert not (settings.data_root / "attachments" / "synthetic.txt").exists()
    with database.SessionLocal() as db:
        assert db.scalar(select(InvoiceMonthlySequence)) is None
        db.add(Client(id=allocate_entity_id(db, Client), name="B only"))
        db.add(InvoiceMonthlySequence(issue_month="202610", last_number=1))
        db.commit()
    (settings.data_root / "attachments" / "synthetic.txt").write_text("B", encoding="utf-8")
    assert client.get("/api/clients", headers=old_headers).status_code == 409
    assert client.post("/api/clients", json={"name": "wrong"}, headers=old_headers).status_code == 409
    assert client.get("/api/clients").status_code == 409
    assert client.get("/api/attachments/1/file", params={"dataset_token": old_headers["X-Financial-Dataset"]}).status_code == 409
    response = client.post("/api/datasets/switch", json={"id": "primary"}, headers=headers(manager))
    assert response.status_code == 200, response.text
    assert client.get("/api/clients", headers=headers(manager)).json()[0]["name"] == "A only"
    with database.SessionLocal() as db:
        assert db.get(InvoiceMonthlySequence, "202610").last_number == 7
    assert (original_root / "attachments" / "synthetic.txt").read_text() == "A"
    assert datasets.Datasets(original_root).active == "primary"


def test_switch_persistence_and_failed_catalog_write_leave_active_unchanged(workspace, monkeypatch):
    client, manager, settings = workspace
    other = create(client, manager)
    before = manager.snapshot()
    def fail(*args):
        raise OSError("synthetic disk full")
    with monkeypatch.context() as m:
        m.setattr(manager, "save", fail)
        response = client.post("/api/datasets/switch", json={"id": other}, headers=headers(manager))
        assert response.status_code == 409
    assert manager.snapshot() == before
    assert settings.data_root == manager.root
    assert client.post("/api/datasets/switch", json={"id": other}, headers=headers(manager)).status_code == 200
    restored = datasets.Datasets(manager.root)
    assert restored.active == other
    assert restored.path(other) == settings.data_root


def old_package(tmp_path, version):
    settings = config.Settings(data_root=tmp_path / "legacy", codex_home=tmp_path / "old-codex", testing=True)
    settings.ensure_directories()
    with config.using_settings(settings):
        command.upgrade(_alembic_config(settings), "d016f0ee1160" if version in {"1.1.6", "1.2.0"} else MEETING_REVISION)
    with closing(sqlite3.connect(settings.database_path)) as db:
        db.execute("INSERT INTO clients(id,name,status,created_at,updated_at) VALUES (1,'legacy customer','DRAFT','2026-10-23','2026-10-23')")
        db.execute("UPDATE app_settings SET value='1' WHERE key='id_high_water.clients'")
        db.commit()
    payload = settings.database_path.read_bytes()
    manifest = {"format": backup.BACKUP_FORMAT, "version": 1, "app_version": version,
                "snapshot_period": {"year": 2026, "quarter": 4}, "created_at": "2026-10-23T00:00:00",
                "files": [{"path": "database/financial_system.sqlite3", "sha256": hashlib.sha256(payload).hexdigest()}]}
    package = tmp_path / "old.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("database/financial_system.sqlite3", payload)
    return package


@pytest.mark.parametrize("version", ["1.1.4", "1.1.5", "1.1.6", "1.2.0"])
def test_old_package_migrates_independently_preserves_original(workspace, tmp_path, version):
    client, manager, settings = workspace
    package = old_package(tmp_path, version)
    original_bytes = package.read_bytes()
    with package.open("rb") as stream:
        response = client.post("/api/datasets/import", data={"name": "old"}, files={"file": ("old.zip", stream)}, headers=headers(manager))
    assert response.status_code == 200, response.text
    identity = response.json()["id"]
    root = manager.path(identity)
    assert package.read_bytes() == (root / "backups/import-original.zip").read_bytes() == original_bytes
    assert manager.active == "primary"
    assert client.get("/api/clients", headers=headers(manager)).json() == []
    assert client.post("/api/datasets/switch", json={"id": identity}, headers=headers(manager)).status_code == 200
    assert client.get("/api/clients", headers=headers(manager)).json()[0]["name"] == "legacy customer"
    with closing(sqlite3.connect(settings.database_path)) as db:
        backup.validate_database_structure(db)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == backup.CURRENT_DATABASE_REVISION


def test_bad_future_package_and_failed_migration_not_published(workspace, tmp_path, monkeypatch):
    client, manager, settings = workspace
    package = old_package(tmp_path, "9.9.9")
    before = manager.snapshot()
    with package.open("rb") as stream:
        response = client.post("/api/datasets/import", data={"name": "future"}, files={"file": ("old.zip", stream)}, headers=headers(manager))
    assert response.status_code == 400
    assert manager.snapshot() == before
    def fail(**kwargs):
        raise RuntimeError("synthetic migration failure")
    monkeypatch.setattr(database, "init_db", fail)
    response = client.post("/api/datasets", json={"name": "failed"}, headers=headers(manager))
    assert response.status_code == 400
    assert manager.snapshot() == before
    assert settings.data_root == manager.root


def test_stream_and_background_work_block_switch_until_response_finishes(workspace):
    client, manager, settings = workspace
    async def run():
        streaming, finish_stream, background, finish_background = [asyncio.Event() for _ in range(4)]
        async def endpoint(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            if scope["path"] == "/api/clients":
                streaming.set()
                await finish_stream.wait()
                await send({"type": "http.response.body", "body": b"[]"})
                background.set()
                await finish_background.wait()
        gate = datasets.DatasetGate(endpoint)
        def scope(path, method="POST"):
            return {"type": "http", "path": path, "method": method, "headers": [], "query_string": b""}
        messages = []
        async def send(message): messages.append(message)
        async def receive(): return {"type": "http.request", "body": b""}
        task = asyncio.create_task(gate(scope("/api/clients", "GET"), receive, send))
        await streaming.wait()
        await gate(scope("/api/datasets/switch"), receive, send)
        assert messages[-2]["status"] == 409
        finish_stream.set(); await background.wait()
        await gate(scope("/api/datasets"), receive, send)
        assert messages[-2]["status"] == 409
        finish_background.set(); await task
        await gate(scope("/api/datasets/switch"), receive, send)
        assert messages[-1]["status"] == 200
    asyncio.run(run())


def test_browser_switch_requires_every_page_to_be_clean():
    async def run():
        sessions = BrowserSessions(None)
        class Socket:
            def __init__(self, page, ready): self.page, self.ready = page, ready
            async def send_json(self, message):
                if message["type"] == "dataset-prepare":
                    sessions.receive(self.page, json.dumps({"type": "dataset-ready", "nonce": message["nonce"], "ready": self.ready}))
        a, b = object(), object()
        for page, ready in [(a, True), (b, False)]:
            sessions.connect(page); sessions.attach_socket(page, Socket(page, ready))
        with pytest.raises(ValueError, match="未保存"):
            await sessions.prepare_switch()
        await sessions.finish_switch("same")
        sessions._sockets[b].ready = True
        await sessions.prepare_switch()
        assert not sessions.connect(object())
        await sessions.finish_switch("next")
        assert sessions.connect(object())
        await sessions.close()
    asyncio.run(run())


def test_missing_registered_primary_database_stops_without_creating_empty_replacement(tmp_path):
    root = tmp_path / "missing"
    root.mkdir()
    manager = datasets.Datasets(root)
    manager.save(manager.entries, "primary")
    restored = datasets.Datasets(root)
    with pytest.raises(RuntimeError, match="数据库缺失"):
        restored.startup()
    assert not (root / "database/financial_system.sqlite3").exists()


def test_restore_only_replaces_selected_dataset(workspace):
    client, manager, settings = workspace
    with database.SessionLocal() as db:
        db.add(Client(id=allocate_entity_id(db, Client), name="Primary retained")); db.commit()
    other = create(client, manager)
    assert client.post("/api/datasets/switch", json={"id": other}, headers=headers(manager)).status_code == 200
    package = backup.create_backup()
    with database.SessionLocal() as db:
        db.add(Client(id=allocate_entity_id(db, Client), name="Will be restored")); db.commit()
    database.engine.dispose()
    backup.stage_restore(package)
    assert backup.apply_pending_restore()
    assert client.get("/api/clients", headers=headers(manager)).json() == []
    assert client.post("/api/datasets/switch", json={"id": "primary"}, headers=headers(manager)).status_code == 200
    assert client.get("/api/clients", headers=headers(manager)).json()[0]["name"] == "Primary retained"
