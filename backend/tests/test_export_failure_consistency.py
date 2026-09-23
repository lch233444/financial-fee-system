"""Keep generated exports consistent with their durable archive records."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import ExportRecord
from app.routes import system
from app.services.excel_export import file_sha256
from test_invoice_aggregation import WRITE_HEADERS, _finalized_settlement, _group


@pytest.fixture(scope="module")
def settlement_id():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, f"EXFAIL{uuid4().hex[:8]}", platform_count=1)
        yield _finalized_settlement(client, data, 0, year=2026, quarter=1)["id"]


@pytest.fixture(params=["excel", "pdf"])
def export_case(request, monkeypatch, tmp_path, settlement_id):
    settings = SimpleNamespace(
        data_root=tmp_path,
        template_path=get_settings().template_path,
    )
    monkeypatch.setattr(system, "get_settings", lambda: settings)
    generator_name = (
        "export_settlements_to_template" if request.param == "excel" else "generate_settlement_pdf"
    )
    generator = getattr(system, generator_name)
    generated_paths = []

    def track_generation(**kwargs):
        generated_paths.append(kwargs["output_path"])
        return generator(**kwargs)

    monkeypatch.setattr(system, generator_name, track_generation)

    def invoke(db):
        if request.param == "excel":
            return system.export_excel(str(settlement_id), fee_plan_id=None, db=db)
        return system.export_settlement_pdf(settlement_id, language="en", db=db)

    # Every failure must preserve a real, previously registered export.
    with SessionLocal() as db:
        old_path = Path(invoke(db).path)
    old_bytes = old_path.read_bytes()
    generated_paths.clear()
    try:
        yield SimpleNamespace(
            invoke=invoke,
            generator_name=generator_name,
            generated_paths=generated_paths,
            old_path=old_path,
        )
        assert old_path.read_bytes() == old_bytes
    finally:
        # This test's output lives in tmp_path, outside the shared synthetic
        # data root. Do not leave archive references that invalidate backups.
        with SessionLocal() as db:
            db.execute(delete(ExportRecord).where(
                ExportRecord.stored_path.in_([str(path) for path in [old_path, *generated_paths]])
            ))
            db.commit()


def _archive(path):
    with SessionLocal() as db:
        return db.scalar(select(ExportRecord).where(ExportRecord.stored_path == str(path)))


@pytest.mark.parametrize("stage", ["generation", "hash", "add", "commit"])
def test_failed_export_removes_only_its_unregistered_file(export_case, monkeypatch, stage):
    case = export_case
    with SessionLocal() as db:
        if stage == "generation":
            def fail_generation(**kwargs):
                path = kwargs["output_path"]
                case.generated_paths.append(path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"incomplete synthetic export")
                raise RuntimeError("injected generation failure")

            monkeypatch.setattr(system, case.generator_name, fail_generation)
        elif stage == "hash":
            def fail_hash(_path):
                raise RuntimeError("injected hash failure")

            monkeypatch.setattr(system, "file_sha256", fail_hash)
        elif stage == "add":
            def fail_add(_record):
                raise RuntimeError("injected add failure")

            monkeypatch.setattr(db, "add", fail_add)
        else:
            def fail_commit():
                db.flush()
                raise RuntimeError("injected commit failure")

            monkeypatch.setattr(db, "commit", fail_commit)

        with pytest.raises(RuntimeError, match=f"injected {stage} failure"):
            case.invoke(db)
        transaction_ended = not db.in_transaction()

    assert len(case.generated_paths) == 1
    failed_path = case.generated_paths[0]
    assert not failed_path.exists()
    assert transaction_ended
    assert _archive(failed_path) is None
    assert _archive(case.old_path) is not None


def test_commit_that_succeeds_then_raises_preserves_registered_export(export_case, monkeypatch):
    case = export_case
    with SessionLocal() as db:
        original_commit = db.commit

        def commit_then_raise():
            original_commit()
            raise SQLAlchemyError("injected error after durable commit")

        monkeypatch.setattr(db, "commit", commit_then_raise)
        with pytest.raises(SQLAlchemyError, match="after durable commit"):
            case.invoke(db)

    path = case.generated_paths[0]
    record = _archive(path)
    assert record is not None
    assert path.is_file()
    assert file_sha256(path) == record.sha256


@pytest.mark.parametrize("committed", [False, True])
def test_unknown_commit_outcome_preserves_file_for_reconciliation(export_case, monkeypatch, committed):
    case = export_case
    with SessionLocal() as db, monkeypatch.context() as failure:
        original_scalar = Session.scalar
        original_commit = db.commit

        def fail_commit():
            if committed:
                original_commit()
            else:
                db.flush()
            raise SQLAlchemyError("injected commit failure")

        def fail_independent_query(self, *args, **kwargs):
            if self is not db:
                raise SQLAlchemyError("injected reconciliation query failure")
            return original_scalar(self, *args, **kwargs)

        failure.setattr(db, "commit", fail_commit)
        failure.setattr(Session, "scalar", fail_independent_query)
        with pytest.raises(HTTPException) as caught:
            case.invoke(db)
        assert caught.value.status_code == 500
        assert "保留现场" in caught.value.detail

    path = case.generated_paths[0]
    assert path.is_file()
    assert (_archive(path) is not None) is committed


def test_failed_rollback_preserves_file(export_case, monkeypatch):
    case = export_case
    with SessionLocal() as db, monkeypatch.context() as failure:
        def fail_commit():
            raise SQLAlchemyError("injected commit failure")

        def fail_rollback():
            raise SQLAlchemyError("injected rollback failure")

        failure.setattr(db, "commit", fail_commit)
        failure.setattr(db, "rollback", fail_rollback)
        with pytest.raises(HTTPException) as caught:
            case.invoke(db)
        assert caught.value.status_code == 500
        assert "保留现场" in caught.value.detail

    assert case.generated_paths[0].is_file()


def test_cleanup_failure_is_visible_and_preserves_other_exports(export_case, monkeypatch):
    case = export_case
    with SessionLocal() as db, monkeypatch.context() as failure:
        original_unlink = Path.unlink

        def fail_commit():
            raise SQLAlchemyError("injected commit failure")

        def fail_unlink(self, *args, **kwargs):
            if self in case.generated_paths:
                raise PermissionError("injected cleanup failure")
            return original_unlink(self, *args, **kwargs)

        failure.setattr(db, "commit", fail_commit)
        failure.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(HTTPException) as caught:
            case.invoke(db)
        assert caught.value.status_code == 500
        assert "保留现场" in caught.value.detail

    path = case.generated_paths[0]
    assert path.is_file()
    assert _archive(path) is None


def test_successful_export_keeps_file_and_matching_archive(export_case):
    case = export_case
    with SessionLocal() as db:
        response = case.invoke(db)
    path = Path(response.path)
    assert path != case.old_path
    assert path == case.generated_paths[0]
    record = _archive(path)
    assert record is not None
    assert record.sha256 == file_sha256(path)


def test_filename_collision_never_overwrites_or_removes_existing_export(export_case, monkeypatch):
    case = export_case
    timestamp_parts = case.old_path.stem.split("_")[-4:-1]
    timestamp = datetime.strptime("_".join(timestamp_parts), "%Y%m%d_%H%M%S_%f")
    monkeypatch.setattr(system, "datetime", SimpleNamespace(now=lambda: timestamp))
    random_part = case.old_path.stem.split("_")[-1]
    monkeypatch.setattr(system, "uuid4", lambda: SimpleNamespace(hex=random_part))
    with SessionLocal() as db, pytest.raises(FileExistsError):
        case.invoke(db)
    assert not case.generated_paths
    assert _archive(case.old_path) is not None
