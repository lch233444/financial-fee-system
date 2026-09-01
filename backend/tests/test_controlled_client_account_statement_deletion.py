from __future__ import annotations

import hashlib
import json
import sqlite3
import stat as stat_module
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import (
    Attachment,
    AuditEvent,
    BalanceSnapshot,
    Client,
    ExportRecord,
    Invoice,
    QuarterlySettlement,
    SettlementAccountLine,
    StatementImport,
    SubAccount,
    TransactionRecord,
)
from app.routes import master, statements
from app.schemas import StatementDeleteRequest
from app.services.entity_ids import allocate_entity_id
from app.services.statement_delete_recovery import reconcile_statement_delete_pending_files


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _suffix() -> str:
    return uuid4().hex[:10].upper()


def _empty_parsed_statement() -> SimpleNamespace:
    return SimpleNamespace(
        account_number=None,
        as_of_date=None,
        total_balance=None,
        document_type="empf_account_page",
        warnings=[],
        raw_text="",
        confidence={},
        extracted_dict=lambda: {},
    )


def _create_hierarchy(http: TestClient, suffix: str) -> tuple[dict, dict, dict, dict, dict, dict]:
    company = http.post(
        "/api/companies",
        json={"name": f"Controlled Delete Company {suffix}", "code": f"C{suffix}"},
    ).json()
    fc = http.post(
        "/api/fcs",
        json={
            "company_id": company["id"],
            "name": f"Controlled Delete FC {suffix}",
            "code": f"F{suffix}",
        },
    ).json()
    platform = http.post(
        "/api/platforms",
        json={"name": f"Controlled Delete Platform {suffix}", "code": f"P{suffix}"},
    ).json()
    plan = http.post(
        "/api/fee-plans",
        json={
            "company_id": company["id"],
            "name": f"Controlled Delete Plan {suffix}",
            "code": f"FP{suffix}",
            "fee_rate_percent": "20.00",
        },
    ).json()
    client = http.post(
        "/api/clients",
        json={
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"Controlled Delete Client {suffix}",
            "management_start_date": "2026-01-01",
            "status": "ACTIVE",
        },
    ).json()
    account = http.post(
        "/api/accounts",
        json={
            "client_id": client["id"],
            "platform_id": platform["id"],
            "fee_plan_id": plan["id"],
            "account_number": f"ACC-{suffix}",
            "start_date": "2026-01-01",
            "status": "ACTIVE",
        },
    ).json()
    return company, fc, platform, plan, client, account


def _create_confirmed_statement(
    *,
    account_id: int,
    suffix: str,
    as_of_date: date = date(2026, 5, 20),
    create_dependent: bool = False,
) -> tuple[int, int, Path, int | None]:
    source = get_settings().data_root / "statement_imports" / f"confirmed-delete-{suffix}.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    content = b"\xff\xd8\xffcontrolled confirmed statement " + suffix.encode("ascii")
    source.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()

    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        statement = StatementImport(
            id=allocate_entity_id(db, StatementImport),
            original_name=source.name,
            stored_path=str(source),
            sha256=digest,
            mime_type="image/jpeg",
            parser_name="TEST",
            parser_version="1.1",
            status="CONFIRMED",
            extracted_json={"document_type": "empf_account_page"},
            reviewed_json={"document_type": "empf_account_page"},
            confirmed_account_id=account_id,
            confirmed_at=datetime.now(timezone.utc),
        )
        db.add(statement)
        db.flush()
        snapshot = BalanceSnapshot(
            id=allocate_entity_id(db, BalanceSnapshot),
            account_id=account_id,
            as_of_date=as_of_date,
            total_balance_cents=100_00,
            currency="HKD",
            source_type="STATEMENT_IMPORT",
            statement_import_id=statement.id,
            holdings_json=[],
            eligible_for_closing=False,
        )
        db.add(snapshot)
        db.flush()
        statement.confirmed_snapshot_id = snapshot.id

        dependent = None
        if create_dependent:
            dependent_source = source.with_name(f"dependent-{suffix}.jpg")
            dependent_bytes = f"dependent-{suffix}".encode()
            dependent_source.write_bytes(dependent_bytes)
            dependent = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name=dependent_source.name,
                stored_path=str(dependent_source),
                sha256=hashlib.sha256(dependent_bytes).hexdigest(),
                mime_type="image/jpeg",
                parser_name="TEST",
                parser_version="1.1",
                status="NEEDS_REVIEW",
                duplicate_of_id=statement.id,
            )
            db.add(dependent)
        db.commit()
        db.refresh(statement)
        db.refresh(snapshot)
        if dependent is not None:
            db.refresh(dependent)
        return statement.id, snapshot.id, source, dependent.id if dependent else None


def _add_draft_settlement_line(
    *,
    client_id: int,
    company_id: int,
    fc_id: int,
    platform_id: int,
    fee_plan_id: int,
    account_id: int,
    snapshot_id: int | None,
) -> int:
    with SessionLocal() as db:
        settlement = QuarterlySettlement(
            client_id=client_id,
            platform_id=platform_id,
            fee_plan_id=fee_plan_id,
            company_id=company_id,
            fc_id=fc_id,
            version_no=1,
            year=2026,
            quarter=2,
            start_date=date(2026, 3, 31),
            closing_date=date(2026, 6, 30),
            days=91,
            beginning_cents=100_00,
            contribution_cents=0,
            withdrawal_cents=0,
            net_contribution_cents=0,
            closing_cents=100_00,
            gain_loss_cents=0,
            period_rate_ppm=0,
            original_hwm_cents=100_00,
            adjusted_hwm_cents=100_00,
            watermark_difference_cents=0,
            chargeable_above_hwm_cents=0,
            service_fee_cents=0,
            next_hwm_cents=100_00,
            fee_rate_bps=2000,
            formula_version="HWM-1.0",
            calculation_mode="LEGACY_GROUP_HWM",
            status="DRAFT",
        )
        db.add(settlement)
        db.flush()
        db.add(
            SettlementAccountLine(
                settlement_id=settlement.id,
                account_id=account_id,
                start_date=date(2026, 3, 31),
                closing_date=date(2026, 6, 30),
                days=91,
                beginning_cents=100_00,
                closing_cents=100_00,
                closing_snapshot_id=snapshot_id,
                contribution_cents=0,
                withdrawal_cents=0,
                net_contribution_cents=0,
                gain_loss_cents=0,
                period_rate_ppm=0,
                original_hwm_cents=100_00,
                adjusted_hwm_cents=100_00,
                watermark_difference_cents=0,
                chargeable_above_hwm_cents=0,
                service_fee_cents=0,
                next_hwm_cents=100_00,
                fee_rate_bps=2000,
                formula_version="HWM-1.0",
            )
        )
        db.commit()
        return settlement.id


