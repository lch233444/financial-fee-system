from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

from app import main as main_module
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.models import AuditEvent, StatementImport
from app.routes.statements import _create_upload_pending_marker
from app.services.entity_ids import allocate_entity_id
from app.services.statement_delete_recovery import (
    _pending_candidates,
    reconcile_statement_delete_pending_files,
)


_CLEANUP_METHOD = "ATOMIC_SAME_DIRECTORY_STAGE_THEN_UNLINK_AFTER_COMMIT"


def _paths(label: str) -> tuple[Path, Path]:
    statement_root = get_settings().data_root / "statement_imports"
    original = statement_root / f"recovery-{label}-{uuid4().hex}.jpg"
    pending = original.with_name(
        f".{original.name}.{uuid4().hex}.delete-pending"
    )
    return original, pending


def _statement(original: Path, digest: str) -> int:
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        item = StatementImport(
            id=allocate_entity_id(db, StatementImport),
            original_name=original.name,
            stored_path=str(original),
            sha256=digest,
            mime_type="image/jpeg",
            parser_name="TEST",
            parser_version="1.1",
            status="NEEDS_REVIEW",
        )
        db.add(item)
        db.commit()
        return item.id


def _delete_audit(pending: Path, digest: str) -> int:
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        deleted_import_id = allocate_entity_id(db, StatementImport)
        event = AuditEvent(
            action="STATEMENT_IMPORT_DELETED",
            entity_type="STATEMENT_IMPORT",
            entity_id=deleted_import_id,
            details_json={
                "sha256": digest,
                "source_cleanup": {
                    "method": _CLEANUP_METHOD,
                    "staged_path": str(pending),
                    "sha256": digest,
                },
            },
        )
        db.add(event)
        db.commit()
        return event.id


def _cleanup(*, original: Path, pending: Path, statement_id: int | None, audit_id: int | None) -> None:
    for path in (pending, original):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    with SessionLocal() as db:
        if statement_id is not None:
            db.execute(delete(StatementImport).where(StatementImport.id == statement_id))
        if audit_id is not None:
            db.execute(delete(AuditEvent).where(AuditEvent.id == audit_id))
        db.commit()


def test_startup_recovery_restores_source_after_precommit_crash() -> None:
    init_db()
    original, pending = _paths("precommit")
    content = b"\xff\xd8\xffsynthetic precommit recovery"
    digest = hashlib.sha256(content).hexdigest()
    original.write_bytes(content)
    statement_id = _statement(original, digest)
    original.replace(pending)
    try:
        assert reconcile_statement_delete_pending_files() == {
            "restored": 1,
            "cleaned": 0,
        }
        assert original.read_bytes() == content
        assert not pending.exists()
        with SessionLocal() as db:
            assert db.get(StatementImport, statement_id) is not None
    finally:
        _cleanup(
            original=original,
            pending=pending,
            statement_id=statement_id,
            audit_id=None,
        )


def test_startup_recovery_cleans_source_after_postcommit_crash() -> None:
    init_db()
    original, pending = _paths("postcommit")
    content = b"\xff\xd8\xffsynthetic postcommit recovery"
    digest = hashlib.sha256(content).hexdigest()
    pending.write_bytes(content)
    audit_id = _delete_audit(pending, digest)
    try:
        assert reconcile_statement_delete_pending_files() == {
            "restored": 0,
            "cleaned": 1,
        }
        assert not original.exists()
        assert not pending.exists()
    finally:
        _cleanup(
            original=original,
            pending=pending,
            statement_id=None,
            audit_id=audit_id,
        )


def test_startup_recovery_rejects_database_reference_to_pending_path() -> None:
    init_db()
    original, pending = _paths("pending-reference")
    content = b"synthetic database reference to pending path"
    digest = hashlib.sha256(content).hexdigest()
    pending.write_bytes(content)
    statement_id = _statement(pending, digest)
    audit_id = _delete_audit(pending, digest)
    try:
        with pytest.raises(RuntimeError, match="数据库记录直接引用待处理路径"):
            reconcile_statement_delete_pending_files()
        assert pending.read_bytes() == content
        assert not original.exists()
    finally:
        _cleanup(
            original=original,
            pending=pending,
            statement_id=statement_id,
            audit_id=audit_id,
        )


@pytest.mark.parametrize("invalid_root", ["file", "junction"])
def test_pending_scan_requires_nonlinked_ordinary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_root: str,
) -> None:
    statement_root = tmp_path / "statement_imports"
    if invalid_root == "file":
        statement_root.write_bytes(b"not a directory")
    else:
        statement_root.mkdir()
        original_is_junction = Path.is_junction

        def emulate_junction(path: Path) -> bool:
            if path == statement_root:
                return True
            return original_is_junction(path)

        monkeypatch.setattr(Path, "is_junction", emulate_junction)

    with pytest.raises(RuntimeError, match="非链接普通目录"):
        _pending_candidates(statement_root)


