from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.database import SessionLocal, settings
from app.main import app
from app.models import (
    Attachment,
    AuditEvent,
    Invoice,
    InvoiceAdjustment,
    InvoiceCorrection,
    PaymentAllocation,
)
from app.serializers import invoice_accounting_cents


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def test_open_correction_pending_entries_are_not_accounting_effective() -> None:
    correction = InvoiceCorrection(id=91, status="OPEN", reason="pending", original_invoice_id=1)
    replacement = Invoice(id=92, amount_cents=10_000)
    replacement.payment_allocations = [
        PaymentAllocation(
            id=93,
            payment_id=1,
            invoice_id=92,
            amount_cents=7_000,
            entry_type="APPLY",
            correction_id=91,
            correction=correction,
        )
    ]
    replacement.adjustments = [
        InvoiceAdjustment(
            id=94,
            invoice_id=92,
            correction_id=91,
            amount_cents=3_000,
            adjustment_type="COMPANY_BORNE_DIFFERENCE",
            reason="pending difference",
            correction=correction,
        )
    ]

    assert invoice_accounting_cents(replacement) == (0, 0, 10_000)
    correction.status = "COMPLETED"
    assert invoice_accounting_cents(replacement) == (7_000, 3_000, 0)


def _create(client: TestClient, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    assert response.status_code in {200, 201}, response.text
    return response.json()


def _linked_proof(client: TestClient, entity_type: str, entity_id: int, marker: str) -> int:
    response = client.post(
        "/api/attachments",
        data={"entity_type": entity_type, "entity_id": str(entity_id)},
        files={"file": (f"{marker}.pdf", f"%PDF-1.4\n{marker}\n%%EOF".encode(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _unclaimed_proof(client: TestClient, entity_type: str, marker: str) -> int:
    response = client.post(
        "/api/attachments",
        data={"entity_type": entity_type},
        files={"file": (f"{marker}.pdf", f"%PDF-1.4\n{marker}\n%%EOF".encode(), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    assert response.json()["entity_id"] is None
    return response.json()["id"]


def _case(client: TestClient, label: str) -> dict:
    suffix = f"{label}{uuid4().hex[:6]}"
    company = _create(
        client,
        "/api/companies",
        {
            "name": f"Payment {suffix} Limited",
            "code": f"C{suffix}",
            "payment_terms_days": 14,
            "bank_information": "Test bank",
        },
    )
    fc = _create(
        client,
        "/api/fcs",
        {"company_id": company["id"], "name": f"FC {suffix}", "code": f"F{suffix}"},
    )
    platform = _create(
        client,
        "/api/platforms",
        {"name": f"Platform {suffix}", "code": f"P{suffix}"},
    )
    plan = _create(
        client,
        "/api/fee-plans",
        {
            "company_id": company["id"],
            "name": f"Plan {suffix}",
            "code": f"FP{suffix}",
            "fee_rate_percent": "20.00",
        },
    )
    customer = _create(
        client,
        "/api/clients",
        {
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"Client {suffix}",
            "management_start_date": "2026-01-01",
            "status": "ACTIVE",
        },
    )
    account = _create(
        client,
        "/api/accounts",
        {
            "client_id": customer["id"],
            "platform_id": platform["id"],
            "fee_plan_id": plan["id"],
            "account_number": f"A-{suffix}",
            "start_date": "2026-01-01",
            "status": "ACTIVE",
        },
    )
    beginning = _create(
        client,
        "/api/balance-snapshots",
        {
            "account_id": account["id"],
            "as_of_date": "2026-01-01",
            "total_balance": "1000.00",
            "eligible_for_closing": False,
        },
    )
    closing = _create(
        client,
        "/api/balance-snapshots",
        {
            "account_id": account["id"],
            "as_of_date": "2026-03-31",
            "total_balance": "1600.00",
            "eligible_for_closing": True,
        },
    )
    _linked_proof(client, "SNAPSHOT", beginning["id"], f"begin-{suffix}")
    _linked_proof(client, "SNAPSHOT", closing["id"], f"close-{suffix}")
    return {
        "company": company,
        "fc": fc,
        "platform": platform,
        "plan": plan,
        "client": customer,
        "account": account,
        "beginning": beginning,
        "closing": closing,
    }


def _calculate(client: TestClient, data: dict, *, original_hwm: str) -> dict:
    return _create(
        client,
        "/api/settlements/calculate",
        {
            "client_id": data["client"]["id"],
            "platform_id": data["platform"]["id"],
            "fee_plan_id": data["plan"]["id"],
            "year": 2026,
            "quarter": 1,
            "start_date": "2026-01-01",
            "closing_date": "2026-03-31",
            "account_lines": [
                {
                    "account_id": data["account"]["id"],
                    "beginning_snapshot_id": data["beginning"]["id"],
                    "closing_snapshot_id": data["closing"]["id"],
                    "original_hwm": original_hwm,
                }
            ],
        },
    )


def _finalize(client: TestClient, settlement: dict) -> dict:
    return _create(client, f"/api/settlements/{settlement['id']}/finalize", {})


def _issue(client: TestClient, data: dict) -> dict:
    draft = _create(
        client,
        "/api/invoices",
        {
            "client_id": data["client"]["id"],
            "year": 2026,
            "quarter": 1,
            "fee_plan_id": data["plan"]["id"],
            "language": "zh",
        },
    )
    return _create(
        client,
        f"/api/invoices/{draft['id']}/issue",
        {"issue_date": "2026-04-05", "language": "zh"},
    )


def _issued_case(client: TestClient, label: str) -> tuple[dict, dict, dict]:
    data = _case(client, label)
    settlement = _calculate(client, data, original_hwm="1000.00")
    assert settlement["service_fee"] == "120.00"
    _finalize(client, settlement)
    return data, settlement, _issue(client, data)


def _invoice(client: TestClient, invoice_id: int) -> dict:
    response = client.get("/api/invoices")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["id"] == invoice_id)


def test_one_time_payment_requires_intact_proof_and_separates_company_difference() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, invoice = _issued_case(client, "PAY")
        blank_void_reason = client.post(
            f"/api/invoices/{invoice['id']}/void", json={"reason": "   "}
        )
        assert blank_void_reason.status_code == 422

        missing = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={"payment_date": "2026-04-10", "amount": "120.00", "method": "BANK_TRANSFER"},
        )
        assert missing.status_code == 422

        tampered_proof = _unclaimed_proof(client, "PAYMENT", "tampered-payment")
        with SessionLocal() as db:
            attachment = db.get(Attachment, tampered_proof)
            assert attachment is not None
            tampered_path = Path(attachment.stored_path)
            original_proof = tampered_path.read_bytes()
            tampered_path.write_bytes(b"tampered")
        tampered = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={
                "payment_date": "2026-04-10",
                "amount": "120.00",
                "method": "BANK_TRANSFER",
                "proof_attachment_id": tampered_proof,
            },
        )
        assert tampered.status_code == 400
        assert "SHA-256" in tampered.json()["detail"] or "大小" in tampered.json()["detail"]
        tampered_path.write_bytes(original_proof)

        proof_id = _unclaimed_proof(client, "PAYMENT", "valid-payment")
        blank_payment_method = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={
                "payment_date": "2026-04-10",
                "amount": "120.00",
                "method": "   ",
                "proof_attachment_id": proof_id,
            },
        )
        assert blank_payment_method.status_code == 422

        reason_without_difference = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={
                "payment_date": "2026-04-10",
                "amount": "120.00",
                "method": "BANK_TRANSFER",
                "proof_attachment_id": proof_id,
                "difference_reason": "Reason without a company difference",
            },
        )
        assert reason_without_difference.status_code == 422

        partial = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={
                "payment_date": "2026-04-10",
                "amount": "100.00",
                "method": "BANK_TRANSFER",
                "proof_attachment_id": proof_id,
            },
        )
        assert partial.status_code == 400
        assert "部分付款" in partial.json()["detail"]

        paid = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={
                "payment_date": "2026-04-10",
                "amount": "100.00",
                "method": "BANK_TRANSFER",
                "proof_attachment_id": proof_id,
                "company_difference": "20.00",
                "difference_reason": "Company absorbs rounding difference",
            },
        )
        assert paid.status_code == 201, paid.text
        assert paid.json()["payment_status"] == "PAID"
        assert paid.json()["paid_amount"] == "100.00"
        assert paid.json()["adjustment_amount"] == "20.00"
        assert paid.json()["outstanding_amount"] == "0.00"
        assert paid.json()["payments"][0]["amount"] == "100.00"

        duplicate = client.post(
            f"/api/invoices/{invoice['id']}/payments",
            json={
                "payment_date": "2026-04-11",
                "amount": "120.00",
                "method": "BANK_TRANSFER",
                "proof_attachment_id": tampered_proof,
            },
        )
        assert duplicate.status_code == 409


def test_paid_invoice_correction_refunds_difference_and_preserves_original_archive() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, original_settlement, original_invoice = _issued_case(client, "CORR")
        proof_id = _unclaimed_proof(client, "PAYMENT", "correction-payment")
        paid = client.post(
            f"/api/invoices/{original_invoice['id']}/payments",
            json={
                "payment_date": "2026-04-10",
                "amount": "120.00",
                "method": "BANK_TRANSFER",
                "proof_attachment_id": proof_id,
            },
        )
        assert paid.status_code == 201, paid.text
        payment_id = paid.json()["payments"][0]["id"]
        archived_before = client.post(
            f"/api/invoices/{original_invoice['id']}/pdf?language=zh"
        )
        assert archived_before.status_code == 200

        blank_correction_reason = client.post(
            f"/api/invoices/{original_invoice['id']}/corrections",
            json={"reason": "   "},
        )
        assert blank_correction_reason.status_code == 422

        opened = client.post(
            f"/api/invoices/{original_invoice['id']}/corrections",
            json={"reason": "Correct overstated source balance"},
        )
        assert opened.status_code == 201, opened.text
        correction_id = opened.json()["id"]
        assert opened.json()["status"] == "OPEN"
        assert _invoice(client, original_invoice["id"])["lifecycle_status"] == "VOID"
        assert _invoice(client, original_invoice["id"])["paid_amount"] == "0.00"

        invalid_refund_proof = _unclaimed_proof(
            client, "PAYMENT_REFUND", "direct-invalid-refund-method"
        )
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            for invalid_method in ("   ", "x" * 81):
                with pytest.raises(
                    sqlite3.IntegrityError,
                    match="payment_refund_method_invalid",
                ):
                    connection.execute(
                        """
                        INSERT INTO payment_refunds (
                            payment_id, correction_id, refund_date, amount_cents,
                            method, reason, proof_attachment_id, created_at, updated_at
                        ) VALUES (
                            ?, ?, '2026-04-15', 2000, ?,
                            'Direct SQL invalid refund method', ?,
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        """,
                        (payment_id, correction_id, invalid_method, invalid_refund_proof),
                    )
            assert connection.execute(
                "SELECT entity_id FROM attachments WHERE id = ?",
                (invalid_refund_proof,),
            ).fetchone()[0] is None

        stale_reopen = client.post(
            "/api/invoices",
            json={
                "client_id": data["client"]["id"],
                "year": 2026,
                "quarter": 1,
                "fee_plan_id": data["plan"]["id"],
                "language": "zh",
            },
        )
        assert stale_reopen.status_code == 409
        assert "替代版本链" in stale_reopen.json()["detail"]

        voided = client.post(
            f"/api/settlements/{original_settlement['id']}/void",
            json={"reason": "Correct source balance"},
        )
        assert voided.status_code == 200, voided.text
        replacement_settlement = _calculate(client, data, original_hwm="1100.00")
        assert replacement_settlement["version_no"] == 2
        assert replacement_settlement["replaces_settlement_id"] == original_settlement["id"]
        assert replacement_settlement["service_fee"] == "100.00"
        _finalize(client, replacement_settlement)
        replacement_invoice = _issue(client, data)

        # The issued replacement must remain financially blank until the OPEN
        # correction atomically transfers retained cash/refunds into it.
        pending_proof = _unclaimed_proof(client, "PAYMENT", "pending-replacement-payment")
        premature_payment = client.post(
            f"/api/invoices/{replacement_invoice['id']}/payments",
            json={
                "payment_date": "2026-04-14",
                "amount": "100.00",
                "method": "BANK_TRANSFER",
                "proof_attachment_id": pending_proof,
            },
        )
        assert premature_payment.status_code == 409
        assert "待关联替代单" in premature_payment.json()["detail"]
        premature_nested_correction = client.post(
            f"/api/invoices/{replacement_invoice['id']}/corrections",
            json={"reason": "Must finish the open correction first"},
        )
        assert premature_nested_correction.status_code == 409
        assert "待关联替代单" in premature_nested_correction.json()["detail"]

        wrong = client.post(
            f"/api/invoice-corrections/{correction_id}/complete",
            json={
                "replacement_invoice_id": original_invoice["id"],
                "retained_allocations": [{"payment_id": payment_id, "amount": "100.00"}],
                "refunds": [],
            },
        )
        assert wrong.status_code == 409

        refund_proof = _unclaimed_proof(client, "PAYMENT_REFUND", "correction-refund")
        blank_refund_method = client.post(
            f"/api/invoice-corrections/{correction_id}/complete",
            json={
                "replacement_invoice_id": replacement_invoice["id"],
                "retained_allocations": [{"payment_id": payment_id, "amount": "100.00"}],
                "refunds": [
                    {
                        "payment_id": payment_id,
                        "refund_date": "2026-04-15",
                        "amount": "20.00",
                        "method": "   ",
                        "proof_attachment_id": refund_proof,
                        "reason": "Refund overpayment after corrected invoice",
                    }
                ],
            },
        )
        assert blank_refund_method.status_code == 422

        blank_refund_reason = client.post(
            f"/api/invoice-corrections/{correction_id}/complete",
            json={
                "replacement_invoice_id": replacement_invoice["id"],
                "retained_allocations": [{"payment_id": payment_id, "amount": "100.00"}],
                "refunds": [
                    {
                        "payment_id": payment_id,
                        "refund_date": "2026-04-15",
                        "amount": "20.00",
                        "method": "BANK_TRANSFER",
                        "proof_attachment_id": refund_proof,
                        "reason": "   ",
                    }
                ],
            },
        )
        assert blank_refund_reason.status_code == 422

        completed = client.post(
            f"/api/invoice-corrections/{correction_id}/complete",
            json={
                "replacement_invoice_id": replacement_invoice["id"],
                "retained_allocations": [{"payment_id": payment_id, "amount": "100.00"}],
                "refunds": [
                    {
                        "payment_id": payment_id,
                        "refund_date": "2026-04-15",
                        "amount": "20.00",
                        "method": "BANK_TRANSFER",
                        "proof_attachment_id": refund_proof,
                        "reason": "Refund overpayment after corrected invoice",
                    }
                ],
            },
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "COMPLETED"
        assert completed.json()["replacement_invoice"]["id"] == replacement_invoice["id"]
        assert completed.json()["refunds"][0]["amount"] == "20.00"
        replacement = _invoice(client, replacement_invoice["id"])
        assert replacement["payment_status"] == "PAID"
        assert replacement["paid_amount"] == "100.00"
        assert replacement["adjustment_amount"] == "0.00"
        blocked_direct_void = client.post(
            f"/api/invoices/{replacement_invoice['id']}/void",
            json={"reason": "Transferred cash must use correction"},
        )
        assert blocked_direct_void.status_code == 409

        opened_v2 = client.post(
            f"/api/invoices/{replacement_invoice['id']}/corrections",
            json={"reason": "Second correction in payment chain"},
        )
        assert opened_v2.status_code == 201, opened_v2.text
        assert opened_v2.json()["payments"][0]["id"] == payment_id
        assert opened_v2.json()["payments"][0]["amount"] == "100.00"
        assert client.post(
            f"/api/settlements/{replacement_settlement['id']}/void",
            json={"reason": "Replace v2 after second correction"},
        ).status_code == 200
        v3_settlement = _calculate(client, data, original_hwm="1150.00")
        assert (v3_settlement["version_no"], v3_settlement["replaces_settlement_id"]) == (
            3,
            replacement_settlement["id"],
        )
        assert v3_settlement["service_fee"] == "90.00"
        _finalize(client, v3_settlement)
        v3_invoice = _issue(client, data)
        second_refund_proof = _unclaimed_proof(client, "PAYMENT_REFUND", "second-refund")
        completed_v2 = client.post(
            f"/api/invoice-corrections/{opened_v2.json()['id']}/complete",
            json={
                "replacement_invoice_id": v3_invoice["id"],
                "retained_allocations": [{"payment_id": payment_id, "amount": "90.00"}],
                "refunds": [
                    {
                        "payment_id": payment_id,
                        "refund_date": "2026-04-20",
                        "amount": "10.00",
                        "method": "BANK_TRANSFER",
                        "proof_attachment_id": second_refund_proof,
                        "reason": "Second correction refund",
                    }
                ],
            },
        )
        assert completed_v2.status_code == 200, completed_v2.text
        assert _invoice(client, v3_invoice["id"])["paid_amount"] == "90.00"
        archived_after = client.post(
            f"/api/invoices/{original_invoice['id']}/pdf?language=zh"
        )
        assert archived_after.status_code == 200
        assert archived_after.content == archived_before.content

        with SessionLocal() as db:
            missing_entity_audits = db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action.in_(
                        [
                            "PAYMENT_RECORDED",
                            "INVOICE_CORRECTION_OPENED",
                            "PAYMENT_REFUNDED",
                        ]
                    ),
                    AuditEvent.entity_id.is_(None),
                )
            ).all()
            assert missing_entity_audits == []