def test_leaf_account_then_client_delete_is_non_cascading_and_audited() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, client, account = _create_hierarchy(http, suffix)
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            db.add_all(
                [
                    AuditEvent(action="TEST_HISTORY", entity_type="ACCOUNT", entity_id=account["id"]),
                    AuditEvent(action="TEST_HISTORY", entity_type="CLIENT", entity_id=client["id"]),
                ]
            )
            db.commit()

        blocked_client = http.delete(f"/api/clients/{client['id']}")
        assert blocked_client.status_code == 409
        assert "Sub Account" in blocked_client.json()["detail"]

        deleted_account = http.delete(f"/api/accounts/{account['id']}")
        assert deleted_account.status_code == 200, deleted_account.text
        assert deleted_account.json() == {"status": "deleted", "id": account["id"]}
        assert http.delete(f"/api/accounts/{account['id']}").status_code == 404

        deleted_client = http.delete(f"/api/clients/{client['id']}")
        assert deleted_client.status_code == 200, deleted_client.text
        assert deleted_client.json() == {"status": "deleted", "id": client["id"]}
        assert http.delete(f"/api/clients/{client['id']}").status_code == 404

    with SessionLocal() as db:
        assert db.get(SubAccount, account["id"]) is None
        assert db.get(Client, client["id"]) is None
        audits = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.action == "MASTER_DATA_DELETED",
                AuditEvent.entity_id.is_(None),
            )
            .order_by(AuditEvent.id.desc())
            .limit(2)
        ).all()
        assert {event.details_json["deleted_entity_type"] for event in audits} == {
            "CLIENT",
            "SUB_ACCOUNT",
        }


def test_account_delete_reconciles_commit_then_sqlalchemy_error_as_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)

    with SessionLocal() as db:
        original_commit = db.commit

        def commit_then_raise() -> None:
            original_commit()
            raise SQLAlchemyError("forced exception after committed account delete")

        monkeypatch.setattr(db, "commit", commit_then_raise)
        result = master.delete_account(account["id"], db)

    assert result == {"status": "deleted", "id": account["id"]}
    with SessionLocal() as db:
        assert db.get(SubAccount, account["id"]) is None
        audits = [
            audit
            for audit in db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "MASTER_DATA_DELETED",
                    AuditEvent.entity_type == "MASTER_DATA",
                    AuditEvent.entity_id.is_(None),
                )
            ).all()
            if audit.details_json.get("deleted_entity_type") == "SUB_ACCOUNT"
            and audit.details_json.get("deleted_entity_id") == account["id"]
        ]
        assert len(audits) == 1


def test_client_delete_reconciles_commit_then_operational_error_as_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, client, account = _create_hierarchy(http, suffix)
        assert http.delete(f"/api/accounts/{account['id']}").status_code == 200

    with SessionLocal() as db:
        original_commit = db.commit

        def commit_then_raise() -> None:
            original_commit()
            raise OperationalError(
                "COMMIT",
                {},
                sqlite3.OperationalError("forced exception after committed client delete"),
            )

        monkeypatch.setattr(db, "commit", commit_then_raise)
        result = master.delete_client(client["id"], db)

    assert result == {"status": "deleted", "id": client["id"]}
    with SessionLocal() as db:
        assert db.get(Client, client["id"]) is None
        audits = [
            audit
            for audit in db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "MASTER_DATA_DELETED",
                    AuditEvent.entity_type == "MASTER_DATA",
                    AuditEvent.entity_id.is_(None),
                )
            ).all()
            if audit.details_json.get("deleted_entity_type") == "CLIENT"
            and audit.details_json.get("deleted_entity_id") == client["id"]
        ]
        assert len(audits) == 1


def test_account_and_client_delete_report_every_business_and_file_reference() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        company, fc, platform, plan, client, account = _create_hierarchy(http, suffix)
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            transaction = TransactionRecord(
                account_id=account["id"],
                transaction_date=date(2026, 4, 1),
                transaction_type="CONTRIBUTION",
                amount_cents=10_00,
            )
            snapshot = BalanceSnapshot(
                id=allocate_entity_id(db, BalanceSnapshot),
                account_id=account["id"],
                as_of_date=date(2026, 3, 31),
                total_balance_cents=100_00,
                currency="HKD",
                source_type="MANUAL",
                eligible_for_closing=False,
            )
            statement_source = (
                get_settings().data_root / "statement_imports" / f"reference-{suffix}.jpg"
            )
            statement_bytes = f"reference-{suffix}".encode()
            statement_source.write_bytes(statement_bytes)
            statement = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name=f"reference-{suffix}.jpg",
                stored_path=str(statement_source),
                sha256=hashlib.sha256(statement_bytes).hexdigest(),
                mime_type="image/jpeg",
                parser_name="TEST",
                parser_version="1.1",
                status="CONFIRMED",
                confirmed_account_id=account["id"],
            )
            db.add_all([transaction, snapshot, statement])
            db.flush()
            db.add_all(
                [
                    Attachment(
                        entity_type="sub-account",
                        entity_id=account["id"],
                        original_name="account.pdf",
                        stored_path=f"F:/synthetic/account-{suffix}.pdf",
                        sha256=hashlib.sha256(f"account-{suffix}".encode()).hexdigest(),
                        size_bytes=1,
                    ),
                    ExportRecord(
                        export_type="TEST",
                        entity_type="ACCOUNT",
                        entity_id=account["id"],
                        stored_path=f"F:/synthetic/account-export-{suffix}.xlsx",
                        sha256=hashlib.sha256(f"account-export-{suffix}".encode()).hexdigest(),
                    ),
                    Attachment(
                        entity_type="CLIENT",
                        entity_id=client["id"],
                        original_name="client.pdf",
                        stored_path=f"F:/synthetic/client-{suffix}.pdf",
                        sha256=hashlib.sha256(f"client-{suffix}".encode()).hexdigest(),
                        size_bytes=1,
                    ),
                    ExportRecord(
                        export_type="TEST",
                        entity_type="client",
                        entity_id=client["id"],
                        stored_path=f"F:/synthetic/client-export-{suffix}.xlsx",
                        sha256=hashlib.sha256(f"client-export-{suffix}".encode()).hexdigest(),
                    ),
                ]
            )
            db.commit()

        settlement_id = _add_draft_settlement_line(
            client_id=client["id"],
            company_id=company["id"],
            fc_id=fc["id"],
            platform_id=platform["id"],
            fee_plan_id=plan["id"],
            account_id=account["id"],
            snapshot_id=None,
        )
        with SessionLocal() as db:
            db.add(
                Invoice(
                    settlement_id=settlement_id,
                    client_id=client["id"],
                    year=2026,
                    quarter=2,
                    fee_plan_id=plan["id"],
                    company_id=company["id"],
                    fc_id=fc["id"],
                    lifecycle_status="DRAFT",
                    amount_cents=0,
                    language="zh",
                )
            )
            db.commit()

        account_response = http.delete(f"/api/accounts/{account['id']}")
        assert account_response.status_code == 409
        account_detail = account_response.json()["detail"]
        for label in (
            "资金流水",
            "余额快照",
            "已确认账单导入",
            "Settlement账户明细",
            "附件记录",
            "导出记录",
        ):
            assert label in account_detail

        client_response = http.delete(f"/api/clients/{client['id']}")
        assert client_response.status_code == 409
        client_detail = client_response.json()["detail"]
        for label in ("Sub Account", "Settlement", "Invoice", "附件记录", "导出记录"):
            assert label in client_detail

    with SessionLocal() as db:
        assert db.get(SubAccount, account["id"]) is not None
        assert db.get(Client, client["id"]) is not None


