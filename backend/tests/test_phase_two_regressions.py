from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import Attachment, Payment
from test_payment_corrections import (
    WRITE_HEADERS, _case, _calculate, _create, _finalize, _issue,
    _linked_proof, _unclaimed_proof, _invoice,
)


def _snapshot(client, account_id, day, balance):
    snapshot = _create(client, "/api/balance-snapshots", {
        "account_id": account_id, "as_of_date": day, "total_balance": balance,
        "eligible_for_closing": day.endswith(("03-31", "06-30", "09-30", "12-31")),
    })
    _linked_proof(client, "SNAPSHOT", snapshot["id"], f"snapshot-{snapshot['id']}")
    return snapshot


def _period_payload(data, *, year=2026, quarter=2, closing, beginning=None):
    line = {"account_id": data["account"]["id"], "closing_snapshot_id": closing["id"]}
    if beginning:
        line.update(beginning_snapshot_id=beginning["id"], original_hwm=beginning["total_balance"])
    return {
        "client_id": data["client"]["id"], "platform_id": data["platform"]["id"],
        "fee_plan_id": data["plan"]["id"], "year": year, "quarter": quarter,
        "account_lines": [line],
    }


@pytest.mark.parametrize("kind", ["CONTRIBUTION", "WITHDRAWAL"])
def test_first_day_used_cash_requires_intact_physical_evidence(kind):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _case(client, "FIRST")
        account_id = data["account"]["id"]
        closing = _snapshot(client, account_id, "2026-06-30", "1800.00")
        transaction = _create(client, "/api/transactions", {
            "account_id": account_id, "transaction_date": "2026-04-01",
            "transaction_type": kind, "amount": "100.00",
        })
        proof = _linked_proof(client, "TRANSACTION", transaction["id"], "first-day")
        draft = _create(client, "/api/settlements/calculate", _period_payload(data, closing=closing, beginning=data["closing"]))
        with SessionLocal() as db:
            path = Path(db.get(Attachment, proof).stored_path)
        original = path.read_bytes()
        try:
            path.write_bytes(b"damaged synthetic proof")
            response = client.post(f"/api/settlements/{draft['id']}/finalize", json={})
            assert response.status_code == 400, response.text
            assert "2026-04-01" in response.json()["detail"]
        finally:
            path.write_bytes(original)
        _finalize(client, draft)


def test_same_day_opening_snapshot_does_not_require_excluded_cash_evidence():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _case(client, "SAME")
        account_id = data["account"]["id"]
        beginning = _snapshot(client, account_id, "2026-04-01", "1600.00")
        closing = _snapshot(client, account_id, "2026-06-30", "1800.00")
        _create(client, "/api/transactions", {"account_id": account_id, "transaction_date": "2026-04-01", "transaction_type": "CONTRIBUTION", "amount": "100.00"})
        draft = _create(client, "/api/settlements/calculate", _period_payload(data, closing=closing, beginning=beginning))
        assert draft["contribution"] == "0.00"
        _finalize(client, draft)


def test_missing_q2_must_be_finalized_before_q3():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _case(client, "GAP")
        _finalize(client, _calculate(client, data, original_hwm="1000.00"))
        q3_snapshot = _snapshot(client, data["account"]["id"], "2026-09-30", "2000.00")
        q3 = _period_payload(data, quarter=3, closing=q3_snapshot)
        rejected = client.post("/api/settlements/calculate", json=q3)
        assert rejected.status_code == 409, rejected.text
        assert "2026 Q2" in rejected.json()["detail"]
        q2_snapshot = _snapshot(client, data["account"]["id"], "2026-06-30", "1800.00")
        q2 = _create(client, "/api/settlements/calculate", _period_payload(data, closing=q2_snapshot))
        assert client.post("/api/settlements/calculate", json=q3).status_code == 409
        _finalize(client, q2)
        q3_draft = _create(client, "/api/settlements/calculate", q3)
        assert q3_draft["account_lines"][0]["previous_line_id"] == q2["account_lines"][0]["id"]
        _finalize(client, q3_draft)


def _new_platform(client, data):
    platform = _create(client, "/api/platforms", {"name": f"Late {data['account']['id']}", "code": f"L{data['account']['id']}"})
    account = _create(client, "/api/accounts", {
        "client_id": data["client"]["id"], "platform_id": platform["id"],
        "fee_plan_id": data["plan"]["id"], "account_number": f"LATE-{data['account']['id']}",
        "start_date": "2026-01-01", "status": "ACTIVE",
    })
    return {**data, "platform": platform, "account": account,
            "beginning": _snapshot(client, account["id"], "2026-01-01", "1000.00"),
            "closing": _snapshot(client, account["id"], "2026-03-31", "1100.00")}


@pytest.mark.parametrize("cash,original_hwm,difference", [("120.00", "1000.00", "0.00"), ("100.00", "1100.00", "20.00")])
def test_late_platform_correction_keeps_cash_and_company_difference_separate(cash, original_hwm, difference):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _case(client, "LATE")
        original_settlement = _calculate(client, data, original_hwm=original_hwm)
        _finalize(client, original_settlement)
        original = _issue(client, data)
        paid = _create(client, f"/api/invoices/{original['id']}/payments", {
            "payment_date": "2026-04-10", "amount": cash, "method": "BANK_TRANSFER",
            "proof_attachment_id": _unclaimed_proof(client, "PAYMENT", f"late-{original['id']}"),
        })
        payment_id = paid["payments"][0]["id"]
        late = _new_platform(client, data)
        _finalize(client, _calculate(client, late, original_hwm="1000.00"))
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {"reason": "公司计算失误，补齐遗漏平台"})
        # Original sources must still be replaced; adding a platform alone cannot bypass that rule.
        body = {"client_id": data["client"]["id"], "year": 2026, "quarter": 1, "fee_plan_id": data["plan"]["id"], "language": "zh"}
        assert client.post("/api/invoices", json=body).status_code == 409
        _create(client, f"/api/settlements/{original_settlement['id']}/void", {"reason": "公司核算更正"})
        _finalize(client, _calculate(client, data, original_hwm="1100.00"))
        replacement = _issue(client, data)
        assert replacement["amount"] == "120.00"
        assert replacement["source_count"] == 2
        completed = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {
            "replacement_invoice_id": replacement["id"],
            "retained_allocations": [{"payment_id": payment_id, "amount": cash}],
            "company_difference": difference,
            "difference_reason": "公司计算失误，差额由公司承担，客户无需补款" if difference != "0.00" else None,
        })
        assert completed["status"] == "COMPLETED"
        current = _invoice(client, replacement["id"])
        assert current["paid_amount"] == cash
        assert current["payment_status"] == "PAID"
        assert current["adjustment_amount"] == difference
        assert current["payments"][0]["id"] == payment_id
        with SessionLocal() as db:
            assert db.scalar(select(Payment.id).where(Payment.invoice_id == replacement["id"])) is None
        old = _invoice(client, original["id"])
        assert old["payments"][0]["id"] == payment_id
        assert old["invoice_number"] == original["invoice_number"]


def test_invoice_detail_supports_reconciliation_after_a_lost_write_response():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _case(client, "DETAIL")
        _finalize(client, _calculate(client, data, original_hwm="1000.00"))
        invoice = _issue(client, data)
        response = client.get(f"/api/invoices/{invoice['id']}")
        assert response.status_code == 200
        assert response.json() == _invoice(client, invoice["id"])
        assert client.get("/api/invoices/999999999").status_code == 404
