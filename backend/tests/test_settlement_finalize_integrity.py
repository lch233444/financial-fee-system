from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

import app.config as config_module
from app.config import Settings, get_settings
from app.database import SessionLocal, engine
from app.main import app
from app.models import (
    Attachment,
    AuditEvent,
    BalanceSnapshot,
    FeePlan,
    StatementImport,
    TransactionRecord,
)
from app.routes.settlements import finalize_settlement, void_settlement
from app.schemas import VoidRequest
from app.services.entity_ids import allocate_entity_id
from app.services.storage import store_bytes


WRITE_HEADERS = {"X-Financial-System-Request": "1"}
HEAD_REVISION = "d4f8a1c73b29"


def _master(client: TestClient, suffix: str) -> dict:
    company = client.post(
        "/api/companies", json={"name": f"Integrity Company {suffix}", "code": f"IC{suffix}"}
    ).json()
    fc = client.post(
        "/api/fcs",
        json={
            "company_id": company["id"],
            "name": f"Integrity FC {suffix}",
            "code": f"IF{suffix}",
        },
    ).json()
    platform = client.post(
        "/api/platforms", json={"name": f"Integrity Platform {suffix}", "code": f"IP{suffix}"}
    ).json()
    plan = client.post(
        "/api/fee-plans",
        json={
            "company_id": company["id"],
            "name": f"Integrity Plan {suffix}",
            "code": f"PL{suffix}",
            "fee_rate_percent": "20",
        },
    ).json()
    customer = client.post(
        "/api/clients",
        json={
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"Integrity Client {suffix}",
            "management_start_date": "2026-01-01",
            "status": "ACTIVE",
        },
    ).json()
    account = client.post(
        "/api/accounts",
        json={
            "client_id": customer["id"],
            "platform_id": platform["id"],
            "fee_plan_id": plan["id"],
            "account_number": f"INT-{suffix}",
            "start_date": "2026-01-01",
            "status": "ACTIVE",
        },
    ).json()
    return {
        "company": company,
        "fc": fc,
        "platform": platform,
        "plan": plan,
        "client": customer,
        "account": account,
    }