def test_confirmed_statement_delete_requires_reason_and_removes_only_its_snapshot() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        synthetic_holdings = [
            {
                "fund_name": "Synthetic Alpha Fund",
                "balance": "100.00",
                "allocation_percent": "100.00",
            }
        ]
        with SessionLocal() as db:
            db.get(BalanceSnapshot, snapshot_id).holdings_json = synthetic_holdings
            db.commit()

        missing_reason = http.delete(f"/api/statement-imports/{import_id}")
        assert missing_reason.status_code == 422
        assert source.is_file()

        short_reason = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": " x "},
        )
        assert short_reason.status_code == 422
        assert source.is_file()

        deleted = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试误确认入账"},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {
            "deleted": True,
            "import_id": import_id,
            "source_file_deleted": True,
            "snapshot_id": snapshot_id,
        }
        assert not source.exists()
        assert not list(source.parent.glob(f".{source.name}.*.delete-pending"))

        assert http.delete(f"/api/accounts/{account['id']}").status_code == 200
        assert http.delete(f"/api/clients/{client['id']}").status_code == 200

    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is None
        assert db.get(BalanceSnapshot, snapshot_id) is None
        audit = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action
                == "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED",
                AuditEvent.entity_id.is_(None),
            )
            .order_by(AuditEvent.id.desc())
        )
        assert audit is not None
        assert audit.details_json["deleted_import_id"] == import_id
        assert audit.details_json["deleted_snapshot_id"] == snapshot_id
        assert audit.details_json["confirmed_account_id"] == account["id"]
        assert audit.details_json["reason"] == "测试误确认入账"
        assert audit.details_json["original_name"] == source.name
        assert audit.details_json["snapshot_as_of_date"] == "2026-05-20"
        assert audit.details_json["total_balance_cents"] == 100_00
        assert audit.details_json["currency"] == "HKD"
        assert audit.details_json["eligible_for_closing"] is False
        assert audit.details_json["holdings_count"] == 1
        canonical_holdings = json.dumps(
            synthetic_holdings,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        assert audit.details_json["holdings_json_sha256"] == hashlib.sha256(
            canonical_holdings
        ).hexdigest()
        assert "Synthetic Alpha Fund" not in json.dumps(
            audit.details_json,
            ensure_ascii=False,
        )
        assert "warnings_json" not in audit.details_json
        assert "cleared_duplicate_import_ids" not in audit.details_json
        assert "cleared_duplicate_import_count" not in audit.details_json


def test_confirmed_statement_delete_rejects_dependent_duplicate_record() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
            create_dependent=True,
        )
        response = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试后继重复记录保护"},
        )

    assert response.status_code == 409
    assert f"后继重复记录 #{dependent_id}" in response.json()["detail"]
    assert source.is_file()
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None
        assert db.get(StatementImport, dependent_id).duplicate_of_id == import_id


@pytest.mark.parametrize(
    "relationship_damage",
    [
        "missing_linked_snapshot",
        "multiple_linked_snapshots",
        "wrong_confirmed_snapshot",
        "wrong_account",
        "wrong_source_type",
        "missing_confirmed_at",
    ],
)
def test_confirmed_statement_delete_rejects_inconsistent_snapshot_relationship(
    relationship_damage: str,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, platform, plan, client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        other_account_id: int | None = None
        if relationship_damage == "wrong_account":
            other_account = http.post(
                "/api/accounts",
                json={
                    "client_id": client["id"],
                    "platform_id": platform["id"],
                    "fee_plan_id": plan["id"],
                    "account_number": f"ACC-OTHER-{suffix}",
                    "start_date": "2026-01-01",
                    "status": "ACTIVE",
                },
            ).json()
            other_account_id = other_account["id"]
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            statement = db.get(StatementImport, import_id)
            snapshot = db.get(BalanceSnapshot, snapshot_id)
            if relationship_damage == "missing_linked_snapshot":
                snapshot.statement_import_id = None
            elif relationship_damage == "multiple_linked_snapshots":
                db.add(
                    BalanceSnapshot(
                        id=allocate_entity_id(db, BalanceSnapshot),
                        account_id=account["id"],
                        as_of_date=date(2026, 5, 22),
                        total_balance_cents=100_00,
                        currency="HKD",
                        source_type="STATEMENT_IMPORT",
                        statement_import_id=import_id,
                        holdings_json=[],
                        eligible_for_closing=False,
                    )
                )
            elif relationship_damage == "wrong_confirmed_snapshot":
                statement.confirmed_snapshot_id = snapshot_id + 1_000_000
            elif relationship_damage == "wrong_account":
                statement.confirmed_account_id = other_account_id
            elif relationship_damage == "wrong_source_type":
                snapshot.source_type = "MANUAL"
            else:
                statement.confirmed_at = None
            db.commit()

        response = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试确认关系异常保护"},
        )

    assert response.status_code == 409
    assert "关系" in response.json()["detail"]
    assert source.is_file()
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None