def test_unpaid_correction_can_complete_without_forcing_payment_and_versions_reach_v3() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, v1, invoice_v1 = _issued_case(client, "VERS")
        correction_v1 = _create(
            client,
            f"/api/invoices/{invoice_v1['id']}/corrections",
            {"reason": "Replace unpaid invoice source"},
        )
        assert client.post(
            f"/api/settlements/{v1['id']}/void", json={"reason": "Replace v1"}
        ).status_code == 200
        v2 = _calculate(client, data, original_hwm="1000.00")
        assert (v2["version_no"], v2["replaces_settlement_id"]) == (2, v1["id"])
        _finalize(client, v2)
        invoice_v2 = _issue(client, data)
        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "UPDATE invoice_corrections SET replacement_invoice_id = ? WHERE id = ?",
                (invoice_v2["id"], correction_v1["id"]),
            )
            with pytest.raises(sqlite3.IntegrityError, match="invoice_adjustment_invalid"):
                connection.execute(
                    """
                    INSERT INTO invoice_adjustments (
                        invoice_id, correction_id, payment_id, adjustment_type,
                        amount_cents, reason, created_at, updated_at
                    ) VALUES (?, ?, NULL, 'COMPANY_BORNE_DIFFERENCE', ?, ?,
                              CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        invoice_v2["id"],
                        correction_v1["id"],
                        12_000,
                        "Illegal no-payment correction difference",
                    ),
                )

            adjustment_trigger_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_invoice_adjustment_validate_insert'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER trg_invoice_adjustment_validate_insert")
            connection.execute(
                """
                INSERT INTO invoice_adjustments (
                    invoice_id, correction_id, payment_id, adjustment_type,
                    amount_cents, reason, created_at, updated_at
                ) VALUES (?, ?, NULL, 'COMPANY_BORNE_DIFFERENCE', ?, ?,
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    invoice_v2["id"],
                    correction_v1["id"],
                    12_000,
                    "Synthetic pre-existing invalid difference",
                ),
            )
            connection.execute(adjustment_trigger_sql)
            with pytest.raises(
                sqlite3.IntegrityError,
                match="invoice_correction_blank_replacement_ledger_required",
            ):
                connection.execute(
                    "UPDATE invoice_corrections SET status = 'COMPLETED', "
                    "completed_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (correction_v1["id"],),
                )
            connection.rollback()
            assert connection.execute(
                "SELECT replacement_invoice_id, status FROM invoice_corrections WHERE id = ?",
                (correction_v1["id"],),
            ).fetchone() == (None, "OPEN")
            assert connection.execute(
                "SELECT COUNT(*) FROM invoice_adjustments WHERE invoice_id = ?",
                (invoice_v2["id"],),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' "
                "AND name = 'trg_invoice_adjustment_validate_insert'"
            ).fetchone()[0] == 1
        completed = client.post(
            f"/api/invoice-corrections/{correction_v1['id']}/complete",
            json={"replacement_invoice_id": invoice_v2["id"]},
        )
        assert completed.status_code == 200, completed.text
        assert _invoice(client, invoice_v2["id"])["payment_status"] == "UNPAID"

        correction_v2 = _create(
            client,
            f"/api/invoices/{invoice_v2['id']}/corrections",
            {"reason": "Replace unpaid invoice source again"},
        )
        assert correction_v2["status"] == "OPEN"
        assert client.post(
            f"/api/settlements/{v2['id']}/void", json={"reason": "Replace v2"}
        ).status_code == 200
        v3 = _calculate(client, data, original_hwm="1000.00")
        assert (v3["version_no"], v3["replaces_settlement_id"]) == (3, v2["id"])