def _attach(
    client: TestClient, entity_type: str, entity_id: int, *, marker: bytes = b"proof"
) -> dict:
    response = client.post(
        "/api/attachments",
        data={"entity_type": entity_type, "entity_id": str(entity_id)},
        files={
            "file": (
                f"proof-{entity_type}-{entity_id}.pdf",
                b"%PDF-1.4\n" + marker + b"\n%%EOF",
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _snapshot(
    client: TestClient,
    account_id: int,
    as_of_date: str,
    balance: str,
    *,
    closing: bool,
    evidence: bool = True,
) -> dict:
    response = client.post(
        "/api/balance-snapshots",
        json={
            "account_id": account_id,
            "as_of_date": as_of_date,
            "total_balance": balance,
            "eligible_for_closing": closing,
        },
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()
    if evidence:
        _attach(client, "SNAPSHOT", snapshot["id"])
    return snapshot


def _calculate(
    client: TestClient,
    data: dict,
    beginning: dict,
    closing: dict,
) -> dict:
    response = client.post(
        "/api/settlements/calculate",
        json={
            "client_id": data["client"]["id"],
            "platform_id": data["platform"]["id"],
            "fee_plan_id": data["plan"]["id"],
            "year": 2026,
            "quarter": 1,
            "account_lines": [
                {
                    "account_id": data["account"]["id"],
                    "beginning_snapshot_id": beginning["id"],
                    "closing_snapshot_id": closing["id"],
                    "original_hwm": "1000.00",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _case(client: TestClient, suffix: str, *, closing_balance: str = "1200.00") -> tuple[dict, dict, dict, dict]:
    data = _master(client, suffix)
    beginning = _snapshot(client, data["account"]["id"], "2026-01-01", "1000.00", closing=False)
    closing = _snapshot(
        client, data["account"]["id"], "2026-03-31", closing_balance, closing=True
    )
    settlement = _calculate(client, data, beginning, closing)
    return data, beginning, closing, settlement


def test_finalize_recalculates_current_transactions_and_requires_explicit_recalculate() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, beginning, closing, settlement = _case(client, "STALETX")
        assert settlement["service_fee"] == "40.00"
        transaction_response = client.post(
            "/api/transactions",
            json={
                "account_id": data["account"]["id"],
                "transaction_date": "2026-02-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        )
        assert transaction_response.status_code == 201, transaction_response.text
        _attach(client, "TRANSACTION", transaction_response.json()["id"])

        blocked = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert blocked.status_code == 409
        assert "重新Calculate" in blocked.json()["detail"]
        assert client.get(f"/api/settlements/{settlement['id']}").json()["status"] == "DRAFT"

        recalculated = _calculate(client, data, beginning, closing)
        assert recalculated["contribution"] == "100.00"
        assert recalculated["service_fee"] == "20.00"
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text


def test_finalize_rejects_fee_rate_changed_after_calculate() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, _beginning, _closing, settlement = _case(client, "STALEPLAN")
        with SessionLocal() as db:
            plan = db.get(FeePlan, data["plan"]["id"])
            assert plan is not None
            plan.fee_rate_bps = 2500
            db.commit()

        blocked = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert blocked.status_code == 409
        assert "fee_rate_bps" in blocked.json()["detail"]


def test_attachment_file_hash_and_size_are_checked_before_finalize() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _data, beginning, _closing, settlement = _case(client, "BADATTACH")
        with SessionLocal() as db:
            attachment = db.query(Attachment).filter_by(
                entity_type="SNAPSHOT", entity_id=beginning["id"]
            ).one()
            path = Path(attachment.stored_path)
            original_bytes = path.read_bytes()
            path.write_bytes(b"X" * attachment.size_bytes)

        try:
            blocked = client.post(f"/api/settlements/{settlement['id']}/finalize")
            assert blocked.status_code == 400
            assert "SHA-256" in blocked.json()["detail"]
        finally:
            path.write_bytes(original_bytes)


def test_statement_import_file_hash_is_checked_without_parsing_customer_content() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "BADSTATEMENT")
        beginning = _snapshot(
            client,
            data["account"]["id"],
            "2026-01-01",
            "1000.00",
            closing=False,
            evidence=False,
        )
        closing = _snapshot(client, data["account"]["id"], "2026-03-31", "1200.00", closing=True)
        statement_bytes = b"%PDF-1.4\nsynthetic statement evidence\n%%EOF"
        statement_path, digest = store_bytes(
            data=statement_bytes,
            original_name="synthetic.pdf",
            directory=get_settings().data_root / "statement_imports",
        )
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            snapshot = db.get(BalanceSnapshot, beginning["id"])
            assert snapshot is not None
            statement = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name="synthetic.pdf",
                stored_path=str(statement_path),
                sha256=digest,
                mime_type="application/pdf",
                status="CONFIRMED",
                confirmed_account_id=snapshot.account_id,
                confirmed_snapshot_id=snapshot.id,
            )
            db.add(statement)
            db.flush()
            snapshot.statement_import_id = statement.id
            db.commit()
        settlement = _calculate(client, data, beginning, closing)
        statement_path.write_bytes(b"Z" * len(statement_bytes))

        try:
            blocked = client.post(f"/api/settlements/{settlement['id']}/finalize")
            assert blocked.status_code == 400
            assert "SHA-256" in blocked.json()["detail"]
        finally:
            statement_path.write_bytes(statement_bytes)


def test_finalized_statement_import_evidence_row_is_frozen_until_void() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "STATEMENTLOCK")
        beginning = _snapshot(
            client,
            data["account"]["id"],
            "2026-01-01",
            "1000.00",
            closing=False,
            evidence=False,
        )
        closing = _snapshot(client, data["account"]["id"], "2026-03-31", "1200.00", closing=True)
        statement_bytes = b"%PDF-1.4\nsynthetic immutable statement\n%%EOF"
        statement_path, digest = store_bytes(
            data=statement_bytes,
            original_name="immutable.pdf",
            directory=get_settings().data_root / "statement_imports",
        )
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            snapshot = db.get(BalanceSnapshot, beginning["id"])
            assert snapshot is not None
            statement = StatementImport(
                id=allocate_entity_id(db, StatementImport),
                original_name="immutable.pdf",
                stored_path=str(statement_path),
                sha256=digest,
                mime_type="application/pdf",
                status="CONFIRMED",
                confirmed_account_id=snapshot.account_id,
                confirmed_snapshot_id=snapshot.id,
            )
            db.add(statement)
            db.flush()
            statement_id = statement.id
            snapshot.statement_import_id = statement_id
            db.commit()
        settlement = _calculate(client, data, beginning, closing)
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text

        database_path = get_settings().database_path
        for sql in (
            "UPDATE statement_imports SET confirmed_snapshot_id = NULL WHERE id = ?",
            "UPDATE statement_imports SET sha256 = lower(hex(randomblob(32))) WHERE id = ?",
            "DELETE FROM statement_imports WHERE id = ?",
        ):
            with sqlite3.connect(database_path) as connection:
                with pytest.raises(
                    sqlite3.IntegrityError,
                    match="statement_import_used_by_finalized_settlement",
                ):
                    connection.execute(sql, (statement_id,))

        with sqlite3.connect(database_path) as connection:
            with pytest.raises(
                sqlite3.IntegrityError,
                match="snapshot_used_by_frozen_settlement",
            ):
                connection.execute(
                    "DELETE FROM balance_snapshots WHERE id = ?", (beginning["id"],)
                )

        voided = client.post(
            f"/api/settlements/{settlement['id']}/void",
            json={"reason": "Allow controlled evidence correction"},
        )
        assert voided.status_code == 200, voided.text
        with sqlite3.connect(database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with pytest.raises(
                sqlite3.IntegrityError,
                match="statement_import_delete_has_snapshot",
            ):
                connection.execute(
                    "DELETE FROM statement_imports WHERE id = ?", (statement_id,)
                )
            connection.execute(
                "UPDATE statement_imports SET confirmed_snapshot_id = NULL WHERE id = ?",
                (statement_id,),
            )
            connection.execute(
                "UPDATE balance_snapshots SET statement_import_id = NULL WHERE id = ?",
                (beginning["id"],),
            )
            connection.execute("DELETE FROM statement_imports WHERE id = ?", (statement_id,))
            connection.commit()
            assert connection.execute(
                "SELECT statement_import_id FROM balance_snapshots WHERE id = ?",
                (beginning["id"],),
            ).fetchone()[0] is None
        statement_path.unlink()


def test_valid_generic_transaction_attachment_can_replace_damaged_direct_reference() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "REPAIREDPROOF")
        beginning = _snapshot(client, data["account"]["id"], "2026-01-01", "1000.00", closing=False)
        closing = _snapshot(client, data["account"]["id"], "2026-03-31", "1210.00", closing=True)
        transaction_response = client.post(
            "/api/transactions",
            json={
                "account_id": data["account"]["id"],
                "transaction_date": "2026-02-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        )
        transaction_id = transaction_response.json()["id"]
        direct = _attach(client, "TRANSACTION", transaction_id, marker=b"old direct proof")
        with SessionLocal() as db:
            transaction = db.get(TransactionRecord, transaction_id)
            attachment = db.get(Attachment, direct["id"])
            assert transaction is not None and attachment is not None
            transaction.attachment_id = attachment.id
            damaged_path = Path(attachment.stored_path)
            original_direct_proof = damaged_path.read_bytes()
            damaged_path.write_bytes(b"D" * attachment.size_bytes)
            db.commit()
        try:
            _attach(client, "TRANSACTION", transaction_id, marker=b"replacement generic proof")
            settlement = _calculate(client, data, beginning, closing)

            finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
            assert finalized.status_code == 200, finalized.text
        finally:
            damaged_path.write_bytes(original_direct_proof)


def test_finalized_parent_children_inputs_are_immutable_but_void_allows_source_correction() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "DBLOCKS")
        beginning = _snapshot(client, data["account"]["id"], "2026-01-01", "1000.00", closing=False)
        closing = _snapshot(client, data["account"]["id"], "2026-03-31", "1210.00", closing=True)
        transaction = client.post(
            "/api/transactions",
            json={
                "account_id": data["account"]["id"],
                "transaction_date": "2026-02-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        ).json()
        transaction_proof = _attach(client, "TRANSACTION", transaction["id"])
        with SessionLocal() as db:
            snapshot_proof = db.query(Attachment).filter_by(
                entity_type="SNAPSHOT", entity_id=closing["id"]
            ).one()
            snapshot_proof_id = snapshot_proof.id
            transaction_attachment = db.get(Attachment, transaction_proof["id"])
            assert transaction_attachment is not None
            original_transaction_proof_sha = transaction_attachment.sha256
        settlement = _calculate(client, data, beginning, closing)
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        line_id = finalized.json()["account_lines"][0]["id"]
        database_path = get_settings().database_path

        guarded_statements = [
            ("UPDATE quarterly_settlements SET service_fee_cents = service_fee_cents + 1 WHERE id = ?", (settlement["id"],)),
            ("UPDATE settlement_account_lines SET service_fee_cents = service_fee_cents + 1 WHERE id = ?", (line_id,)),
            ("DELETE FROM quarterly_settlements WHERE id = ?", (settlement["id"],)),
            ("UPDATE transactions SET amount_cents = amount_cents + 1 WHERE id = ?", (transaction["id"],)),
            ("DELETE FROM transactions WHERE id = ?", (transaction["id"],)),
            ("UPDATE balance_snapshots SET total_balance_cents = total_balance_cents + 1 WHERE id = ?", (closing["id"],)),
            ("UPDATE attachments SET sha256 = ? WHERE id = ?", ("a" * 64, transaction_proof["id"])),
            ("DELETE FROM attachments WHERE id = ?", (transaction_proof["id"],)),
            ("UPDATE attachments SET entity_id = NULL WHERE id = ?", (snapshot_proof_id,)),
            ("DELETE FROM attachments WHERE id = ?", (snapshot_proof_id,)),
            (
                "INSERT INTO transactions (account_id, transaction_date, transaction_type, amount_cents, created_at, updated_at) "
                "VALUES (?, '2026-01-01', 'CONTRIBUTION', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                (data["account"]["id"],),
            ),
        ]
        for sql, parameters in guarded_statements:
            with sqlite3.connect(database_path) as connection:
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(sql, parameters)

        voided = client.post(
            f"/api/settlements/{settlement['id']}/void", json={"reason": "Synthetic correction"}
        )
        assert voided.status_code == 200, voided.text
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE transactions SET amount_cents = amount_cents + 1 WHERE id = ?",
                (transaction["id"],),
            )
            connection.execute(
                "UPDATE balance_snapshots SET total_balance_cents = total_balance_cents + 1 WHERE id = ?",
                (closing["id"],),
            )
            connection.execute(
                "UPDATE attachments SET sha256 = ? WHERE id = ?",
                ("b" * 64, transaction_proof["id"]),
            )
            connection.execute(
                "UPDATE attachments SET entity_id = NULL WHERE id = ?",
                (snapshot_proof_id,),
            )
            connection.commit()
        for sql, parameters in guarded_statements[:3]:
            with sqlite3.connect(database_path) as connection:
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(sql, parameters)
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE attachments SET sha256 = ? WHERE id = ?",
                (original_transaction_proof_sha, transaction_proof["id"]),
            )
            connection.commit()


def test_direct_status_bypasses_require_metadata_reason_and_draft_insert() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _data, _beginning, _closing, settlement = _case(client, "DIRECTSTATE")
        database_path = get_settings().database_path
        with sqlite3.connect(database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="settlement_finalize_metadata_invalid"):
                connection.execute(
                    "UPDATE quarterly_settlements SET status = 'FINALIZED' WHERE id = ?",
                    (settlement["id"],),
                )
        with sqlite3.connect(database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="settlement_lifecycle_transition_invalid"):
                connection.execute(
                    "UPDATE quarterly_settlements SET status = 'VOID', void_reason = 'bad draft void' "
                    "WHERE id = ?",
                    (settlement["id"],),
                )

        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        with sqlite3.connect(database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE quarterly_settlements SET void_reason = 'same-status tamper' WHERE id = ?",
                    (settlement["id"],),
                )
        with sqlite3.connect(database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="settlement_void_reason_required"):
                connection.execute(
                    "UPDATE quarterly_settlements SET status = 'VOID' WHERE id = ?",
                    (settlement["id"],),
                )
        with sqlite3.connect(database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="settlement_insert_must_be_draft"):
                connection.execute(
                    """
                    INSERT INTO quarterly_settlements (
                        client_id, platform_id, fee_plan_id, company_id, fc_id,
                        previous_settlement_id, version_no, replaces_settlement_id,
                        year, quarter, start_date, closing_date, days,
                        beginning_cents, contribution_cents, withdrawal_cents,
                        net_contribution_cents, closing_cents, gain_loss_cents,
                        period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                        watermark_difference_cents, chargeable_above_hwm_cents,
                        service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                        calculation_mode, status, finalized_at, void_reason, created_at, updated_at
                    )
                    SELECT client_id, platform_id, fee_plan_id, company_id, fc_id,
                           previous_settlement_id, 1, NULL,
                           2027, 1, '2027-01-01', '2027-03-31', 90,
                           beginning_cents, contribution_cents, withdrawal_cents,
                           net_contribution_cents, closing_cents, gain_loss_cents,
                           period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                           watermark_difference_cents, chargeable_above_hwm_cents,
                           service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                           calculation_mode, 'FINALIZED', CURRENT_TIMESTAMP, NULL,
                           CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM quarterly_settlements WHERE id = ?
                    """,
                    (settlement["id"],),
                )


def test_direct_finalize_rejects_tampered_period_rate_and_formula_version() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _data, _beginning, _closing, settlement = _case(client, "DIRECTFORMULA")
        settlement_id = settlement["id"]
        line_id = settlement["account_lines"][0]["id"]
        database_path = get_settings().database_path

        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "UPDATE settlement_account_lines SET period_rate_ppm = 42 WHERE id = ?",
                (line_id,),
            )
            connection.execute(
                "UPDATE quarterly_settlements SET period_rate_ppm = 42 WHERE id = ?",
                (settlement_id,),
            )
            connection.commit()
            with pytest.raises(sqlite3.IntegrityError, match="settlement_account_line_incomplete"):
                connection.execute(
                    "UPDATE quarterly_settlements "
                    "SET status = 'FINALIZED', finalized_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (settlement_id,),
                )
            connection.rollback()
            connection.execute(
                "UPDATE settlement_account_lines SET period_rate_ppm = 200000 WHERE id = ?",
                (line_id,),
            )
            connection.execute(
                "UPDATE quarterly_settlements "
                "SET period_rate_ppm = 200000, formula_version = 'UNAPPROVED' WHERE id = ?",
                (settlement_id,),
            )
            connection.execute(
                "UPDATE settlement_account_lines SET formula_version = 'UNAPPROVED' WHERE id = ?",
                (line_id,),
            )
            connection.commit()
            with pytest.raises(sqlite3.IntegrityError, match="settlement_formula_version_invalid"):
                connection.execute(
                    "UPDATE quarterly_settlements "
                    "SET status = 'FINALIZED', finalized_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (settlement_id,),
                )
            connection.rollback()

        deleted = client.delete(f"/api/settlements/{settlement_id}")
        assert deleted.status_code == 200, deleted.text


def test_period_rate_sql_integer_boundary_is_explicit_and_exact() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "RATEBOUNDARY")
        maximum = "90000000000.00"
        beginning = _snapshot(
            client, data["account"]["id"], "2026-01-01", maximum, closing=False
        )
        closing = _snapshot(
            client, data["account"]["id"], "2026-03-31", maximum, closing=True
        )
        calculated = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["client"]["id"],
                "platform_id": data["platform"]["id"],
                "fee_plan_id": data["plan"]["id"],
                "year": 2026,
                "quarter": 1,
                "account_lines": [
                    {
                        "account_id": data["account"]["id"],
                        "beginning_snapshot_id": beginning["id"],
                        "closing_snapshot_id": closing["id"],
                        "original_hwm": maximum,
                    }
                ],
            },
        )
        assert calculated.status_code == 200, calculated.text
        assert calculated.json()["period_rate"] == 0
        finalized = client.post(f"/api/settlements/{calculated.json()['id']}/finalize")
        assert finalized.status_code == 200, finalized.text

        too_large_data = _master(client, "RATEOVER")
        too_large = "90000000000.01"
        too_large_beginning = _snapshot(
            client,
            too_large_data["account"]["id"],
            "2026-01-01",
            too_large,
            closing=False,
        )
        too_large_closing = _snapshot(
            client,
            too_large_data["account"]["id"],
            "2026-03-31",
            too_large,
            closing=True,
        )
        rejected = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": too_large_data["client"]["id"],
                "platform_id": too_large_data["platform"]["id"],
                "fee_plan_id": too_large_data["plan"]["id"],
                "year": 2026,
                "quarter": 1,
                "account_lines": [
                    {
                        "account_id": too_large_data["account"]["id"],
                        "beginning_snapshot_id": too_large_beginning["id"],
                        "closing_snapshot_id": too_large_closing["id"],
                        "original_hwm": too_large,
                    }
                ],
            },
        )
        assert rejected.status_code == 400
        assert "90,000,000,000" in rejected.json()["detail"]


def test_draft_can_be_deleted_with_audit_but_cannot_be_voided_or_delete_history() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _data, _beginning, _closing, draft = _case(client, "DRAFTDELETE")
        line_id = draft["account_lines"][0]["id"]

        void_draft = client.post(
            f"/api/settlements/{draft['id']}/void", json={"reason": "wrong operation"}
        )
        assert void_draft.status_code == 409
        assert "Finalized" in void_draft.json()["detail"]

        deleted = client.delete(f"/api/settlements/{draft['id']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"status": "deleted", "id": draft["id"]}
        assert client.get(f"/api/settlements/{draft['id']}").status_code == 404
        with sqlite3.connect(get_settings().database_path) as connection:
            assert connection.execute(
                "SELECT 1 FROM settlement_account_lines WHERE id = ?", (line_id,)
            ).fetchone() is None
        with SessionLocal() as db:
            audit = db.query(AuditEvent).filter_by(
                action="SETTLEMENT_DRAFT_DELETED",
                entity_type="SETTLEMENT",
                entity_id=None,
            ).order_by(AuditEvent.id.desc()).first()
            assert audit is not None
            assert audit.details_json["deleted_settlement_id"] == draft["id"]
            assert audit.details_json["client_id"] == _data["client"]["id"]
            assert audit.details_json["year"] == 2026
            assert audit.details_json["quarter"] == 1

        _data2, _beginning2, _closing2, finalized_case = _case(client, "NODELETEHISTORY")
        finalized = client.post(f"/api/settlements/{finalized_case['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        blocked_finalized = client.delete(f"/api/settlements/{finalized_case['id']}")
        assert blocked_finalized.status_code == 409
        voided = client.post(
            f"/api/settlements/{finalized_case['id']}/void",
            json={"reason": "history remains immutable"},
        )
        assert voided.status_code == 200, voided.text
        blocked_void = client.delete(f"/api/settlements/{finalized_case['id']}")
        assert blocked_void.status_code == 409


def test_finalize_rechecks_replacement_identity_even_if_database_guard_was_bypassed() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, beginning, closing, first = _case(client, "REPLFINAL")
        finalized = client.post(f"/api/settlements/{first['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        voided = client.post(
            f"/api/settlements/{first['id']}/void",
            json={"reason": "Create a controlled replacement draft"},
        )
        assert voided.status_code == 200, voided.text
        replacement = _calculate(client, data, beginning, closing)
        assert replacement["version_no"] == 2
        assert replacement["replaces_settlement_id"] == first["id"]

        database_path = get_settings().database_path
        with sqlite3.connect(database_path) as connection:
            trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' "
                "AND name='trg_settlement_parent_financial_lock'"
            ).fetchone()[0]
            try:
                connection.execute("DROP TRIGGER trg_settlement_parent_financial_lock")
                connection.execute(
                    "UPDATE quarterly_settlements SET year = year + 1 WHERE id = ?",
                    (replacement["id"],),
                )
            finally:
                connection.execute(trigger_sql)
                connection.commit()

        blocked = client.post(f"/api/settlements/{replacement['id']}/finalize")
        assert blocked.status_code == 409
        assert "替代关系已失效" in blocked.json()["detail"]
        with sqlite3.connect(database_path) as connection:
            assert connection.execute(
                "SELECT status FROM quarterly_settlements WHERE id = ?",
                (replacement["id"],),
            ).fetchone()[0] == "DRAFT"


def test_delete_stale_draft_unblocks_recalculation_after_account_plan_change() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, beginning, closing, stale_draft = _case(client, "DRAFTREPLACE")
        replacement_plan = client.post(
            "/api/fee-plans",
            json={
                "company_id": data["company"]["id"],
                "name": "Replacement Plan DRAFTREPLACE",
                "code": "RPDRAFTREPLACE",
                "fee_rate_percent": "20",
            },
        ).json()
        changed = client.patch(
            f"/api/accounts/{data['account']['id']}",
            json={"fee_plan_id": replacement_plan["id"]},
        )
        assert changed.status_code == 200, changed.text
        replacement_data = {**data, "plan": replacement_plan}
        payload = {
            "client_id": data["client"]["id"],
            "platform_id": data["platform"]["id"],
            "fee_plan_id": replacement_plan["id"],
            "year": 2026,
            "quarter": 1,
            "account_lines": [
                {
                    "account_id": data["account"]["id"],
                    "beginning_snapshot_id": beginning["id"],
                    "closing_snapshot_id": closing["id"],
                    "original_hwm": "1000.00",
                }
            ],
        }
        blocked = client.post("/api/settlements/calculate", json=payload)
        assert blocked.status_code == 409
        assert "已存在Settlement" in blocked.json()["detail"]

        deleted = client.delete(f"/api/settlements/{stale_draft['id']}")
        assert deleted.status_code == 200, deleted.text
        replacement = _calculate(client, replacement_data, beginning, closing)
        assert replacement["fee_plan_id"] == replacement_plan["id"]


def test_same_account_period_cannot_repeat_after_plan_change_in_app_or_sql() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, beginning, closing, first = _case(client, "PERIODDUP")
        finalized = client.post(f"/api/settlements/{first['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        replacement_plan = client.post(
            "/api/fee-plans",
            json={
                "company_id": data["company"]["id"],
                "name": "Replacement Plan PERIODDUP",
                "code": "RPPERIODDUP",
                "fee_rate_percent": "20",
            },
        ).json()
        changed = client.patch(
            f"/api/accounts/{data['account']['id']}",
            json={"fee_plan_id": replacement_plan["id"]},
        )
        assert changed.status_code == 200, changed.text
        blocked = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["client"]["id"],
                "platform_id": data["platform"]["id"],
                "fee_plan_id": replacement_plan["id"],
                "year": 2026,
                "quarter": 1,
                "account_lines": [
                    {
                        "account_id": data["account"]["id"],
                        "beginning_snapshot_id": beginning["id"],
                        "closing_snapshot_id": closing["id"],
                        "original_hwm": "1000.00",
                    }
                ],
            },
        )
        assert blocked.status_code == 409
        assert "不能重复结算" in blocked.json()["detail"]

        database_path = get_settings().database_path
        with sqlite3.connect(database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            cursor = connection.execute(
                """
                INSERT INTO quarterly_settlements (
                    client_id, platform_id, fee_plan_id, company_id, fc_id,
                    previous_settlement_id, version_no, replaces_settlement_id,
                    year, quarter, start_date, closing_date, days,
                    beginning_cents, contribution_cents, withdrawal_cents,
                    net_contribution_cents, closing_cents, gain_loss_cents,
                    period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                    watermark_difference_cents, chargeable_above_hwm_cents,
                    service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                    calculation_mode, status, finalized_at, void_reason, created_at, updated_at
                )
                SELECT client_id, platform_id, ?, company_id, fc_id,
                       NULL, 1, NULL, year, quarter, start_date, closing_date, days,
                       beginning_cents, contribution_cents, withdrawal_cents,
                       net_contribution_cents, closing_cents, gain_loss_cents,
                       period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                       watermark_difference_cents, chargeable_above_hwm_cents,
                       service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                       calculation_mode, 'DRAFT', NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM quarterly_settlements WHERE id = ?
                """,
                (replacement_plan["id"], first["id"]),
            )
            duplicate_draft_id = cursor.lastrowid
            connection.commit()
            with pytest.raises(sqlite3.IntegrityError, match="settlement_account_period_duplicate"):
                connection.execute(
                    """
                    INSERT INTO settlement_account_lines (
                        settlement_id, account_id, previous_line_id,
                        beginning_snapshot_id, closing_snapshot_id,
                        start_date, closing_date, days, beginning_cents, closing_cents,
                        contribution_cents, withdrawal_cents, net_contribution_cents,
                        gain_loss_cents, period_rate_ppm, original_hwm_cents,
                        adjusted_hwm_cents, watermark_difference_cents,
                        chargeable_above_hwm_cents, service_fee_cents, next_hwm_cents,
                        fee_rate_bps, formula_version, remark, created_at, updated_at
                    )
                    SELECT ?, account_id, previous_line_id,
                           beginning_snapshot_id, closing_snapshot_id,
                           start_date, closing_date, days, beginning_cents, closing_cents,
                           contribution_cents, withdrawal_cents, net_contribution_cents,
                           gain_loss_cents, period_rate_ppm, original_hwm_cents,
                           adjusted_hwm_cents, watermark_difference_cents,
                           chargeable_above_hwm_cents, service_fee_cents, next_hwm_cents,
                           fee_rate_bps, formula_version, remark, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM settlement_account_lines WHERE settlement_id = ?
                    """,
                    (duplicate_draft_id, first["id"]),
                )
            connection.rollback()

            account_order_trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_settlement_account_line_order'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER trg_settlement_account_line_order")
            connection.commit()
            try:
                connection.execute(
                    """
                    INSERT INTO settlement_account_lines (
                        settlement_id, account_id, previous_line_id,
                        beginning_snapshot_id, closing_snapshot_id,
                        start_date, closing_date, days, beginning_cents, closing_cents,
                        contribution_cents, withdrawal_cents, net_contribution_cents,
                        gain_loss_cents, period_rate_ppm, original_hwm_cents,
                        adjusted_hwm_cents, watermark_difference_cents,
                        chargeable_above_hwm_cents, service_fee_cents, next_hwm_cents,
                        fee_rate_bps, formula_version, remark, created_at, updated_at
                    )
                    SELECT ?, account_id, previous_line_id,
                           beginning_snapshot_id, closing_snapshot_id,
                           start_date, closing_date, days, beginning_cents, closing_cents,
                           contribution_cents, withdrawal_cents, net_contribution_cents,
                           gain_loss_cents, period_rate_ppm, original_hwm_cents,
                           adjusted_hwm_cents, watermark_difference_cents,
                           chargeable_above_hwm_cents, service_fee_cents, next_hwm_cents,
                           fee_rate_bps, formula_version, remark, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    FROM settlement_account_lines WHERE settlement_id = ?
                    """,
                    (duplicate_draft_id, first["id"]),
                )
                connection.commit()
            finally:
                connection.execute(account_order_trigger_sql)
                connection.commit()

            with pytest.raises(sqlite3.IntegrityError, match="settlement_account_period_duplicate"):
                connection.execute(
                    "UPDATE quarterly_settlements "
                    "SET status = 'FINALIZED', finalized_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (duplicate_draft_id,),
                )
            connection.rollback()
            connection.execute(
                "DELETE FROM quarterly_settlements WHERE id = ?", (duplicate_draft_id,)
            )
            connection.commit()


def test_finalize_begin_immediate_serializes_two_database_connections() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _data, _beginning, _closing, settlement = _case(client, "TWOCONN")
        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
            statements.append(statement.strip())

        def finalize_in_worker() -> dict:
            with SessionLocal() as db:
                return finalize_settlement(settlement["id"], db)

        first = sqlite3.connect(get_settings().database_path, timeout=5, check_same_thread=False)
        first.execute("BEGIN IMMEDIATE")
        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(finalize_in_worker)
                time.sleep(0.2)
                assert not future.done()
                first.commit()
                result = future.result(timeout=5)
            assert result["status"] == "FINALIZED"
            assert statements
            assert statements[0].upper() == "BEGIN IMMEDIATE"
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)
            first.close()


def test_void_begin_immediate_serializes_before_reading_dependencies() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _data, _beginning, _closing, settlement = _case(client, "VOIDTWOCONN")
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _parameters, _context, _many) -> None:
            statements.append(statement.strip())

        def void_in_worker() -> dict:
            with SessionLocal() as db:
                return void_settlement(
                    settlement["id"], VoidRequest(reason="serialize dependency check"), db
                )

        first = sqlite3.connect(get_settings().database_path, timeout=5, check_same_thread=False)
        first.execute("BEGIN IMMEDIATE")
        event.listen(engine, "before_cursor_execute", record_statement)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(void_in_worker)
                time.sleep(0.2)
                assert not future.done()
                first.commit()
                result = future.result(timeout=5)
            assert result["status"] == "VOID"
            assert statements
            assert statements[0].upper() == "BEGIN IMMEDIATE"
        finally:
            event.remove(engine, "before_cursor_execute", record_statement)
            first.close()