@pytest.mark.parametrize(
    "abnormal_state",
    ["unknown_status", "confirmed_account", "confirmed_snapshot", "confirmed_at"],
)
def test_unconfirmed_statement_delete_rejects_unknown_state_or_confirmation_trace(
    abnormal_state: str,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        source = get_settings().data_root / "statement_imports" / f"abnormal-{suffix}.jpg"
        content = b"\xff\xd8\xffsynthetic abnormal statement " + suffix.encode("ascii")
        source.write_bytes(content)
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            item = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name=source.name,
                stored_path=str(source),
                sha256=hashlib.sha256(content).hexdigest(),
                mime_type="image/jpeg",
                parser_name="TEST",
                parser_version="1.1",
                status="UNKNOWN" if abnormal_state == "unknown_status" else "NEEDS_REVIEW",
                confirmed_account_id=(
                    account["id"] if abnormal_state == "confirmed_account" else None
                ),
                confirmed_snapshot_id=(
                    999_999 if abnormal_state == "confirmed_snapshot" else None
                ),
                confirmed_at=(
                    datetime.now(timezone.utc) if abnormal_state == "confirmed_at" else None
                ),
            )
            db.add(item)
            db.commit()
            import_id = item.id

        response = http.delete(f"/api/statement-imports/{import_id}")
        account_delete = (
            http.delete(f"/api/accounts/{account['id']}")
            if abnormal_state == "confirmed_account"
            else None
        )

    assert response.status_code == 409
    assert "状态或确认痕迹异常" in response.json()["detail"]
    if account_delete is not None:
        assert account_delete.status_code == 409
        assert "已确认账单导入" in account_delete.json()["detail"]
    assert source.is_file()
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None


def test_confirmed_statement_delete_rejects_any_settlement_line_reference() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        company, fc, platform, plan, client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        settlement_id = _add_draft_settlement_line(
            client_id=client["id"],
            company_id=company["id"],
            fc_id=fc["id"],
            platform_id=platform["id"],
            fee_plan_id=plan["id"],
            account_id=account["id"],
            snapshot_id=snapshot_id,
        )

        response = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试误确认入账"},
        )
        assert response.status_code == 409
        assert f"Settlement #{settlement_id}" in response.json()["detail"]
        assert source.is_file()

    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None


def test_confirmed_statement_delete_rejects_snapshot_or_import_files_and_exports() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        with SessionLocal() as db:
            db.add_all(
                [
                    Attachment(
                        entity_type="snapshot",
                        entity_id=snapshot_id,
                        original_name="snapshot.pdf",
                        stored_path=f"F:/synthetic/snapshot-{suffix}.pdf",
                        sha256=hashlib.sha256(f"snapshot-{suffix}".encode()).hexdigest(),
                        size_bytes=1,
                    ),
                    ExportRecord(
                        export_type="TEST",
                        entity_type="statement-import",
                        entity_id=import_id,
                        stored_path=f"F:/synthetic/statement-export-{suffix}.xlsx",
                        sha256=hashlib.sha256(f"statement-export-{suffix}".encode()).hexdigest(),
                    ),
                ]
            )
            db.commit()

        response = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试误确认入账"},
        )
        assert response.status_code == 409
        assert "附件记录" in response.json()["detail"]
        assert "导出记录" in response.json()["detail"]
        assert source.is_file()

    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None


def test_confirmed_statement_delete_rejects_missing_or_tampered_source() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        missing_id, missing_snapshot_id, missing_source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        missing_source.unlink()
        missing = http.request(
            "DELETE",
            f"/api/statement-imports/{missing_id}",
            json={"reason": "测试误确认入账"},
        )
        assert missing.status_code == 409
        assert "原件不存在" in missing.json()["detail"]

        tampered_suffix = _suffix()
        tampered_id, tampered_snapshot_id, tampered_source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=tampered_suffix,
            as_of_date=date(2026, 5, 21),
        )
        tampered_source.write_bytes(b"tampered")
        tampered = http.request(
            "DELETE",
            f"/api/statement-imports/{tampered_id}",
            json={"reason": "测试误确认入账"},
        )
        assert tampered.status_code == 409
        assert "SHA-256不一致" in tampered.json()["detail"]

    with SessionLocal() as db:
        assert db.get(StatementImport, missing_id) is not None
        assert db.get(BalanceSnapshot, missing_snapshot_id) is not None
        assert db.get(StatementImport, tampered_id) is not None
        assert db.get(BalanceSnapshot, tampered_snapshot_id) is not None
    missing_source.write_bytes(
        b"\xff\xd8\xffcontrolled confirmed statement " + suffix.encode("ascii")
    )
    tampered_source.write_bytes(
        b"\xff\xd8\xffcontrolled confirmed statement "
        + tampered_suffix.encode("ascii")
    )


def test_unconfirmed_statement_delete_rejects_tampered_existing_source() -> None:
    suffix = _suffix()
    source = get_settings().data_root / "statement_imports" / f"unconfirmed-{suffix}.jpg"
    original_content = b"\xff\xd8\xffsynthetic unconfirmed source " + suffix.encode("ascii")
    source.write_bytes(original_content)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        item = StatementImport(
            id=allocate_entity_id(db, StatementImport),
            original_name=source.name,
            stored_path=str(source),
            sha256=hashlib.sha256(original_content).hexdigest(),
            mime_type="image/jpeg",
            parser_name="TEST",
            parser_version="1.1",
            status="NEEDS_REVIEW",
        )
        db.add(item)
        db.commit()
        import_id = item.id
    source.write_bytes(b"tampered unconfirmed source")

    with TestClient(app, headers=WRITE_HEADERS) as http:
        response = http.delete(f"/api/statement-imports/{import_id}")

    assert response.status_code == 409
    assert "SHA-256不一致" in response.json()["detail"]
    assert source.read_bytes() == b"tampered unconfirmed source"
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None

    source.write_bytes(original_content)


def test_confirmed_statement_delete_rejects_symlink_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        target = source.with_name(f"symlink-target-{suffix}.jpg")
        source.replace(target)
        real_symlink = True
        try:
            source.symlink_to(target.name)
        except OSError:
            target.replace(source)
            real_symlink = False
            original_lstat = Path.lstat

            def emulate_symlink_lstat(path: Path):
                result = original_lstat(path)
                if path == source:
                    return SimpleNamespace(
                        st_mode=stat_module.S_IFLNK | 0o777,
                        st_size=result.st_size,
                    )
                return result

            monkeypatch.setattr(Path, "lstat", emulate_symlink_lstat)

        response = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试符号链接原件保护"},
        )

    assert response.status_code == 409
    assert "不是普通文件" in response.json()["detail"]
    assert source.exists()
    if real_symlink:
        assert source.is_symlink()
        assert target.is_file()
    else:
        assert source.is_file()
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None
    if real_symlink:
        source.unlink()
        target.replace(source)


