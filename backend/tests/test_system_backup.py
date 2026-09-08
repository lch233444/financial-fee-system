from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import warnings
import zipfile
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import app.config as config_module
from app.config import Settings, get_settings
from app.database import SessionLocal, engine, init_db
from app import main as app_main
from app.main import app
from app.models import (
    AppSetting,
    Attachment,
    Client,
    Company,
    ExportRecord,
    FC,
    FeePlan,
    Invoice,
    InvoiceCorrection,
    Payment,
    PaymentAllocation,
    PaymentRefund,
    Platform,
    QuarterlySettlement,
    StatementImport,
)
from app.routes import system as system_routes
from app.services import backup as backup_service
from app.services.backup import apply_pending_restore, create_backup, stage_restore, validate_backup_archive_root
from app.services.entity_ids import allocate_entity_id
from app.services.shutdown import get_shutdown_coordinator
from app.services.storage import sha256_file


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


@pytest.fixture(autouse=True)
def clean_pending_restore_artifacts() -> None:
    settings = get_settings()
    marker = settings.data_root / "pending_restore.json"
    marker.unlink(missing_ok=True)
    for pattern in ("pending_restore*", "restore_prepare_*", "restore_apply_*"):
        for path in (settings.data_root / "tmp").glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    yield
    marker.unlink(missing_ok=True)
    for pattern in ("pending_restore*", "restore_prepare_*", "restore_apply_*"):
        for path in (settings.data_root / "tmp").glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def _valid_backup() -> Path:
    init_db()
    return create_backup()