def _alembic_config(settings: Settings) -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def _seed_c4_master_rows(connection: sqlite3.Connection, *, with_account: bool = False) -> None:
    connection.executescript(
        """
        INSERT INTO companies (
            id, name, code, payment_terms_days, active, created_at, updated_at
        ) VALUES (1, 'Migration Company', 'MIGCO', 14, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        INSERT INTO fcs (
            id, company_id, name, code, active, created_at, updated_at
        ) VALUES (1, 1, 'Migration FC', 'MIGFC', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        INSERT INTO platforms (
            id, name, code, active, created_at, updated_at
        ) VALUES (1, 'Migration Platform', 'MIGPLAT', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        INSERT INTO fee_plans (
            id, company_id, name, code, fee_rate_bps, calculation_method, active,
            created_at, updated_at
        ) VALUES (
            1, 1, 'Migration Plan', 'MIGPLAN', 2000, 'HIGH_WATER_MARK', 1,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        );
        INSERT INTO clients (
            id, company_id, fc_id, name, management_start_date, status,
            created_at, updated_at
        ) VALUES (
            1, 1, 1, 'Migration Client', '2026-01-01', 'ACTIVE',
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        );
        """
    )
    if with_account:
        connection.execute(
            """
            INSERT INTO sub_accounts (
                id, client_id, platform_id, fee_plan_id, account_number, currency,
                start_date, status, created_at, updated_at
            ) VALUES (
                1, 1, 1, 1, 'MIG-ACCOUNT', 'HKD', '2026-01-01', 'ACTIVE',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )


def _seed_c4_finalized_account_history(connection: sqlite3.Connection) -> None:
    _seed_c4_master_rows(connection, with_account=True)
    connection.executescript(
        """
        INSERT INTO balance_snapshots (
            id, account_id, as_of_date, total_balance_cents, currency, source_type,
            eligible_for_closing, created_at, updated_at
        ) VALUES
            (1, 1, '2026-01-01', 100000, 'HKD', 'MANUAL', 0,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            (2, 1, '2026-03-31', 120000, 'HKD', 'MANUAL', 1,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            (3, 1, '2026-06-30', 130000, 'HKD', 'MANUAL', 1,
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

        INSERT INTO attachments (
            id, entity_type, entity_id, original_name, stored_path, sha256,
            mime_type, size_bytes, created_at, updated_at
        ) VALUES
            (1, 'SNAPSHOT', 1, 's1.pdf', 'F:/synthetic/s1.pdf',
             '1111111111111111111111111111111111111111111111111111111111111111',
             'application/pdf', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            (2, 'SNAPSHOT', 2, 's2.pdf', 'F:/synthetic/s2.pdf',
             '2222222222222222222222222222222222222222222222222222222222222222',
             'application/pdf', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            (3, 'SNAPSHOT', 3, 's3.pdf', 'F:/synthetic/s3.pdf',
             '3333333333333333333333333333333333333333333333333333333333333333',
             'application/pdf', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

        INSERT INTO quarterly_settlements (
            id, client_id, platform_id, fee_plan_id, company_id, fc_id,
            previous_settlement_id, year, quarter, start_date, closing_date, days,
            beginning_cents, contribution_cents, withdrawal_cents,
            net_contribution_cents, closing_cents, gain_loss_cents,
            period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
            watermark_difference_cents, chargeable_above_hwm_cents,
            service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
            calculation_mode, status, finalized_at, created_at, updated_at
        ) VALUES
            (1, 1, 1, 1, 1, 1, NULL, 2026, 1,
             '2026-01-01', '2026-03-31', 90,
             100000, 0, 0, 0, 120000, 20000, 200000,
             100000, 100000, 20000, 20000, 4000, 120000,
             2000, 'HWM-2.0-ACCOUNT', 'ACCOUNT_HWM', 'FINALIZED',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            (2, 1, 1, 1, 1, 1, 1, 2026, 2,
             '2026-04-01', '2026-06-30', 91,
             120000, 0, 0, 0, 130000, 10000, 83333,
             120000, 120000, 10000, 10000, 2000, 130000,
             2000, 'HWM-2.0-ACCOUNT', 'ACCOUNT_HWM', 'FINALIZED',
             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

        INSERT INTO settlement_account_lines (
            id, settlement_id, account_id, previous_line_id,
            beginning_snapshot_id, closing_snapshot_id,
            start_date, closing_date, days, beginning_cents, closing_cents,
            contribution_cents, withdrawal_cents, net_contribution_cents,
            gain_loss_cents, period_rate_ppm, original_hwm_cents,
            adjusted_hwm_cents, watermark_difference_cents,
            chargeable_above_hwm_cents, service_fee_cents, next_hwm_cents,
            fee_rate_bps, formula_version, created_at, updated_at
        ) VALUES
            (1, 1, 1, NULL, 1, 2, '2026-01-01', '2026-03-31', 90,
             100000, 120000, 0, 0, 0, 20000, 200000,
             100000, 100000, 20000, 20000, 4000, 120000,
             2000, 'HWM-2.0-ACCOUNT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
            (2, 2, 1, 1, 2, 3, '2026-04-01', '2026-06-30', 91,
             120000, 130000, 0, 0, 0, 10000, 83333,
             120000, 120000, 10000, 10000, 2000, 130000,
             2000, 'HWM-2.0-ACCOUNT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        """
    )


def test_legal_stale_void_draft_does_not_block_upgrade_and_unsafe_downgrade_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_root=tmp_path / "stale-void")
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, "c4b7f1d92e60")
    with sqlite3.connect(settings.database_path) as connection:
        _seed_c4_master_rows(connection)
        connection.execute(
            """
            INSERT INTO quarterly_settlements (
                client_id, platform_id, fee_plan_id, company_id, fc_id,
                year, quarter, start_date, closing_date, days,
                beginning_cents, contribution_cents, withdrawal_cents,
                net_contribution_cents, closing_cents, gain_loss_cents,
                period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                watermark_difference_cents, chargeable_above_hwm_cents,
                service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                calculation_mode, status, void_reason, created_at, updated_at
            ) VALUES (
                1, 1, 1, 1, 1,
                2026, 1, '2026-01-01', '2026-03-31', 90,
                999, 123, 7, 116, 5, -1110,
                777, 4, 88, -83, 0, 765, 456, 2000, 'HWM-2.0-ACCOUNT',
                'ACCOUNT_HWM', 'VOID', 'Expired but valid legacy Draft',
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()

    command.upgrade(alembic_config, "head")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
        triggers_before = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
    assert "trg_settlement_parent_financial_lock" in triggers_before

    with pytest.raises(RuntimeError, match="原地降级"):
        command.downgrade(alembic_config, "c4b7f1d92e60")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
        triggers_after = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
    assert triggers_after == triggers_before


def test_upgrade_rejects_foreign_key_damage_before_replacing_any_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(data_root=tmp_path / "broken-fk")
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, "c4b7f1d92e60")
    with sqlite3.connect(settings.database_path) as connection:
        triggers_before = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
        connection.execute(
            """
            INSERT INTO quarterly_settlements (
                client_id, platform_id, fee_plan_id, company_id, fc_id,
                year, quarter, start_date, closing_date, days,
                beginning_cents, contribution_cents, withdrawal_cents,
                net_contribution_cents, closing_cents, gain_loss_cents,
                period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                watermark_difference_cents, chargeable_above_hwm_cents,
                service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                calculation_mode, status, void_reason, created_at, updated_at
            ) VALUES (
                999, 999, 999, 999, 999,
                2026, 1, '2026-01-01', '2026-03-31', 90,
                1, 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1,
                2000, 'HWM-2.0-ACCOUNT', 'ACCOUNT_HWM', 'VOID',
                'Broken FK fixture', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="外键完整性"):
        command.upgrade(alembic_config, "head")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "c4b7f1d92e60"
        triggers_after = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
            )
        }
    assert triggers_after == triggers_before


@pytest.mark.parametrize(
    "corruption",
    ["previous_chain", "integer_formula", "period_rate", "formula_version"],
)
def test_upgrade_rejects_corrupted_finalized_hwm_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    settings = Settings(data_root=tmp_path / f"bad-{corruption}")
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, "c4b7f1d92e60")
    with sqlite3.connect(settings.database_path) as connection:
        _seed_c4_finalized_account_history(connection)
        if corruption == "previous_chain":
            connection.execute(
                "UPDATE settlement_account_lines SET previous_line_id = NULL WHERE id = 2"
            )
        elif corruption == "integer_formula":
            connection.execute(
                "UPDATE settlement_account_lines SET service_fee_cents = 2001 WHERE id = 2"
            )
            connection.execute(
                "UPDATE quarterly_settlements SET service_fee_cents = 2001 WHERE id = 2"
            )
        elif corruption == "period_rate":
            connection.execute(
                "UPDATE settlement_account_lines SET period_rate_ppm = 42 WHERE id = 2"
            )
            connection.execute(
                "UPDATE quarterly_settlements SET period_rate_ppm = 42 WHERE id = 2"
            )
        else:
            connection.execute(
                "UPDATE settlement_account_lines SET formula_version = 'UNAPPROVED' WHERE id = 2"
            )
            connection.execute(
                "UPDATE quarterly_settlements SET formula_version = 'UNAPPROVED' WHERE id = 2"
            )
        connection.commit()

    with pytest.raises(RuntimeError, match="Finalized Settlement"):
        command.upgrade(alembic_config, "head")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "c4b7f1d92e60"


def _prepare_9d_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[Settings, Config]:
    settings = Settings(data_root=tmp_path / name)
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, "c4b7f1d92e60")
    with sqlite3.connect(settings.database_path) as connection:
        _seed_c4_finalized_account_history(connection)
        connection.commit()
    command.upgrade(alembic_config, "9d2f6a8c4b13")
    return settings, alembic_config


def _schema_snapshot(database_path: Path) -> list[tuple]:
    with sqlite3.connect(database_path) as connection:
        return connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()


def test_0_2_14_upgrade_preserves_settlement_ids_self_references_and_foreign_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, alembic_config = _prepare_9d_history(tmp_path, monkeypatch, "preserve-ids")

    command.upgrade(alembic_config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
        assert connection.execute(
            "SELECT id, previous_settlement_id, version_no, replaces_settlement_id "
            "FROM quarterly_settlements ORDER BY id"
        ).fetchall() == [(1, None, 1, None), (2, 1, 1, None)]
        assert connection.execute(
            "SELECT id, settlement_id, previous_line_id FROM settlement_account_lines ORDER BY id"
        ).fetchall() == [(1, 1, None), (2, 2, 1)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        settlement_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(quarterly_settlements)")
        }
        assert ("previous_settlement_id", "quarterly_settlements", "id", "RESTRICT") in settlement_fks
        assert ("replaces_settlement_id", "quarterly_settlements", "id", "RESTRICT") in settlement_fks
        line_fks = {
            (row[3], row[2], row[4], row[6])
            for row in connection.execute("PRAGMA foreign_key_list(settlement_account_lines)")
        }
        assert ("previous_line_id", "settlement_account_lines", "id", "RESTRICT") in line_fks
        # This revision intentionally does not repair the historical ORM/DDL
        # drift for closing_snapshot_id; changing it belongs in a separate risk review.
        assert ("closing_snapshot_id", "balance_snapshots", "id", "SET NULL") in line_fks
        active_index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='uq_settlement_group_period_active'"
        ).fetchone()[0]
        assert "WHERE status != 'VOID'" in active_index_sql

        connection.execute(
            "UPDATE quarterly_settlements SET status='VOID', "
            "void_reason='Synthetic version replacement' WHERE id=2"
        )
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(quarterly_settlements)")
        ]
        original = dict(
            zip(
                columns,
                connection.execute(
                    f"SELECT {', '.join(columns)} FROM quarterly_settlements WHERE id=2"
                ).fetchone(),
                strict=True,
            )
        )
        replacement = {
            **original,
            "id": 3,
            "version_no": 2,
            "replaces_settlement_id": 2,
            "status": "DRAFT",
            "finalized_at": None,
            "void_reason": None,
        }
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO quarterly_settlements ({', '.join(columns)}) "
            f"VALUES ({placeholders})",
            tuple(replacement[name] for name in columns),
        )
        assert connection.execute(
            "SELECT version_no, replaces_settlement_id FROM quarterly_settlements WHERE id=3"
        ).fetchone() == (2, 2)

        connection.execute(
            "UPDATE quarterly_settlements SET previous_settlement_id = NULL WHERE id = 3"
        )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="settlement_container_previous_changed",
        ):
            connection.execute(
                "UPDATE quarterly_settlements SET status = 'FINALIZED', "
                "finalized_at = CURRENT_TIMESTAMP WHERE id = 3"
            )
        assert connection.execute(
            "SELECT previous_settlement_id, status, finalized_at "
            "FROM quarterly_settlements WHERE id = 3"
        ).fetchone() == (None, "DRAFT", None)
        connection.execute(
            "UPDATE quarterly_settlements SET previous_settlement_id = 1 WHERE id = 3"
        )

        replacement["id"] = 4
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT INTO quarterly_settlements ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                tuple(replacement[name] for name in columns),
            )

        with pytest.raises(
            sqlite3.IntegrityError, match="settlement_replacement_identity_immutable"
        ):
            connection.execute(
                "UPDATE quarterly_settlements SET year = 2027 WHERE id = 3"
            )
        assert connection.execute(
            "SELECT year, status FROM quarterly_settlements WHERE id = 3"
        ).fetchone() == (2026, "DRAFT")

        parent_trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_settlement_parent_financial_lock'"
        ).fetchone()[0]
        finalize_trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' "
            "AND name='trg_settlement_validate_finalize'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER trg_settlement_parent_financial_lock")
        connection.execute("UPDATE quarterly_settlements SET year = 2027 WHERE id = 3")
        connection.execute(parent_trigger_sql)
        connection.execute("DROP TRIGGER trg_settlement_validate_finalize")
        try:
            with pytest.raises(
                sqlite3.IntegrityError,
                match="settlement_replacement_invalid_at_finalize",
            ):
                connection.execute(
                    "UPDATE quarterly_settlements SET status = 'FINALIZED', "
                    "finalized_at = CURRENT_TIMESTAMP WHERE id = 3"
                )
        finally:
            connection.execute(finalize_trigger_sql)
        assert connection.execute(
            "SELECT year, status, finalized_at FROM quarterly_settlements WHERE id = 3"
        ).fetchone() == (2027, "DRAFT", None)