def test_confirmed_statement_delete_rejects_hardlink_source_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        alias = source.with_name(f"hardlink-alias-{suffix}.jpg")
        real_hardlink = True
        try:
            alias.hardlink_to(source)
        except OSError:
            real_hardlink = False
            alias.write_bytes(source.read_bytes())
            original_samefile = Path.samefile

            def emulate_samefile(path: Path, other_path: Path) -> bool:
                if path == source and Path(other_path) == alias:
                    return True
                return original_samefile(path, other_path)

            monkeypatch.setattr(Path, "samefile", emulate_samefile)

        with SessionLocal() as db:
            alias_record = StatementImport(
                original_name=alias.name,
                stored_path=str(alias),
                sha256="f" * 64,
                mime_type="image/jpeg",
                parser_name="TEST",
                parser_version="1.1",
                status="NEEDS_REVIEW",
            )
            db.add(alias_record)
            db.commit()
            alias_record_id = alias_record.id

        response = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试硬链接别名保护"},
        )

    assert response.status_code == 409
    assert f"记录 #{alias_record_id}" in response.json()["detail"]
    assert source.is_file()
    assert alias.is_file()
    if real_hardlink:
        assert source.samefile(alias)
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None
        db.execute(delete(StatementImport).where(StatementImport.id == alias_record_id))
        db.commit()
    alias.unlink()


def test_confirmed_statement_delete_restores_source_when_database_commit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
    import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
        account_id=account["id"],
        suffix=suffix,
    )

    with SessionLocal() as db:
        def fail_commit() -> None:
            raise IntegrityError("forced commit failure", {}, sqlite3.IntegrityError("forced"))

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(HTTPException) as exc_info:
            statements.delete_statement_import(
                import_id,
                StatementDeleteRequest(reason="测试提交失败恢复"),
                db,
            )

    assert exc_info.value.status_code == 409
    assert source.is_file()
    assert not list(source.parent.glob(f".{source.name}.*.delete-pending"))
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None


def test_confirmed_statement_delete_stops_if_staged_source_changes_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
    import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
        account_id=account["id"],
        suffix=suffix,
    )
    original_content = source.read_bytes()
    real_stage = statements._stage_source_file

    def stage_then_tamper(source_path: Path | None, staged_path: Path | None) -> None:
        real_stage(source_path, staged_path)
        assert staged_path is not None
        staged_path.write_bytes(b"tampered after atomic stage")

    monkeypatch.setattr(statements, "_stage_source_file", stage_then_tamper)
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc_info:
            statements.delete_statement_import(
                import_id,
                StatementDeleteRequest(reason="测试暂存后文件变化"),
                db,
            )

    assert exc_info.value.status_code == 500
    assert "原件自动恢复失败" in exc_info.value.detail
    assert not source.exists()
    pending_files = list(source.parent.glob(f".{source.name}.*.delete-pending"))
    assert len(pending_files) == 1
    assert pending_files[0].read_bytes() == b"tampered after atomic stage"
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None

    pending_files[0].unlink()
    source.write_bytes(original_content)


def test_upload_waits_for_delete_critical_section_before_recreating_same_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"\xff\xd8\xffsynthetic upload delete serialization " + _suffix().encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    source = get_settings().data_root / "statement_imports" / f"{digest}.jpg"
    source.write_bytes(data)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        item = StatementImport(
            id=allocate_entity_id(db, StatementImport),
            original_name="serialized-delete.jpg",
            stored_path=str(source),
            sha256=digest,
            mime_type="image/jpeg",
            parser_name="TEST",
            parser_version="1.1",
            status="NEEDS_REVIEW",
        )
        db.add(item)
        db.commit()
        import_id = item.id

    delete_staged = threading.Event()
    allow_delete_to_finish = threading.Event()
    upload_waiting_for_lock = threading.Event()
    upload_store_entered = threading.Event()
    real_stage = statements._stage_source_file
    real_store = statements.store_bytes

    def stage_and_pause(source_path: Path | None, staged_path: Path | None) -> None:
        real_stage(source_path, staged_path)
        delete_staged.set()
        if not allow_delete_to_finish.wait(timeout=5):
            raise RuntimeError("test did not release staged delete")

    def observe_store_bytes(**kwargs):
        upload_store_entered.set()
        return real_store(**kwargs)

    parsed = SimpleNamespace(
        account_number=None,
        as_of_date=None,
        total_balance=None,
        document_type="empf_account_page",
        warnings=[],
        raw_text="",
        confidence={},
        extracted_dict=lambda: {},
    )

    class ObservableLock:
        def __init__(self) -> None:
            self._lock = threading.Lock()

        def __enter__(self):
            if self._lock.locked():
                upload_waiting_for_lock.set()
            self._lock.acquire()
            return self

        def __exit__(self, _exc_type, _exc, _traceback) -> None:
            self._lock.release()

    monkeypatch.setattr(statements, "_stage_source_file", stage_and_pause)
    monkeypatch.setattr(statements, "store_bytes", observe_store_bytes)
    monkeypatch.setattr(statements, "parse_empf_statement", lambda _path: parsed)
    monkeypatch.setattr(statements, "_STATEMENT_RECOGNITION_LOCK", ObservableLock())

    results: dict[str, dict] = {}
    errors: list[BaseException] = []

    def delete_worker() -> None:
        try:
            with SessionLocal() as db:
                results["delete"] = statements.delete_statement_import(import_id, None, db)
        except BaseException as exc:
            errors.append(exc)

    def upload_worker() -> None:
        try:
            response = http.post(
                "/api/statement-imports",
                files={"file": ("serialized-upload.jpg", data, "image/jpeg")},
            )
            assert response.status_code == 201, response.text
            results["upload"] = response.json()
        except BaseException as exc:
            errors.append(exc)

    with TestClient(app, headers=WRITE_HEADERS) as http:
        delete_thread = threading.Thread(target=delete_worker)
        upload_thread = threading.Thread(target=upload_worker)
        delete_thread.start()
        assert delete_staged.wait(timeout=5)
        upload_thread.start()
        assert upload_waiting_for_lock.wait(timeout=5)
        try:
            assert not upload_store_entered.is_set()
            health_started = time.perf_counter()
            health = http.get("/api/health")
            health_elapsed = time.perf_counter() - health_started
            assert health.status_code == 200
            assert health_elapsed < 1.5
        finally:
            allow_delete_to_finish.set()
        delete_thread.join(timeout=5)
        upload_thread.join(timeout=5)

    assert not delete_thread.is_alive()
    assert not upload_thread.is_alive()
    assert errors == []
    assert results["delete"]["deleted"] is True
    assert results["upload"]["duplicate"] is False
    assert source.read_bytes() == data
    with SessionLocal() as db:
        replacement = db.get(StatementImport, results["upload"]["id"])
        assert replacement is not None
        assert replacement.original_name == "serialized-upload.jpg"
        deletion_audit = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action == "STATEMENT_IMPORT_DELETED",
                AuditEvent.entity_id.is_(None),
                AuditEvent.details_json["deleted_import_id"].as_integer() == import_id,
            )
            .order_by(AuditEvent.id.desc())
        )
        assert deletion_audit is not None