def _archive_files(backup_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(backup_path) as archive:
        return {member.filename: archive.read(member) for member in archive.infolist() if not member.is_dir()}


def _write_variant(
    source: Path,
    destination: Path,
    mutate: Callable[[dict[str, bytes], dict], None],
) -> Path:
    files = _archive_files(source)
    manifest = json.loads(files["manifest.json"])
    mutate(files, manifest)
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in files.items():
            archive.writestr(relative_path, content)
    return destination


def _mark_as_legacy_backup(manifest: dict) -> None:
    manifest["format"] = backup_service.LEGACY_BACKUP_FORMAT
    manifest.pop("app_version", None)
    manifest.pop("snapshot_period", None)


def _mutate_archive_database(
    files: dict[str, bytes],
    manifest: dict,
    database_copy: Path,
    mutate: Callable[[sqlite3.Connection], None],
) -> None:
    database_record = next(
        record for record in manifest["files"] if record["path"].startswith("database/")
    )
    database_copy.write_bytes(files[database_record["path"]])
    connection = sqlite3.connect(database_copy)
    try:
        mutate(connection)
        connection.commit()
    finally:
        connection.close()
    content = database_copy.read_bytes()
    files[database_record["path"]] = content
    database_record["sha256"] = hashlib.sha256(content).hexdigest()


def _assert_restore_rejected(backup_path: Path) -> None:
    settings = get_settings()
    marker = settings.data_root / "pending_restore.json"
    with pytest.raises(ValueError):
        stage_restore(backup_path)
    assert not marker.exists()
    assert not list((settings.data_root / "tmp").glob("pending_restore*"))


def _seed_statement_import() -> tuple[StatementImport, Path, bytes]:
    init_db()
    settings = get_settings()
    token = uuid4().hex[:12]
    content = b"\xff\xd8\xffsynthetic-backup-statement-" + token.encode("ascii")
    source_path = settings.data_root / "statement_imports" / f"statement-{token}.jpg"
    source_path.write_bytes(content)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        item = StatementImport(
            id=allocate_entity_id(db, StatementImport),
            original_name=source_path.name,
            stored_path=str(source_path),
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type="image/jpeg",
            parser_name="TEST",
            parser_version="1.1",
            status="NEEDS_REVIEW",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        db.expunge(item)
    return item, source_path, content


def _seed_payment_and_refund_proofs() -> dict[str, object]:
    init_db()
    settings = get_settings()
    token = uuid4().hex[:12]
    payment_bytes = f"payment-proof-{token}".encode()
    refund_bytes = f"refund-proof-{token}".encode()
    payment_path = settings.data_root / "attachments" / f"payment-proof-{token}.pdf"
    refund_path = settings.data_root / "attachments" / f"refund-proof-{token}.pdf"
    payment_path.write_bytes(payment_bytes)
    refund_path.write_bytes(refund_bytes)

    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        company = Company(name=f"Backup Proof {token}", code=f"B{token}")
        db.add(company)
        db.flush()
        fc = FC(company_id=company.id, name=f"FC {token}", code=f"F{token}")
        plan = FeePlan(company_id=company.id, name=f"Plan {token}", code=f"P{token}")
        platform = Platform(name=f"Platform {token}", code=f"PL{token}")
        db.add_all([fc, plan, platform])
        db.flush()
        customer = Client(
            id=allocate_entity_id(db, Client),
            company_id=company.id,
            fc_id=fc.id,
            name=f"Client {token}",
            management_start_date=date(2026, 1, 1),
            status="ACTIVE",
        )
        db.add(customer)
        db.flush()
        settlement = QuarterlySettlement(
            client_id=customer.id,
            platform_id=platform.id,
            fee_plan_id=plan.id,
            company_id=company.id,
            fc_id=fc.id,
            version_no=1,
            year=2026,
            quarter=1,
            start_date=date(2026, 1, 1),
            closing_date=date(2026, 3, 31),
            days=90,
            beginning_cents=10_000,
            contribution_cents=0,
            withdrawal_cents=0,
            net_contribution_cents=0,
            closing_cents=11_000,
            gain_loss_cents=1_000,
            period_rate_ppm=100_000,
            original_hwm_cents=10_000,
            adjusted_hwm_cents=10_000,
            watermark_difference_cents=1_000,
            chargeable_above_hwm_cents=1_000,
            service_fee_cents=1_000,
            next_hwm_cents=11_000,
            fee_rate_bps=2_000,
            formula_version="HWM-1.0",
            calculation_mode="ACCOUNT_HWM",
            status="DRAFT",
        )
        db.add(settlement)
        db.flush()
        invoice = Invoice(
            settlement_id=settlement.id,
            client_id=customer.id,
            year=2026,
            quarter=1,
            fee_plan_id=plan.id,
            company_id=company.id,
            fc_id=fc.id,
            lifecycle_status="DRAFT",
            amount_cents=1_000,
            language="zh",
        )
        db.add(invoice)
        db.commit()
        invoice_id = invoice.id

    with closing(sqlite3.connect(settings.database_path)) as connection, connection:
        trigger_names = (
            "trg_invoice_validate_issue",
            "trg_invoice_lifecycle_transition",
            "trg_invoice_issue_metadata_guard",
        )
        trigger_sql = {
            name: connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (name,),
            ).fetchone()[0]
            for name in trigger_names
        }
        try:
            for name in trigger_names:
                connection.execute(f'DROP TRIGGER "{name}"')
            connection.execute(
                """
                UPDATE invoices
                SET invoice_number = ?, lifecycle_status = 'ISSUED',
                    issue_date = '2026-04-01', due_date = '2026-04-15',
                    issued_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (f"BACKUP-{token}", invoice_id),
            )
        finally:
            for name in trigger_names:
                if connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                    (name,),
                ).fetchone() is None:
                    connection.execute(trigger_sql[name])
            connection.commit()

    with SessionLocal() as db:
        payment_proof = Attachment(
            entity_type="PAYMENT",
            entity_id=None,
            original_name=f"payment-{token}.pdf",
            stored_path=str(payment_path),
            sha256=hashlib.sha256(payment_bytes).hexdigest(),
            mime_type="application/pdf",
            size_bytes=len(payment_bytes),
        )
        db.add(payment_proof)
        db.flush()
        payment = Payment(
            invoice_id=invoice_id,
            payment_date=date(2026, 4, 2),
            amount_cents=1_000,
            company_difference_cents=0,
            difference_reason=None,
            method="BANK_TRANSFER",
            proof_attachment_id=payment_proof.id,
        )
        db.add(payment)
        db.flush()
        initial_allocation = db.scalar(
            select(PaymentAllocation).where(PaymentAllocation.payment_id == payment.id)
        )
        assert initial_allocation is not None
        correction = InvoiceCorrection(
            original_invoice_id=invoice_id,
            status="OPEN",
            reason="Synthetic valid refund proof chain",
        )
        db.add(correction)
        db.flush()
        db.add(
            PaymentAllocation(
                payment_id=payment.id,
                invoice_id=invoice_id,
                amount_cents=1_000,
                entry_type="REVERSAL",
                reverses_allocation_id=initial_allocation.id,
                correction_id=correction.id,
            )
        )
        db.flush()
        invoice = db.get(Invoice, invoice_id)
        assert invoice is not None
        invoice.lifecycle_status = "VOID"
        invoice.voided_at = datetime.now(timezone.utc)
        invoice.void_reason = "Synthetic refund proof chain"
        db.flush()
        refund_proof = Attachment(
            entity_type="PAYMENT_REFUND",
            entity_id=None,
            original_name=f"refund-{token}.pdf",
            stored_path=str(refund_path),
            sha256=hashlib.sha256(refund_bytes).hexdigest(),
            mime_type="application/pdf",
            size_bytes=len(refund_bytes),
        )
        db.add(refund_proof)
        db.flush()
        refund = PaymentRefund(
            payment_id=payment.id,
            correction_id=correction.id,
            refund_date=date(2026, 4, 3),
            amount_cents=1_000,
            method="BANK_TRANSFER",
            reason="Synthetic full refund",
            proof_attachment_id=refund_proof.id,
        )
        db.add(refund)
        db.commit()
        return {
            "payment_path": payment_path,
            "payment_bytes": payment_bytes,
            "payment_attachment_id": payment_proof.id,
            "refund_path": refund_path,
            "refund_bytes": refund_bytes,
            "refund_attachment_id": refund_proof.id,
        }


def test_backup_creation_uses_unique_atomic_names_within_same_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 20, 30, 40, 123456, tzinfo=tz)

    monkeypatch.setattr(backup_service, "datetime", FrozenDateTime)
    first = create_backup()
    second = create_backup()

    assert first != second
    assert first.name.startswith(
        "financial_system_data_package_2026_Q3_20260831_203040_123456_"
    )
    assert second.name.startswith(
        "financial_system_data_package_2026_Q3_20260831_203040_123456_"
    )
    for backup_path in (first, second):
        assert backup_path.is_file()
        with zipfile.ZipFile(backup_path) as archive:
            assert archive.testzip() is None
            assert "manifest.json" in archive.namelist()
    assert not list(first.parent.glob(".financial_system_data_package_*.zip.tmp"))


def test_data_package_manifest_records_same_version_and_review_period() -> None:
    init_db()
    package_path = create_backup(snapshot_year=2026, snapshot_quarter=2)

    with zipfile.ZipFile(package_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))

    assert manifest["format"] == backup_service.BACKUP_FORMAT
    assert manifest["version"] == backup_service.BACKUP_VERSION
    assert manifest["app_version"] == config_module.APP_VERSION
    assert manifest["snapshot_period"] == {"year": 2026, "quarter": 2}


def test_restore_rejects_data_package_from_different_system_version(tmp_path: Path) -> None:
    package_path = _valid_backup()

    def change_app_version(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["app_version"] = "99.99.99"

    invalid_package = _write_variant(
        package_path,
        tmp_path / "different-system-version.zip",
        change_app_version,
    )
    with pytest.raises(ValueError, match="系统版本不一致"):
        stage_restore(invalid_package)


def test_data_package_rebases_all_persisted_file_paths_for_another_computer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    statement, statement_path, statement_content = _seed_statement_import()
    seeded = _seed_payment_and_refund_proofs()
    source_settings = get_settings()
    invoice_pdf = source_settings.data_root / "output" / "pdf" / f"portable-{uuid4().hex}.pdf"
    invoice_pdf.write_bytes(b"synthetic portable invoice")
    extra_export = source_settings.data_root / "output" / "excel" / f"portable-{uuid4().hex}.xlsx"
    extra_export.write_bytes(b"synthetic portable export")

    with SessionLocal() as db:
        payment = db.scalar(
            select(Payment).where(
                Payment.proof_attachment_id == seeded["payment_attachment_id"]
            )
        )
        assert payment is not None
        invoice = db.get(Invoice, payment.invoice_id)
        assert invoice is not None
        invoice.pdf_paths_json = {"zh": str(invoice_pdf)}
        db.add_all(
            [
                ExportRecord(
                    export_type="PDF_INVOICE",
                    entity_type="INVOICE",
                    entity_id=invoice.id,
                    stored_path=str(invoice_pdf),
                    sha256=sha256_file(invoice_pdf),
                    language="zh",
                ),
                ExportRecord(
                    export_type="EXCEL_INTERNAL",
                    entity_type="SETTLEMENT_BATCH",
                    entity_id=1,
                    stored_path=str(extra_export),
                    sha256=sha256_file(extra_export),
                ),
            ]
        )
        db.commit()
        invoice_id = invoice.id

    package_path = create_backup(snapshot_year=2026, snapshot_quarter=2)
    target_settings = Settings(data_root=tmp_path / "receiving-computer-data")
    target_settings.ensure_directories()
    monkeypatch.setattr(backup_service, "get_settings", lambda: target_settings)

    stage_restore(package_path)
    assert apply_pending_restore() is True

    target_statement_path = target_settings.data_root / "statement_imports" / statement_path.name
    target_payment_path = target_settings.data_root / "attachments" / Path(seeded["payment_path"]).name
    target_refund_path = target_settings.data_root / "attachments" / Path(seeded["refund_path"]).name
    target_invoice_pdf = target_settings.data_root / "output" / "pdf" / invoice_pdf.name
    target_extra_export = target_settings.data_root / "output" / "excel" / extra_export.name
    assert target_statement_path.read_bytes() == statement_content
    assert target_payment_path.read_bytes() == seeded["payment_bytes"]
    assert target_refund_path.read_bytes() == seeded["refund_bytes"]
    assert target_invoice_pdf.read_bytes() == invoice_pdf.read_bytes()
    assert target_extra_export.read_bytes() == extra_export.read_bytes()

    with closing(sqlite3.connect(target_settings.database_path)) as connection, connection:
        assert connection.execute(
            "SELECT stored_path FROM statement_imports WHERE id = ?", (statement.id,)
        ).fetchone() == (str(target_statement_path),)
        assert connection.execute(
            "SELECT stored_path FROM attachments WHERE id = ?",
            (seeded["payment_attachment_id"],),
        ).fetchone() == (str(target_payment_path),)
        assert connection.execute(
            "SELECT stored_path FROM attachments WHERE id = ?",
            (seeded["refund_attachment_id"],),
        ).fetchone() == (str(target_refund_path),)
        restored_invoice_paths = json.loads(
            connection.execute(
                "SELECT pdf_paths_json FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()[0]
        )
        assert restored_invoice_paths == {"zh": str(target_invoice_pdf)}
        restored_export_paths = {
            row[0]
            for row in connection.execute(
                "SELECT stored_path FROM export_records WHERE stored_path LIKE ?",
                (f"{target_settings.data_root}%",),
            ).fetchall()
        }
        assert str(target_invoice_pdf) in restored_export_paths
        assert str(target_extra_export) in restored_export_paths
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"
        ).fetchone() == (57,)


def test_backup_creation_does_not_publish_or_leave_temp_when_self_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    settings = get_settings()
    before = set((settings.data_root / "backups").glob("financial_system_data_package_*.zip"))

    def reject_backup(_backup_path: Path, _extract_root: Path) -> dict:
        raise ValueError("synthetic validation failure")

    monkeypatch.setattr(backup_service, "validate_backup", reject_backup)
    with pytest.raises(ValueError, match="synthetic validation failure"):
        create_backup()

    after = set((settings.data_root / "backups").glob("financial_system_data_package_*.zip"))
    assert after == before
    assert not list((settings.data_root / "backups").glob(".financial_system_data_package_*.zip.tmp"))


def test_backup_round_trip_validates_every_statement_import_source() -> None:
    item, source_path, content = _seed_statement_import()

    backup_path = create_backup()

    relative_path = f"statement_imports/{source_path.name}"
    with zipfile.ZipFile(backup_path) as archive:
        assert archive.read(relative_path) == content
    extracted = backup_path.parent / f"validated-statement-{item.id}-{uuid4().hex}"
    try:
        backup_service.validate_backup(backup_path, extracted)
    finally:
        shutil.rmtree(extracted, ignore_errors=True)


@pytest.mark.parametrize("mutation", ["missing", "tampered", "orphan-pending"])
def test_backup_creation_rejects_inconsistent_statement_import_files(mutation: str) -> None:
    _item, source_path, content = _seed_statement_import()
    settings = get_settings()
    before = set((settings.data_root / "backups").glob("financial_system_data_package_*.zip"))
    pending: Path | None = None

    if mutation == "missing":
        source_path.unlink()
    elif mutation == "tampered":
        source_path.write_bytes(content + b"tampered")
    else:
        pending = source_path.with_name(
            f".{source_path.name}.{uuid4().hex}.delete-pending"
        )
        pending.write_bytes(b"orphan pending statement")

    try:
        with pytest.raises(ValueError, match="账单原件|statement_imports"):
            create_backup()
    finally:
        source_path.write_bytes(content)
        if pending is not None:
            pending.unlink(missing_ok=True)

    assert set((settings.data_root / "backups").glob("financial_system_data_package_*.zip")) == before
    assert not list((settings.data_root / "backups").glob(".financial_system_data_package_*.zip.tmp"))


def test_backup_creation_rejects_statement_delete_race_after_database_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _item, source_path, _content = _seed_statement_import()
    pending_path = source_path.with_name(
        f".{source_path.name}.{uuid4().hex}.delete-pending"
    )
    original_validate = backup_service._validate_sqlite_database
    raced = False

    def validate_then_stage(database_path: Path) -> None:
        nonlocal raced
        original_validate(database_path)
        if not raced:
            raced = True
            source_path.replace(pending_path)

    monkeypatch.setattr(backup_service, "_validate_sqlite_database", validate_then_stage)
    try:
        with pytest.raises(ValueError, match="账单原件|statement_imports"):
            create_backup()
    finally:
        if pending_path.exists() and not source_path.exists():
            pending_path.replace(source_path)

    assert raced is True


def test_restore_rejects_manifest_consistent_orphan_statement_pending_file(
    tmp_path: Path,
) -> None:
    _item, source_path, content = _seed_statement_import()
    valid = create_backup()
    tampered = tmp_path / "statement-pending.zip"
    relative_path = f"statement_imports/{source_path.name}"
    pending_relative = (
        f"statement_imports/.{source_path.name}.{uuid4().hex}.delete-pending"
    )

    def mutate(files: dict[str, bytes], manifest: dict) -> None:
        files.pop(relative_path)
        files[pending_relative] = content
        for record in manifest["files"]:
            if record["path"] == relative_path:
                record["path"] = pending_relative
                record["sha256"] = hashlib.sha256(content).hexdigest()
                break

    _write_variant(valid, tampered, mutate)
    _assert_restore_rejected(tampered)


@pytest.mark.parametrize(
    ("proof_kind", "mutation"),
    [
        ("payment", "missing"),
        ("payment", "tampered"),
        ("refund", "missing"),
    ],
)
def test_backup_creation_rejects_missing_or_tampered_financial_proof(
    proof_kind: str, mutation: str
) -> None:
    seeded = _seed_payment_and_refund_proofs()
    settings = get_settings()
    proof_path = Path(seeded[f"{proof_kind}_path"])
    original = bytes(seeded[f"{proof_kind}_bytes"])
    before = set((settings.data_root / "backups").glob("financial_system_data_package_*.zip"))
    try:
        if mutation == "missing":
            proof_path.unlink()
        else:
            proof_path.write_bytes(bytes(byte ^ 0x01 for byte in original))
        with pytest.raises(ValueError, match="付款凭证"):
            create_backup()
    finally:
        proof_path.write_bytes(original)
    assert set((settings.data_root / "backups").glob("financial_system_data_package_*.zip")) == before
    assert not list((settings.data_root / "backups").glob(".financial_system_data_package_*.zip.tmp"))


def test_archive_context_rejects_legacy_payment_without_proof(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-null-proof.sqlite3"
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE payments (id INTEGER PRIMARY KEY, proof_attachment_id INTEGER);
            CREATE TABLE attachments (
                id INTEGER PRIMARY KEY,
                entity_type TEXT,
                entity_id INTEGER,
                stored_path TEXT,
                size_bytes INTEGER,
                sha256 TEXT
            );
            INSERT INTO payments (id, proof_attachment_id) VALUES (1, NULL);
            """
        )
    with pytest.raises(ValueError, match="付款凭证关系"):
        backup_service._validate_payment_proof_archive(database_path, tmp_path, {})


def test_restore_request_is_exclusive_before_upload_read() -> None:
    backup_path = _valid_backup()
    with system_routes._restore_request_lock:
        with TestClient(app) as client:
            response = client.post(
                "/api/backups/restore",
                headers=WRITE_HEADERS,
                files={"file": (backup_path.name, backup_path.read_bytes(), "application/zip")},
            )
    assert response.status_code == 409
    assert "另一份数据包正在校验" in response.json()["detail"]


def test_financial_mutation_gate_rejects_write_already_overlapping_restore() -> None:
    assert app_main._restore_mutation_gate.try_enter_restore() is True
    try:
        with TestClient(app) as client:
            response = client.post("/api/transactions", headers=WRITE_HEADERS, json={})
    finally:
        app_main._restore_mutation_gate.leave_restore()
    assert response.status_code == 409
    assert "数据包导入正在校验" in response.json()["detail"]


def test_restore_gate_rejects_restore_while_financial_write_is_active() -> None:
    assert app_main._restore_mutation_gate.try_enter_mutation() is True
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/backups/restore",
                headers=WRITE_HEADERS,
                files={"file": ("synthetic.zip", b"not-read", "application/zip")},
            )
    finally:
        app_main._restore_mutation_gate.leave_mutation()
    assert response.status_code == 409
    assert "仍有财务写入" in response.json()["detail"]