@pytest.mark.parametrize(
    "damage",
    ["unproven", "database_hash_mismatch", "audit_hash_mismatch", "path_conflict"],
)
def test_startup_recovery_stops_on_ambiguous_or_inconsistent_evidence(
    damage: str,
) -> None:
    init_db()
    original, pending = _paths(damage)
    content = f"synthetic-{damage}".encode()
    digest = hashlib.sha256(content).hexdigest()
    pending.write_bytes(content)
    statement_id: int | None = None
    audit_id: int | None = None
    if damage == "database_hash_mismatch":
        statement_id = _statement(original, "0" * 64)
    elif damage == "audit_hash_mismatch":
        audit_id = _delete_audit(pending, "f" * 64)
    elif damage == "path_conflict":
        original.write_bytes(content)
        statement_id = _statement(original, digest)

    try:
        with pytest.raises(RuntimeError, match="系统已停止启动"):
            reconcile_statement_delete_pending_files()
        assert pending.read_bytes() == content
        if damage == "path_conflict":
            assert original.read_bytes() == content
        else:
            assert not original.exists()
    finally:
        _cleanup(
            original=original,
            pending=pending,
            statement_id=statement_id,
            audit_id=audit_id,
        )


@pytest.mark.parametrize("state", ["missing", "orphan", "committed"])
def test_startup_recovery_reconciles_upload_pending_marker(state: str) -> None:
    init_db()
    statement_root = get_settings().data_root / "statement_imports"
    content = f"synthetic upload pending {state} {uuid4().hex}".encode()
    digest = hashlib.sha256(content).hexdigest()
    original = statement_root / f"{digest}.jpg"
    marker = _create_upload_pending_marker(original, sha256=digest)
    statement_id: int | None = None
    if state != "missing":
        original.write_bytes(content)
    if state == "committed":
        statement_id = _statement(original, digest)

    try:
        assert reconcile_statement_delete_pending_files() == {
            "restored": 0,
            "cleaned": 1,
        }
        assert not marker.exists()
        assert original.exists() is (state == "committed")
        if statement_id is not None:
            with SessionLocal() as db:
                assert db.get(StatementImport, statement_id) is not None
    finally:
        _cleanup(
            original=original,
            pending=marker,
            statement_id=statement_id,
            audit_id=None,
        )


def test_startup_recovery_rejects_conflicting_upload_marker_and_duplicate_markers() -> None:
    init_db()
    statement_root = get_settings().data_root / "statement_imports"
    content = f"synthetic upload marker conflict {uuid4().hex}".encode()
    digest = hashlib.sha256(content).hexdigest()
    original = statement_root / f"{digest}.jpg"
    original.write_bytes(content)
    first_marker = _create_upload_pending_marker(original, sha256=digest)
    second_marker = _create_upload_pending_marker(original, sha256=digest)
    try:
        with pytest.raises(RuntimeError, match="多个待处理标记"):
            reconcile_statement_delete_pending_files()
        assert original.read_bytes() == content
        assert first_marker.is_file()
        assert second_marker.is_file()
    finally:
        for path in (first_marker, second_marker, original):
            path.unlink(missing_ok=True)


def test_startup_recovery_rejects_upload_marker_database_hash_conflict() -> None:
    init_db()
    statement_root = get_settings().data_root / "statement_imports"
    content = f"synthetic upload marker db conflict {uuid4().hex}".encode()
    digest = hashlib.sha256(content).hexdigest()
    original = statement_root / f"{digest}.jpg"
    original.write_bytes(content)
    marker = _create_upload_pending_marker(original, sha256=digest)
    statement_id = _statement(original, "0" * 64)
    try:
        with pytest.raises(RuntimeError, match="哈希冲突"):
            reconcile_statement_delete_pending_files()
        assert original.read_bytes() == content
        assert marker.is_file()
    finally:
        _cleanup(
            original=original,
            pending=marker,
            statement_id=statement_id,
            audit_id=None,
        )


def test_lifespan_reconciles_statement_delete_after_restore_and_database_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class _Server:
        def close(self) -> None:
            order.append("close")

    monkeypatch.setattr(
        main_module,
        "apply_pending_restore",
        lambda: order.append("restore"),
    )
    monkeypatch.setattr(main_module, "init_db", lambda: order.append("init_db"))
    monkeypatch.setattr(
        main_module,
        "reconcile_statement_delete_pending_files",
        lambda: order.append("statement_recovery"),
    )
    monkeypatch.setattr(main_module, "get_codex_app_server", lambda: _Server())

    async def run_lifespan() -> None:
        async with main_module.lifespan(main_module.app):
            order.append("serving")

    asyncio.run(run_lifespan())
    assert order == ["restore", "init_db", "statement_recovery", "serving", "close"]