def test_upload_commit_then_raise_is_reconciled_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    data = b"\xff\xd8\xffsynthetic upload commit outcome " + suffix.encode("ascii")
    monkeypatch.setattr(
        statements,
        "parse_empf_statement",
        lambda _path: _empty_parsed_statement(),
    )

    with TestClient(app, headers=WRITE_HEADERS) as http, SessionLocal() as db:
        original_commit = db.commit
        commit_calls = 0

        def commit_then_raise() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()
            if commit_calls == 1:
                raise SQLAlchemyError("forced exception after committed upload")

        monkeypatch.setattr(db, "commit", commit_then_raise)
        result = statements._store_parse_and_create_statement(
            data=data,
            detected_suffix=".jpg",
            detected_mime="image/jpeg",
            original_name=f"upload-commit-{suffix}.jpg",
            db=db,
        )

        assert result["duplicate"] is False
        assert result.get("upload_recovery_pending") is None
        source = Path(db.get(StatementImport, result["id"]).stored_path)
        assert source.read_bytes() == data
        assert not list(source.parent.glob(f".{source.name}.*.upload-pending"))
        assert http.delete(f"/api/statement-imports/{result['id']}").status_code == 200


def test_upload_rejects_source_changed_by_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    data = b"\xff\xd8\xffsynthetic parser mutation " + suffix.encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    source = get_settings().data_root / "statement_imports" / f"{digest}.jpg"

    def mutate_source(path: Path) -> SimpleNamespace:
        path.write_bytes(b"tampered while parser was running")
        return _empty_parsed_statement()

    monkeypatch.setattr(statements, "parse_empf_statement", mutate_source)

    with TestClient(app, headers=WRITE_HEADERS) as http:
        response = http.post(
            "/api/statement-imports",
            files={"file": (f"parser-mutation-{suffix}.jpg", data, "image/jpeg")},
        )

    assert response.status_code == 500
    assert "安全对账标记" in response.json()["detail"]
    assert source.read_bytes() == b"tampered while parser was running"
    markers = list(source.parent.glob(f".{source.name}.*.upload-pending"))
    assert len(markers) == 1
    with SessionLocal() as db:
        assert db.scalar(
            select(StatementImport.id).where(StatementImport.sha256 == digest)
        ) is None

    # Restore the expected bytes so the normal startup reconciler can prove
    # this was an uncommitted upload and clean both pieces of evidence.
    source.write_bytes(data)
    assert reconcile_statement_delete_pending_files()["cleaned"] >= 1
    assert not source.exists()
    assert not markers[0].exists()


def test_upload_failure_reports_marker_cleanup_pending_and_startup_reconciles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    data = b"\xff\xd8\xffsynthetic upload marker cleanup " + suffix.encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    source = get_settings().data_root / "statement_imports" / f"{digest}.jpg"
    monkeypatch.setattr(
        statements,
        "parse_empf_statement",
        lambda _path: (_ for _ in ()).throw(RuntimeError("forced parser failure")),
    )
    monkeypatch.setattr(statements, "_remove_upload_pending_marker", lambda _marker: False)

    with TestClient(app, headers=WRITE_HEADERS) as http:
        response = http.post(
            "/api/statement-imports",
            files={"file": (f"marker-failure-{suffix}.jpg", data, "image/jpeg")},
        )

    assert response.status_code == 500
    assert "安全重启" in response.json()["detail"]
    assert not source.exists()
    markers = list(source.parent.glob(f".{source.name}.*.upload-pending"))
    assert len(markers) == 1
    with SessionLocal() as db:
        assert db.scalar(
            select(StatementImport.id).where(StatementImport.sha256 == digest)
        ) is None
    assert reconcile_statement_delete_pending_files()["cleaned"] >= 1
    assert not markers[0].exists()


def test_upload_write_lock_timeout_leaves_recoverable_pending_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    data = b"\xff\xd8\xffsynthetic upload locked cleanup " + suffix.encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    source = get_settings().data_root / "statement_imports" / f"{digest}.jpg"
    monkeypatch.setattr(
        statements,
        "parse_empf_statement",
        lambda _path: _empty_parsed_statement(),
    )

    with TestClient(app, headers=WRITE_HEADERS) as http:
        lock_connection = sqlite3.connect(
            get_settings().database_path,
            timeout=30,
            isolation_level=None,
        )
        try:
            lock_connection.execute("BEGIN IMMEDIATE")
            response = http.post(
                "/api/statement-imports",
                files={"file": (f"locked-{suffix}.jpg", data, "image/jpeg")},
            )
            assert response.status_code == 500
            assert "重启系统自动清理" in response.json()["detail"]
            assert source.is_file()
            markers = list(source.parent.glob(f".{source.name}.*.upload-pending"))
            assert len(markers) == 1
        finally:
            lock_connection.rollback()
            lock_connection.close()

    assert reconcile_statement_delete_pending_files()["cleaned"] >= 1
    assert not source.exists()
    assert not markers[0].exists()
    with SessionLocal() as db:
        assert db.scalar(
            select(StatementImport.id).where(StatementImport.sha256 == digest)
        ) is None


@pytest.mark.parametrize("source_state", ["missing", "corrupt", "canonical_missing"])
def test_duplicate_upload_rejects_untrusted_existing_source(
    monkeypatch: pytest.MonkeyPatch,
    source_state: str,
) -> None:
    suffix = _suffix()
    data = b"\xff\xd8\xffsynthetic corrupt duplicate source " + suffix.encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    statement_root = get_settings().data_root / "statement_imports"
    canonical = statement_root / f"{digest}.jpg"
    old_source = (
        canonical
        if source_state == "canonical_missing"
        else statement_root / f"legacy-duplicate-{source_state}-{suffix}.jpg"
    )
    if source_state == "corrupt":
        old_source.write_bytes(b"corrupt legacy source")
    else:
        old_source.unlink(missing_ok=True)

    monkeypatch.setattr(
        statements,
        "parse_empf_statement",
        lambda _path: _empty_parsed_statement(),
    )
    with TestClient(app, headers=WRITE_HEADERS) as http:
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            existing = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name=old_source.name,
                stored_path=str(old_source),
                sha256=digest,
                mime_type="image/jpeg",
                parser_name="TEST",
                parser_version="1.1",
                status="NEEDS_REVIEW",
            )
            db.add(existing)
            db.commit()
            existing_id = existing.id

        response = http.post(
            "/api/statement-imports",
            files={"file": (f"duplicate-{suffix}.jpg", data, "image/jpeg")},
        )
        assert response.status_code == 409
        assert "原件缺失或完整性异常" in response.json()["detail"]
        with SessionLocal() as db:
            rows = db.scalars(
                select(StatementImport).where(StatementImport.sha256 == digest)
            ).all()
            assert [row.id for row in rows] == [existing_id]

        if source_state == "canonical_missing":
            assert canonical.read_bytes() == data
        else:
            assert not canonical.exists()
            old_source.write_bytes(data)
        assert not list(statement_root.glob(f".{canonical.name}.*.upload-pending"))
        assert http.delete(f"/api/statement-imports/{existing_id}").status_code == 200