def test_0_2_14_upgrade_refuses_foreign_keys_on_before_any_ddl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, alembic_config = _prepare_9d_history(tmp_path, monkeypatch, "fk-on")
    schema_before = _schema_snapshot(settings.database_path)

    def enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    event.listen(Engine, "connect", enable_foreign_keys)
    try:
        with pytest.raises(RuntimeError, match="foreign_keys=OFF"):
            command.upgrade(alembic_config, "head")
    finally:
        event.remove(Engine, "connect", enable_foreign_keys)

    assert _schema_snapshot(settings.database_path) == schema_before
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "9d2f6a8c4b13"


def test_0_2_14_mid_ddl_failure_rolls_back_tables_triggers_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, alembic_config = _prepare_9d_history(tmp_path, monkeypatch, "ddl-rollback")
    schema_before = _schema_snapshot(settings.database_path)

    def fail_after_copy(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = " ".join(statement.upper().replace('"', "").split())
        if normalized == "DROP TABLE QUARTERLY_SETTLEMENTS":
            raise RuntimeError("synthetic mid-DDL failure")

    event.listen(Engine, "before_cursor_execute", fail_after_copy)
    try:
        with pytest.raises(RuntimeError, match="synthetic mid-DDL failure"):
            command.upgrade(alembic_config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail_after_copy)

    assert _schema_snapshot(settings.database_path) == schema_before
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "9d2f6a8c4b13"
        assert connection.execute(
            "SELECT id, previous_settlement_id FROM quarterly_settlements ORDER BY id"
        ).fetchall() == [(1, None), (2, 1)]
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE '\\_%\\_0214' ESCAPE '\\'"
        ).fetchone()[0] == 0