def test_open_correction_linked_replacement_cannot_be_voided_by_direct_sql() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, v1, invoice_v1 = _issued_case(client, "VOIDLINK")
        correction = _create(
            client,
            f"/api/invoices/{invoice_v1['id']}/corrections",
            {"reason": "Link replacement before completing correction"},
        )
        assert client.post(
            f"/api/settlements/{v1['id']}/void", json={"reason": "Create replacement"}
        ).status_code == 200
        v2 = _calculate(client, data, original_hwm="1000.00")
        _finalize(client, v2)
        invoice_v2 = _issue(client, data)

        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "UPDATE invoice_corrections SET replacement_invoice_id = ? WHERE id = ?",
                (invoice_v2["id"], correction["id"]),
            )
            connection.commit()

        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with pytest.raises(
                sqlite3.IntegrityError,
                match="invoice_open_correction_replacement_cannot_void",
            ):
                connection.execute(
                    "UPDATE invoices SET lifecycle_status = 'VOID', "
                    "voided_at = CURRENT_TIMESTAMP, "
                    "void_reason = 'Direct SQL bypass' WHERE id = ?",
                    (invoice_v2["id"],),
                )
            connection.rollback()
            assert connection.execute(
                "SELECT lifecycle_status FROM invoices WHERE id = ?",
                (invoice_v2["id"],),
            ).fetchone()[0] == "ISSUED"