def test_duplicate_upload_accepts_valid_legacy_source_and_removes_new_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    data = b"\xff\xd8\xffsynthetic valid duplicate source " + suffix.encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    statement_root = get_settings().data_root / "statement_imports"
    canonical = statement_root / f"{digest}.jpg"
    old_source = statement_root / f"legacy-valid-duplicate-{suffix}.jpg"
    old_source.write_bytes(data)
    monkeypatch.setattr(
        statements,
        "parse_empf_statement",
        lambda _path: _empty_parsed_statement(),
    )

    with TestClient(app, headers=WRITE_HEADERS) as http:
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            existing = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name=old_source.name,
                stored_path=str(old_source),
                sha256=digest,
                mime_type="image/jpeg",
                parser_name="TEST",
                parser_version="1.1",
                status="NEEDS_REVIEW",
            )
            db.add(existing)
            db.commit()
            existing_id = existing.id

        response = http.post(
            "/api/statement-imports",
            files={"file": (f"duplicate-{suffix}.jpg", data, "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        assert response.json()["duplicate"] is True
        assert response.json()["id"] == existing_id
        assert old_source.read_bytes() == data
        assert not canonical.exists()
        assert not list(statement_root.glob(f".{canonical.name}.*.upload-pending"))
        assert http.delete(f"/api/statement-imports/{existing_id}").status_code == 200


def test_statement_delete_maps_sqlite_busy_to_stable_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with SessionLocal() as db:
        def fail_begin(*_args, **_kwargs):
            raise OperationalError(
                "BEGIN IMMEDIATE",
                {},
                sqlite3.OperationalError("database is locked"),
            )

        monkeypatch.setattr(db, "execute", fail_begin)
        with pytest.raises(HTTPException) as exc_info:
            statements.delete_statement_import(
                999_999,
                None,
                db,
            )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "数据库正在处理另一笔财务写入，请稍后重试"


def test_confirmed_statement_delete_restores_source_for_other_sqlalchemy_commit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
    import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
        account_id=account["id"],
        suffix=suffix,
    )

    with SessionLocal() as db:
        def fail_commit() -> None:
            raise SQLAlchemyError("forced non-integrity SQLAlchemy commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(SQLAlchemyError):
            statements.delete_statement_import(
                import_id,
                StatementDeleteRequest(reason="测试其他数据库异常恢复"),
                db,
            )

    assert source.is_file()
    assert not list(source.parent.glob(f".{source.name}.*.delete-pending"))
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None


def test_confirmed_statement_delete_records_pending_cleanup_when_unlink_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        original_unlink = Path.unlink

        def fail_pending_unlink(path: Path, missing_ok: bool = False) -> None:
            if path.name.endswith(".delete-pending"):
                raise PermissionError("forced pending source cleanup failure")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", fail_pending_unlink)
        response = http.request(
            "DELETE",
            f"/api/statement-imports/{import_id}",
            json={"reason": "测试原件清理失败审计"},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "deleted": True,
        "import_id": import_id,
        "source_file_deleted": False,
        "snapshot_id": snapshot_id,
        "source_file_cleanup_pending": True,
    }
    assert not source.exists()
    pending_files = list(source.parent.glob(f".{source.name}.*.delete-pending"))
    assert len(pending_files) == 1
    pending_path = pending_files[0]

    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is None
        assert db.get(BalanceSnapshot, snapshot_id) is None
        pending_audit = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action == "STATEMENT_IMPORT_SOURCE_FILE_CLEANUP_PENDING",
                AuditEvent.entity_id.is_(None),
            )
            .order_by(AuditEvent.id.desc())
        )
        assert pending_audit is not None
        assert pending_audit.details_json["deleted_import_id"] == import_id
        assert pending_audit.details_json["deleted_snapshot_id"] == snapshot_id
        assert pending_audit.details_json["staged_path"] == str(pending_path)
        assert pending_audit.details_json["sha256"] == hashlib.sha256(
            b"\xff\xd8\xffcontrolled confirmed statement " + suffix.encode("ascii")
        ).hexdigest()

        deletion_audit = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action
                == "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED",
                AuditEvent.entity_id.is_(None),
            )
            .order_by(AuditEvent.id.desc())
        )
        assert deletion_audit is not None
        assert deletion_audit.details_json["source_cleanup"]["staged_path"] == str(pending_path)

    original_unlink(pending_path)


def test_confirmed_statement_delete_reports_secondary_cleanup_audit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
    import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
        account_id=account["id"],
        suffix=suffix,
    )
    original_unlink = Path.unlink

    def fail_pending_unlink(path: Path, missing_ok: bool = False) -> None:
        if path.name.endswith(".delete-pending"):
            raise PermissionError("forced pending source cleanup failure")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_pending_unlink)
    with SessionLocal() as db:
        original_commit = db.commit
        commit_count = 0

        def fail_second_commit() -> None:
            nonlocal commit_count
            commit_count += 1
            if commit_count == 2:
                raise SQLAlchemyError("forced cleanup audit commit failure")
            original_commit()

        monkeypatch.setattr(db, "commit", fail_second_commit)
        result = statements.delete_statement_import(
            import_id,
            StatementDeleteRequest(reason="测试待清理审计失败"),
            db,
        )

    assert result == {
        "deleted": True,
        "import_id": import_id,
        "source_file_deleted": False,
        "snapshot_id": snapshot_id,
        "source_file_cleanup_pending": True,
        "cleanup_audit_failed": True,
    }
    assert not source.exists()
    pending_files = list(source.parent.glob(f".{source.name}.*.delete-pending"))
    assert len(pending_files) == 1
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is None
        assert db.get(BalanceSnapshot, snapshot_id) is None
        assert db.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "STATEMENT_IMPORT_SOURCE_FILE_CLEANUP_PENDING",
                    AuditEvent.details_json["deleted_import_id"].as_integer() == import_id,
                    AuditEvent.details_json["staged_path"].as_string()
                    == str(pending_files[0]),
                )
        ) is None
    original_unlink(pending_files[0])