def test_successful_restore_staging_schedules_shutdown_and_blocks_later_writes() -> None:
    backup_path = _valid_backup()

    class FakeShutdownCoordinator:
        def __init__(self) -> None:
            self.events: list[str] = []

        def request(self) -> bool:
            self.events.append("request")
            return True

        def execute(self) -> None:
            self.events.append("execute")

    fake = FakeShutdownCoordinator()
    app.dependency_overrides[get_shutdown_coordinator] = lambda: fake
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/backups/restore",
                headers=WRITE_HEADERS,
                files={"file": (backup_path.name, backup_path.read_bytes(), "application/zip")},
            )
            assert response.status_code == 200
            assert response.json()["staged"] is True
            assert response.json()["shutdown_scheduled"] is True

            blocked = client.post("/api/transactions", headers=WRITE_HEADERS, json={})
            assert blocked.status_code == 409
            assert "已停止接受财务写入" in blocked.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_shutdown_coordinator, None)

    assert fake.events == ["request", "execute"]


def test_backup_restore_preserves_database_files_and_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    # Keep references so garbage collection cannot hide a leaked fixture
    # connection and make Windows directory replacement depend on test order.
    connections: list[sqlite3.Connection] = []
    original_connect = sqlite3.connect

    def track_connection(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect", track_connection)
        _seed_payment_and_refund_proofs()
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "backup-roundtrip.txt"
    attachment.write_text("original attachment", encoding="utf-8")

    with SessionLocal() as db:
        existing = db.get(AppSetting, "backup_roundtrip")
        if existing:
            existing.value = "original database value"
        else:
            db.add(AppSetting(key="backup_roundtrip", value="original database value"))
        db.commit()

    backup_path = create_backup()
    with zipfile.ZipFile(backup_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for record in manifest["files"]:
            extracted = settings.data_root / "tmp" / "hash-check" / record["path"]
            extracted.parent.mkdir(parents=True, exist_ok=True)
            extracted.write_bytes(archive.read(record["path"]))
            assert sha256_file(extracted) == record["sha256"]

    with SessionLocal() as db:
        db.get(AppSetting, "backup_roundtrip").value = "mutated database value"
        db.commit()
    attachment.write_text("mutated attachment", encoding="utf-8")

    stage_restore(backup_path)
    engine.dispose()
    assert apply_pending_restore() is True
    engine.dispose()

    with SessionLocal() as db:
        restored = db.scalar(select(AppSetting).where(AppSetting.key == "backup_roundtrip"))
        assert restored is not None
        assert restored.value == "original database value"
    assert attachment.read_text(encoding="utf-8") == "original attachment"


def test_restore_rejects_backup_without_database(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def remove_database(files: dict[str, bytes], manifest: dict) -> None:
        database_paths = [record["path"] for record in manifest["files"] if record["path"].startswith("database/")]
        for relative_path in database_paths:
            files.pop(relative_path)
        manifest["files"] = [
            record for record in manifest["files"] if not record["path"].startswith("database/")
        ]

    invalid_backup = _write_variant(backup_path, tmp_path / "missing-database.zip", remove_database)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_empty_manifest_files(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def empty_files(files: dict[str, bytes], manifest: dict) -> None:
        files.clear()
        manifest["files"] = []

    invalid_backup = _write_variant(backup_path, tmp_path / "empty-files.zip", empty_files)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_duplicate_manifest_path(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def duplicate_record(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"].append(dict(manifest["files"][0]))

    invalid_backup = _write_variant(backup_path, tmp_path / "duplicate-path.zip", duplicate_record)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_manifest_record_with_extra_field(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def add_record_field(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["size"] = 123

    invalid_backup = _write_variant(backup_path, tmp_path / "record-extra-field.zip", add_record_field)
    _assert_restore_rejected(invalid_backup)


@pytest.mark.parametrize("invalid_version", [True, 1.0])
def test_restore_rejects_non_integer_manifest_version(tmp_path: Path, invalid_version: object) -> None:
    backup_path = _valid_backup()

    def change_version(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["version"] = invalid_version

    invalid_backup = _write_variant(backup_path, tmp_path / f"invalid-version-{invalid_version}.zip", change_version)
    _assert_restore_rejected(invalid_backup)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../database/financial_system.sqlite3",
        "attachments/CON",
        "attachments/trailing-dot.",
        "attachments/control\x01.txt",
    ],
)
def test_restore_rejects_unsafe_manifest_path(tmp_path: Path, unsafe_path: str) -> None:
    backup_path = _valid_backup()

    def change_path(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["path"] = unsafe_path

    invalid_backup = _write_variant(backup_path, tmp_path / "unsafe-manifest-path.zip", change_path)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_case_insensitive_duplicate_manifest_path(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def duplicate_record_with_different_case(_files: dict[str, bytes], manifest: dict) -> None:
        duplicate = dict(manifest["files"][0])
        parent, filename = duplicate["path"].split("/", 1)
        duplicate["path"] = f"{parent}/{filename.upper()}"
        manifest["files"].append(duplicate)

    invalid_backup = _write_variant(
        backup_path,
        tmp_path / "case-insensitive-duplicate-path.zip",
        duplicate_record_with_different_case,
    )
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_duplicate_archive_member(tmp_path: Path) -> None:
    backup_path = _valid_backup()
    files = _archive_files(backup_path)
    duplicate_path = next(relative_path for relative_path in files if relative_path != "manifest.json")
    invalid_backup = tmp_path / "duplicate-archive-member.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(invalid_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative_path, content in files.items():
                archive.writestr(relative_path, content)
            archive.writestr(duplicate_path, files[duplicate_path])

    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_unlisted_archive_file(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def add_unlisted_file(files: dict[str, bytes], _manifest: dict) -> None:
        files["attachments/not-in-manifest.txt"] = b"unlisted"

    invalid_backup = _write_variant(backup_path, tmp_path / "unlisted-file.zip", add_unlisted_file)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_hash_mismatch(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def change_hash(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["sha256"] = "0" * 64

    invalid_backup = _write_variant(backup_path, tmp_path / "bad-hash.zip", change_hash)
    _assert_restore_rejected(invalid_backup)


@pytest.mark.parametrize("proof_kind", ["payment", "refund"])
def test_restore_rejects_manifest_consistent_archive_missing_referenced_proof(
    tmp_path: Path, proof_kind: str
) -> None:
    seeded = _seed_payment_and_refund_proofs()
    settings = get_settings()
    backup_path = create_backup()
    relative_path = Path(seeded[f"{proof_kind}_path"]).relative_to(
        settings.data_root
    ).as_posix()

    def remove_referenced_proof(files: dict[str, bytes], manifest: dict) -> None:
        files.pop(relative_path)
        manifest["files"] = [
            record for record in manifest["files"] if record["path"] != relative_path
        ]

    invalid_backup = _write_variant(
        backup_path,
        tmp_path / f"missing-{proof_kind}-proof.zip",
        remove_referenced_proof,
    )
    _assert_restore_rejected(invalid_backup)


def test_restore_proof_mapping_does_not_depend_on_current_data_root(tmp_path: Path) -> None:
    seeded = _seed_payment_and_refund_proofs()
    settings = get_settings()
    backup_path = create_backup()
    relative_path = Path(seeded["payment_path"]).relative_to(settings.data_root).as_posix()
    rebased_stored_path = f"Z:/different-source-root/{relative_path}"

    def rebase_database_path(files: dict[str, bytes], manifest: dict) -> None:
        def mutate(connection: sqlite3.Connection) -> None:
            trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_attachment_update_block_payment_evidence'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER trg_attachment_update_block_payment_evidence")
            connection.execute(
                "UPDATE attachments SET stored_path = ? WHERE id = ?",
                (rebased_stored_path, seeded["payment_attachment_id"]),
            )
            connection.execute(trigger_sql)

        _mutate_archive_database(
            files, manifest, tmp_path / "rebased-proof-path.sqlite3", mutate
        )

    rebased_backup = _write_variant(
        backup_path,
        tmp_path / "rebased-source-root.zip",
        rebase_database_path,
    )
    staged = stage_restore(rebased_backup)
    assert staged.marker.is_file()


def test_restore_rejects_corrupt_sqlite_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def corrupt_database(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        corrupt_content = b"this is not a sqlite database"
        files[database_record["path"]] = corrupt_content
        database_record["sha256"] = hashlib.sha256(corrupt_content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "corrupt-database.zip", corrupt_database)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_foreign_key_violation_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def break_foreign_key(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        database_copy = tmp_path / "foreign-key-violation.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = OFF;
                CREATE TABLE backup_fk_parent (id INTEGER PRIMARY KEY);
                CREATE TABLE backup_fk_child (
                    id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL REFERENCES backup_fk_parent(id)
                );
                INSERT INTO backup_fk_child(id, parent_id) VALUES (1, 999);
                """
            )
            connection.commit()
        finally:
            connection.close()
        corrupt_content = database_copy.read_bytes()
        files[database_record["path"]] = corrupt_content
        database_record["sha256"] = hashlib.sha256(corrupt_content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "foreign-key-violation.zip", break_foreign_key)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_non_system_sqlite_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def replace_with_unrelated_database(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        unrelated_database = tmp_path / "unrelated.sqlite3"
        connection = sqlite3.connect(unrelated_database)
        try:
            connection.execute("CREATE TABLE unrelated_data (id INTEGER PRIMARY KEY, value TEXT)")
            connection.commit()
        finally:
            connection.close()
        content = unrelated_database.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "unrelated-database.zip", replace_with_unrelated_database)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_unknown_database_revision_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def replace_revision(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        database_copy = tmp_path / "future-revision.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.execute("UPDATE alembic_version SET version_num = 'future_revision_not_supported'")
            connection.commit()
        finally:
            connection.close()
        content = database_copy.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "future-revision.zip", replace_revision)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_new_head_with_incomplete_trigger_set(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def drop_required_trigger(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        database_copy = tmp_path / "missing-new-head-trigger.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.execute("DROP TRIGGER trg_client_delete_no_cascade")
            connection.commit()
        finally:
            connection.close()
        content = database_copy.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    invalid_backup = _write_variant(
        backup_path,
        tmp_path / "missing-new-head-trigger.zip",
        drop_required_trigger,
    )
    _assert_restore_rejected(invalid_backup)


@pytest.mark.parametrize(
    "replacement_sql",
    [
        """
        CREATE INDEX uq_settlement_group_period_active
        ON quarterly_settlements (client_id, platform_id, fee_plan_id, year, quarter)
        WHERE status != 'VOID'
        """,
        """
        CREATE UNIQUE INDEX uq_settlement_group_period_active
        ON quarterly_settlements (client_id, platform_id, fee_plan_id, year, id)
        WHERE status != 'VOID'
        """,
    ],
    ids=("same-name-non-unique", "same-name-wrong-columns"),
)
def test_restore_rejects_forged_active_settlement_partial_index(
    tmp_path: Path, replacement_sql: str
) -> None:
    backup_path = _valid_backup()

    def forge_active_index(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(
            record for record in manifest["files"] if record["path"].startswith("database/")
        )
        database_copy = tmp_path / "forged-active-index.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.execute("DROP INDEX uq_settlement_group_period_active")
            connection.execute(replacement_sql)
            connection.commit()
        finally:
            connection.close()
        content = database_copy.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    invalid_backup = _write_variant(
        backup_path,
        tmp_path / f"forged-active-index-{hashlib.sha256(replacement_sql.encode()).hexdigest()[:8]}.zip",
        forge_active_index,
    )
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_same_name_nonunique_active_invoice_index(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def forge_invoice_index(files: dict[str, bytes], manifest: dict) -> None:
        def mutate(connection: sqlite3.Connection) -> None:
            connection.execute("DROP INDEX uq_invoices_active_client_period_plan")
            connection.execute(
                """
                CREATE INDEX uq_invoices_active_client_period_plan
                ON invoices (client_id, year, quarter, fee_plan_id)
                WHERE lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
                """
            )

        _mutate_archive_database(
            files, manifest, tmp_path / "forged-active-invoice-index.sqlite3", mutate
        )

    invalid_backup = _write_variant(
        backup_path,
        tmp_path / "forged-active-invoice-index.zip",
        forge_invoice_index,
    )
    _assert_restore_rejected(invalid_backup)


def test_restore_accepts_complete_supported_9d_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_path = _valid_backup()
    legacy_settings = Settings(data_root=tmp_path / "legacy-9d-data")
    legacy_settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: legacy_settings)
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", legacy_settings.database_url)
    command.upgrade(alembic_config, "9d2f6a8c4b13")

    def replace_with_9d_database(files: dict[str, bytes], manifest: dict) -> None:
        _mark_as_legacy_backup(manifest)
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        content = legacy_settings.database_path.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()
        statement_paths = [
            path for path in files if path.startswith("statement_imports/")
        ]
        for statement_path in statement_paths:
            files.pop(statement_path)
        manifest["files"] = [
            record
            for record in manifest["files"]
            if not record["path"].startswith("statement_imports/")
        ]

    legacy_backup = _write_variant(
        backup_path,
        tmp_path / "supported-9d-backup.zip",
        replace_with_9d_database,
    )
    staged = stage_restore(legacy_backup)
    assert staged.marker.is_file()


def test_restore_accepts_complete_supported_7f_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_path = _valid_backup()
    legacy_settings = Settings(data_root=tmp_path / "legacy-7f-data")
    legacy_settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: legacy_settings)
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", legacy_settings.database_url)
    command.upgrade(alembic_config, "7f3c2a91b6e4")

    with closing(sqlite3.connect(legacy_settings.database_path)) as connection, connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"
        ).fetchone() == (53,)

    def replace_with_7f_database(files: dict[str, bytes], manifest: dict) -> None:
        _mark_as_legacy_backup(manifest)
        database_record = next(
            record for record in manifest["files"] if record["path"].startswith("database/")
        )
        content = legacy_settings.database_path.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()
        statement_paths = [
            path for path in files if path.startswith("statement_imports/")
        ]
        for statement_path in statement_paths:
            files.pop(statement_path)
        manifest["files"] = [
            record
            for record in manifest["files"]
            if not record["path"].startswith("statement_imports/")
        ]

    legacy_backup = _write_variant(
        backup_path,
        tmp_path / "supported-7f-backup.zip",
        replace_with_7f_database,
    )
    staged = stage_restore(legacy_backup)
    assert staged.marker.is_file()


def test_restore_rejects_9d_head_with_incomplete_trigger_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_path = _valid_backup()
    legacy_settings = Settings(data_root=tmp_path / "legacy-9d-missing-trigger-data")
    legacy_settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: legacy_settings)
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", legacy_settings.database_url)
    command.upgrade(alembic_config, "9d2f6a8c4b13")
    with closing(sqlite3.connect(legacy_settings.database_path)) as connection, connection:
        connection.execute("DROP TRIGGER trg_settlement_validate_finalize")

    def replace_with_incomplete_9d_database(files: dict[str, bytes], manifest: dict) -> None:
        _mark_as_legacy_backup(manifest)
        database_record = next(
            record for record in manifest["files"] if record["path"].startswith("database/")
        )
        content = legacy_settings.database_path.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    invalid_backup = _write_variant(
        backup_path,
        tmp_path / "missing-9d-trigger.zip",
        replace_with_incomplete_9d_database,
    )
    _assert_restore_rejected(invalid_backup)


def test_restore_accepts_recognizable_unversioned_legacy_database(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def remove_revision_table(files: dict[str, bytes], manifest: dict) -> None:
        _mark_as_legacy_backup(manifest)
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        database_copy = tmp_path / "unversioned-legacy.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.execute("DROP TABLE alembic_version")
            connection.commit()
        finally:
            connection.close()
        content = database_copy.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    legacy_backup = _write_variant(backup_path, tmp_path / "unversioned-legacy.zip", remove_revision_table)
    staged = stage_restore(legacy_backup)
    assert staged.marker.is_file()


def test_restore_rejects_unsafe_archive_path_before_extraction(tmp_path: Path) -> None:
    backup_path = _valid_backup()
    files = _archive_files(backup_path)
    invalid_backup = tmp_path / "unsafe-path.zip"
    with zipfile.ZipFile(invalid_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in files.items():
            archive.writestr(relative_path, content)
        archive.writestr("../escaped.txt", b"must not be extracted")

    _assert_restore_rejected(invalid_backup)
    assert not (get_settings().data_root / "tmp" / "escaped.txt").exists()


def test_restore_rejects_invalid_zip(tmp_path: Path) -> None:
    invalid_backup = tmp_path / "not-a-zip.zip"
    invalid_backup.write_bytes(b"not a zip archive")
    _assert_restore_rejected(invalid_backup)


def test_failed_stage_preserves_existing_valid_pending_restore(tmp_path: Path) -> None:
    valid_backup = _valid_backup()
    marker = stage_restore(valid_backup).marker
    original_marker = marker.read_text(encoding="utf-8")
    original_pending_root = Path(json.loads(original_marker)["path"])
    original_pending_files = sorted(path.relative_to(original_pending_root) for path in original_pending_root.rglob("*"))

    def change_hash(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["sha256"] = "f" * 64

    invalid_backup = _write_variant(valid_backup, tmp_path / "replacement-invalid.zip", change_hash)
    with pytest.raises(ValueError):
        stage_restore(invalid_backup)

    assert marker.read_text(encoding="utf-8") == original_marker
    assert original_pending_root.is_dir()
    assert sorted(path.relative_to(original_pending_root) for path in original_pending_root.rglob("*")) == original_pending_files
    validate_backup_archive_root(original_pending_root)


def test_successful_stage_replaces_marker_and_removes_previous_pending_tree() -> None:
    backup_path = _valid_backup()
    marker = stage_restore(backup_path).marker
    previous_pending_root = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])

    stage_restore(backup_path)

    current_pending_root = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])
    assert current_pending_root != previous_pending_root
    assert current_pending_root.is_dir()
    assert not previous_pending_root.exists()
    validate_backup_archive_root(current_pending_root)


def test_stage_reports_old_pending_cleanup_failure_as_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    backup_path = _valid_backup()
    first_stage = stage_restore(backup_path)
    previous_pending_root = Path(json.loads(first_stage.marker.read_text(encoding="utf-8"))["path"])
    real_cleanup = backup_service._remove_obsolete_pending_root

    def fail_cleanup(path: Path) -> None:
        assert Path(path).resolve() == previous_pending_root.resolve()
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(backup_service, "_remove_obsolete_pending_root", fail_cleanup)
    replacement = stage_restore(backup_path)

    current_pending_root = Path(json.loads(replacement.marker.read_text(encoding="utf-8"))["path"])
    assert current_pending_root != previous_pending_root
    assert current_pending_root.is_dir()
    assert previous_pending_root.is_dir()
    assert replacement.cleanup_warning is not None
    assert "新备份已安全暂存" in replacement.cleanup_warning
    validate_backup_archive_root(current_pending_root)

    monkeypatch.setattr(backup_service, "_remove_obsolete_pending_root", real_cleanup)
    engine.dispose()
    assert apply_pending_restore() is True
    assert not previous_pending_root.exists()


def test_startup_removes_unreferenced_restore_upload() -> None:
    settings = get_settings()
    upload_path = settings.data_root / "tmp" / f"restore_{'a' * 64}.zip"
    upload_path.write_bytes(b"orphaned upload")

    assert apply_pending_restore() is False
    assert not upload_path.exists()


def test_apply_revalidates_staged_backup_and_keeps_marker_on_failure() -> None:
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "apply-revalidation.txt"
    attachment.write_text("original", encoding="utf-8")
    marker = stage_restore(_valid_backup()).marker
    pending_root = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])
    staged_attachment = pending_root / "attachments" / attachment.name
    staged_attachment.write_text("tampered after staging", encoding="utf-8")

    with pytest.raises(ValueError):
        apply_pending_restore()

    assert marker.exists()
    assert pending_root.exists()
    assert attachment.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize(
    "marker_text, expected_message",
    [
        (
            '{"path":"F:/first","path":"F:/second","staged_at":"2026-08-31T12:00:00"}',
            "待恢复标记无效",
        ),
        (
            '{"path":"F:/pending_restore","staged_at":"not-a-time"}',
            "待恢复标记时间无效",
        ),
    ],
)
def test_apply_rejects_ambiguous_or_invalid_pending_marker(marker_text: str, expected_message: str) -> None:
    marker = get_settings().data_root / "pending_restore.json"
    marker.write_text(marker_text, encoding="utf-8")

    with pytest.raises(ValueError, match=expected_message):
        apply_pending_restore()


def test_apply_component_failure_rolls_back_all_live_data(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "apply-rollback.txt"
    attachment.write_text("backup attachment", encoding="utf-8")
    with SessionLocal() as db:
        existing = db.get(AppSetting, "apply_rollback")
        if existing is None:
            db.add(AppSetting(key="apply_rollback", value="backup database"))
        else:
            existing.value = "backup database"
        db.commit()
    backup_path = create_backup()

    with SessionLocal() as db:
        db.get(AppSetting, "apply_rollback").value = "live database"
        db.commit()
    attachment.write_text("live attachment", encoding="utf-8")
    marker = stage_restore(backup_path).marker
    real_activate = backup_service._activate_restore_component

    def fail_on_attachments(component: str, data_root: Path, transaction_root: Path) -> None:
        if component == "attachments":
            raise OSError("simulated component switch failure")
        real_activate(component, data_root, transaction_root)

    monkeypatch.setattr(backup_service, "_activate_restore_component", fail_on_attachments)
    engine.dispose()
    with pytest.raises(OSError, match="simulated component switch failure"):
        apply_pending_restore()
    engine.dispose()

    with SessionLocal() as db:
        assert db.get(AppSetting, "apply_rollback").value == "live database"
    assert attachment.read_text(encoding="utf-8") == "live attachment"
    assert marker.exists()
    assert not list((settings.data_root / "tmp").glob("restore_apply_*"))


@pytest.mark.parametrize("interrupt_after_install", [False, True])
def test_apply_recovers_interrupted_atomic_replace_before_retry(
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after_install: bool,
) -> None:
    class SimulatedPowerLoss(BaseException):
        pass

    init_db()
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "apply-interrupted.txt"
    attachment.write_text("backup attachment", encoding="utf-8")
    with SessionLocal() as db:
        existing = db.get(AppSetting, "apply_interrupted")
        if existing is None:
            db.add(AppSetting(key="apply_interrupted", value="backup database"))
        else:
            existing.value = "backup database"
        db.commit()
    backup_path = create_backup()

    with SessionLocal() as db:
        db.get(AppSetting, "apply_interrupted").value = "live database"
        db.commit()
    attachment.write_text("live attachment", encoding="utf-8")
    stage_restore(backup_path)
    real_replace = backup_service._replace_restore_directory
    replace_count = 0

    def interrupt_second_replace(source: Path, destination: Path) -> None:
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2 and not interrupt_after_install:
            raise SimulatedPowerLoss()
        real_replace(source, destination)
        if replace_count == 2 and interrupt_after_install:
            raise SimulatedPowerLoss()

    monkeypatch.setattr(backup_service, "_replace_restore_directory", interrupt_second_replace)
    engine.dispose()
    with pytest.raises(SimulatedPowerLoss):
        apply_pending_restore()
    assert list((settings.data_root / "tmp").glob("restore_apply_*"))

    monkeypatch.setattr(backup_service, "_replace_restore_directory", real_replace)
    assert apply_pending_restore() is True
    engine.dispose()

    with SessionLocal() as db:
        assert db.get(AppSetting, "apply_interrupted").value == "backup database"
    assert attachment.read_text(encoding="utf-8") == "backup attachment"
    assert not (settings.data_root / "pending_restore.json").exists()
    assert not list((settings.data_root / "tmp").glob("restore_apply_*"))


def test_committed_cleanup_failure_preserves_and_applies_later_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    settings = get_settings()
    with SessionLocal() as db:
        existing = db.get(AppSetting, "committed_cleanup")
        if existing is None:
            db.add(AppSetting(key="committed_cleanup", value="backup database"))
        else:
            existing.value = "backup database"
        db.commit()
    backup_path = create_backup()
    with SessionLocal() as db:
        db.get(AppSetting, "committed_cleanup").value = "live database"
        db.commit()
    stage_restore(backup_path)

    real_remove = backup_service._remove_restore_transaction_root
    removal_attempts = 0

    def fail_first_committed_cleanup(transaction_root: Path) -> None:
        nonlocal removal_attempts
        removal_attempts += 1
        if removal_attempts == 1:
            raise OSError("simulated committed cleanup failure")
        real_remove(transaction_root)

    monkeypatch.setattr(backup_service, "_remove_restore_transaction_root", fail_first_committed_cleanup)
    engine.dispose()
    with pytest.warns(RuntimeWarning, match="已完整恢复"):
        assert apply_pending_restore() is True
    assert not (settings.data_root / "pending_restore.json").exists()
    assert list((settings.data_root / "tmp").glob("restore_apply_*"))

    with SessionLocal() as db:
        db.get(AppSetting, "committed_cleanup").value = "later backup database"
        db.commit()
    later_backup_path = create_backup()
    later_stage = stage_restore(later_backup_path)
    assert later_stage.marker.exists()

    engine.dispose()
    assert apply_pending_restore() is True
    engine.dispose()
    with SessionLocal() as db:
        assert db.get(AppSetting, "committed_cleanup").value == "later backup database"
    assert not (settings.data_root / "pending_restore.json").exists()
    assert not list((settings.data_root / "tmp").glob("restore_apply_*"))