def test_0_2_14_acquires_writer_lock_before_reading_legacy_preflight_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, alembic_config = _prepare_9d_history(tmp_path, monkeypatch, "preflight-lock")
    state = {"begin_seen": False, "write_probe_done": False, "write_error": ""}

    def probe_competing_writer(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        normalized = " ".join(statement.upper().replace('"', "").split())
        if normalized == "BEGIN IMMEDIATE":
            state["begin_seen"] = True
            return
        if (
            state["begin_seen"]
            and not state["write_probe_done"]
            and "SELECT VERSION_NUM FROM ALEMBIC_VERSION" in normalized
        ):
            state["write_probe_done"] = True
            try:
                with sqlite3.connect(settings.database_path, timeout=0) as competing:
                    competing.execute(
                        "INSERT INTO app_settings(key, value) VALUES ('legacy-race', 'unsafe')"
                    )
                    competing.commit()
            except sqlite3.OperationalError as exc:
                state["write_error"] = str(exc)

    event.listen(Engine, "before_cursor_execute", probe_competing_writer)
    try:
        command.upgrade(alembic_config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", probe_competing_writer)

    assert state["begin_seen"] is True
    assert state["write_probe_done"] is True
    assert "locked" in state["write_error"].casefold()
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM app_settings WHERE key = 'legacy-race'"
        ).fetchone()[0] == 0