def test_confirmed_statement_delete_restores_source_even_when_rollback_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
    import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
        account_id=account["id"],
        suffix=suffix,
    )

    with SessionLocal() as db:
        original_rollback = db.rollback

        def fail_commit() -> None:
            raise SQLAlchemyError("forced commit failure before rollback failure")

        def rollback_then_raise() -> None:
            original_rollback()
            raise SQLAlchemyError("forced rollback failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        monkeypatch.setattr(db, "rollback", rollback_then_raise)
        with pytest.raises(SQLAlchemyError, match="forced rollback failure"):
            statements.delete_statement_import(
                import_id,
                StatementDeleteRequest(reason="测试回滚异常仍恢复原件"),
                db,
            )

    assert source.is_file()
    assert not list(source.parent.glob(f".{source.name}.*.delete-pending"))
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is not None
        assert db.get(BalanceSnapshot, snapshot_id) is not None


def test_confirmed_statement_delete_reconciles_commit_then_raise_as_committed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
    import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
        account_id=account["id"],
        suffix=suffix,
    )

    with SessionLocal() as db:
        original_commit = db.commit
        commit_calls = 0

        def commit_then_raise() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()
            if commit_calls == 1:
                raise SQLAlchemyError("forced exception after committed delete")

        monkeypatch.setattr(db, "commit", commit_then_raise)
        result = statements.delete_statement_import(
            import_id,
            StatementDeleteRequest(reason="测试提交成功后异常对账"),
            db,
        )

    assert result == {
        "deleted": True,
        "import_id": import_id,
        "source_file_deleted": True,
        "snapshot_id": snapshot_id,
    }
    assert not source.exists()
    assert not list(source.parent.glob(f".{source.name}.*.delete-pending"))
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id) is None
        assert db.get(BalanceSnapshot, snapshot_id) is None


def test_confirmed_statement_delete_rejects_source_below_statement_subdirectory() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as http:
        _company, _fc, _platform, _plan, _client, account = _create_hierarchy(http, suffix)
        import_id, snapshot_id, source, _dependent_id = _create_confirmed_statement(
            account_id=account["id"],
            suffix=suffix,
        )
        nested = source.parent / f"nested-{suffix}" / source.name
        nested.parent.mkdir()
        source.replace(nested)
        with SessionLocal() as db:
            db.get(StatementImport, import_id).stored_path = str(nested)
            db.commit()

        try:
            response = http.request(
                "DELETE",
                f"/api/statement-imports/{import_id}",
                json={"reason": "测试子目录原件保护"},
            )

            assert response.status_code == 409
            assert "顶层" in response.json()["detail"]
            assert nested.is_file()
            with SessionLocal() as db:
                assert db.get(StatementImport, import_id) is not None
                assert db.get(BalanceSnapshot, snapshot_id) is not None
        finally:
            if nested.is_file() and not source.exists():
                nested.replace(source)
            with SessionLocal() as db:
                item = db.get(StatementImport, import_id)
                if item is not None:
                    item.stored_path = str(source)
                    db.commit()
            nested.parent.rmdir()


def test_controlled_deletion_never_reuses_client_account_statement_or_snapshot_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    suffix = _suffix()
    account_number = f"AUTO-{suffix}"
    extracted = {
        "document_type": "empf_account_page",
        "client_name": f"Auto Client {suffix}",
        "account_number": account_number,
        "scheme_name": f"Synthetic Scheme {suffix}",
        "trustee": "Synthetic Trustee",
        "currency": "HKD",
        "as_of_date": "2026-05-20",
        "total_balance": "100.00",
        "holdings": [],
    }

    def parsed_statement(_path: Path):
        return SimpleNamespace(
            account_number=account_number,
            as_of_date=date(2026, 5, 20),
            total_balance="100.00",
            document_type="empf_account_page",
            warnings=[],
            raw_text="synthetic",
            confidence={},
            extracted_dict=lambda: dict(extracted),
        )

    monkeypatch.setattr(statements, "parse_empf_statement", parsed_statement)
    first_bytes = b"\xff\xd8\xffsynthetic monotonic first " + suffix.encode("ascii")
    second_bytes = b"\xff\xd8\xffsynthetic monotonic second " + suffix.encode("ascii")
    with TestClient(app, headers=WRITE_HEADERS) as http:
        first_upload = http.post(
            "/api/statement-imports",
            files={"file": ("first.jpg", first_bytes, "image/jpeg")},
        )
        assert first_upload.status_code == 201, first_upload.text
        first_statement_id = first_upload.json()["id"]
        confirmed = http.post(
            f"/api/statement-imports/{first_statement_id}/confirm",
            json={
                "client_name": extracted["client_name"],
                "account_number": account_number,
                "scheme_name": extracted["scheme_name"],
                "trustee": extracted["trustee"],
                "as_of_date": extracted["as_of_date"],
                "total_balance": extracted["total_balance"],
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        first_account_id = confirmed.json()["account_id"]
        first_snapshot_id = confirmed.json()["snapshot"]["id"]
        with SessionLocal() as db:
            first_client_id = db.get(SubAccount, first_account_id).client_id

        deleted = http.request(
            "DELETE",
            f"/api/statement-imports/{first_statement_id}",
            json={"reason": "验证删除后ID不会复用"},
        )
        assert deleted.status_code == 200, deleted.text
        assert http.delete(f"/api/accounts/{first_account_id}").status_code == 200
        assert http.delete(f"/api/clients/{first_client_id}").status_code == 200

        manual_client = http.post(
            "/api/clients",
            json={"name": f"Manual Replacement {suffix}", "status": "DRAFT"},
        )
        assert manual_client.status_code == 201, manual_client.text
        manual_account = http.post(
            "/api/accounts",
            json={
                "client_id": manual_client.json()["id"],
                "account_number": f"MANUAL-{suffix}",
                "status": "DRAFT",
            },
        )
        assert manual_account.status_code == 201, manual_account.text
        manual_snapshot = http.post(
            "/api/balance-snapshots",
            json={
                "account_id": manual_account.json()["id"],
                "as_of_date": "2026-05-21",
                "total_balance": "101.00",
                "eligible_for_closing": False,
            },
        )
        assert manual_snapshot.status_code == 201, manual_snapshot.text
        second_upload = http.post(
            "/api/statement-imports",
            files={"file": ("second.jpg", second_bytes, "image/jpeg")},
        )
        assert second_upload.status_code == 201, second_upload.text

    assert manual_client.json()["id"] > first_client_id
    assert manual_account.json()["id"] > first_account_id
    assert manual_snapshot.json()["id"] > first_snapshot_id
    assert second_upload.json()["id"] > first_statement_id
    with SessionLocal() as db:
        old_confirmation = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "STATEMENT_CONFIRMED",
                AuditEvent.entity_id == first_statement_id,
            )
        )
        assert old_confirmation is not None
        assert old_confirmation.details_json["account_id"] == first_account_id
        assert old_confirmation.details_json["snapshot_id"] == first_snapshot_id
        assert db.get(StatementImport, second_upload.json()["id"]) is not None