def test_invoice_void_requires_and_freezes_audit_metadata_in_direct_sql() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, invoice = _issued_case(client, "VOIDMETA")

        with sqlite3.connect(settings.database_path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            with pytest.raises(sqlite3.IntegrityError, match="invoice_void_metadata_invalid"):
                connection.execute(
                    "UPDATE invoices SET lifecycle_status = 'VOID' WHERE id = ?",
                    (invoice["id"],),
                )
            with pytest.raises(sqlite3.IntegrityError, match="invoice_void_metadata_invalid"):
                connection.execute(
                    "UPDATE invoices SET lifecycle_status = 'VOID', "
                    "voided_at = CURRENT_TIMESTAMP, void_reason = 'x' WHERE id = ?",
                    (invoice["id"],),
                )
            with pytest.raises(sqlite3.IntegrityError, match="invoice_void_metadata_invalid"):
                connection.execute(
                    "UPDATE invoices SET lifecycle_status = 'VOID', "
                    "voided_at = CURRENT_TIMESTAMP, void_reason = ? WHERE id = ?",
                    ("x" * 501, invoice["id"]),
                )

            connection.execute(
                "UPDATE invoices SET lifecycle_status = 'VOID', "
                "voided_at = CURRENT_TIMESTAMP, void_reason = ? WHERE id = ?",
                ("Controlled direct SQL void", invoice["id"]),
            )
            voided_at, void_reason = connection.execute(
                "SELECT voided_at, void_reason FROM invoices WHERE id = ?",
                (invoice["id"],),
            ).fetchone()
            with pytest.raises(sqlite3.IntegrityError, match="invoice_void_metadata_immutable"):
                connection.execute(
                    "UPDATE invoices SET void_reason = 'Changed after void' WHERE id = ?",
                    (invoice["id"],),
                )
            with pytest.raises(sqlite3.IntegrityError, match="invoice_void_metadata_immutable"):
                connection.execute(
                    "UPDATE invoices SET voided_at = '2099-01-01 00:00:00' WHERE id = ?",
                    (invoice["id"],),
                )
            assert connection.execute(
                "SELECT lifecycle_status, voided_at, void_reason FROM invoices WHERE id = ?",
                (invoice["id"],),
            ).fetchone() == ("VOID", voided_at, void_reason)


def test_concurrent_payment_confirmation_creates_only_one_payment() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, invoice = _issued_case(client, "RACE")
        proof_ids = [
            _unclaimed_proof(client, "PAYMENT", "race-payment-a"),
            _unclaimed_proof(client, "PAYMENT", "race-payment-b"),
        ]

        def confirm(proof_id: int) -> int:
            return client.post(
                f"/api/invoices/{invoice['id']}/payments",
                json={
                    "payment_date": "2026-04-10",
                    "amount": "120.00",
                    "method": "BANK_TRANSFER",
                    "proof_attachment_id": proof_id,
                },
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(confirm, proof_ids))
        assert sorted(statuses) == [201, 409]
        assert len(_invoice(client, invoice["id"])["payments"]) == 1
