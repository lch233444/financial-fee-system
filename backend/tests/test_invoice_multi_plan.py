"""Synthetic multi-client / multi-plan workflow; never uses customer statements."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import sqlite3

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.config import get_settings
from test_invoice_aggregation import WRITE_HEADERS, _group, _finalized_settlement, _draft, _pdf_text
from test_payment_corrections import _create, _unclaimed_proof


def _second_plan(client, data, suffix, rate="10.00"):
    plan = _create(client, "/api/fee-plans", {"company_id": data["company"]["id"], "name": f"Profit {rate} {suffix}", "code": f"P2{suffix}", "fee_rate_percent": rate})
    account = _create(client, "/api/accounts", {
        "client_id": data["client"]["id"], "platform_id": data["platforms"][0]["id"], "fee_plan_id": plan["id"],
        "account_number": f"SECOND-{suffix}", "start_date": "2026-01-01", "status": "ACTIVE",
    })
    return {**data, "plan": plan, "accounts": [account], "platforms": [data["platforms"][0]]}


def _issue_draft(client, draft):
    assert draft.status_code == 201, draft.text
    return _create(client, f"/api/invoices/{draft.json()['id']}/issue", {"issue_date": "2026-04-10"})


def test_one_bill_combines_same_platform_different_rates_with_exact_account_fees_and_client_isolation():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        first = _group(client, "MULTI", platform_count=1)
        second = _second_plan(client, first, "MULTI")
        other = _group(client, "OTHERCLIENT", platform_count=1)
        s1 = _finalized_settlement(client, first, 0, year=2026, quarter=1, closing="1100.03")
        s2 = _finalized_settlement(client, second, 0, year=2026, quarter=1, closing="1300.05")
        s3 = _finalized_settlement(client, other, 0, year=2026, quarter=1, closing="2000.00")
        assert s1["service_fee"] == "20.01" and s2["service_fee"] == "30.01"
        # An old API caller sending one plan must still receive the complete bill.
        draft = _draft(client, first, year=2026, quarter=1)
        assert draft.status_code == 201, draft.text
        body = draft.json()
        assert body["amount"] == "50.02"
        assert set(body["settlement_ids"]) == {s1["id"], s2["id"]}
        assert s3["id"] not in body["settlement_ids"]
        assert set(body["fee_plan_ids"]) == {first["plan"]["id"], second["plan"]["id"]}
        assert {line["fee_rate_percent"] for line in body["account_lines"]} == {10, 20}
        assert {line["service_fee"] for line in body["account_lines"]} == {"20.01", "30.01"}
        assert _draft(client, second, year=2026, quarter=1).status_code == 409
        issued = _issue_draft(client, draft)
        pdf = client.post(f"/api/invoices/{issued['id']}/pdf?language=en")
        assert pdf.status_code == 200
        text = _pdf_text(pdf.content)
        assert "50.02" in text and issued["invoice_number"] in text
        assert "200.00" not in text and other["client"]["name"] not in text
        proof = _unclaimed_proof(client, "PAYMENT", "multi-plan-paid")
        _create(client, f"/api/invoices/{issued['id']}/payments", {
            "payment_date": "2026-04-11", "amount": "50.02", "method": "BANK_TRANSFER", "proof_attachment_id": proof,
        })
        paid = client.get(f"/api/invoices/{issued['id']}").json()
        assert (paid["payment_status"], paid["paid_amount"], paid["outstanding_amount"]) == ("PAID", "50.02", "0.00")
        assert _draft(client, other, year=2026, quarter=1).status_code == 201


def test_late_plan_invalidates_draft_at_api_and_sql_issue_without_allocating_number():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        first = _group(client, "LATEPLAN", platform_count=1)
        _finalized_settlement(client, first, 0, year=2026, quarter=1)
        draft = _draft(client, first, year=2026, quarter=1).json()
        second = _second_plan(client, first, "LATEPLAN")
        _finalized_settlement(client, second, 0, year=2026, quarter=1)
        result = client.post(f"/api/invoices/{draft['id']}/issue", json={"issue_date": "2026-04-10"})
        assert result.status_code == 409 and "集合已变化" in result.text
        with closing(sqlite3.connect(get_settings().database_path)) as sql:
            with pytest.raises(sqlite3.IntegrityError, match="invoice_source_set_incomplete"):
                sql.execute("UPDATE invoices SET lifecycle_status='ISSUING', invoice_number='Synthetic-Late', issue_date='2026-04-10', due_date='2026-04-24' WHERE id=?", (draft["id"],))
            sql.rollback()
        unchanged = client.get(f"/api/invoices/{draft['id']}").json()
        assert unchanged["lifecycle_status"] == "DRAFT" and unchanged["invoice_number"] is None


def test_paid_correction_adds_new_plan_on_same_platform_and_preserves_cash_company_difference():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        first = _group(client, "FIXPLAN", platform_count=1)
        original_source = _finalized_settlement(client, first, 0, year=2026, quarter=1, closing="1500.00")
        original = _issue_draft(client, _draft(client, first, year=2026, quarter=1))
        proof = _unclaimed_proof(client, "PAYMENT", "multi-plan-correction")
        payment = _create(client, f"/api/invoices/{original['id']}/payments", {
            "payment_date": "2026-04-11", "amount": "100.00", "method": "BANK_TRANSFER", "proof_attachment_id": proof,
        })
        original_pdf = client.post(f"/api/invoices/{original['id']}/pdf?language=en").content
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {"reason": "Omitted second fee plan"})
        _create(client, f"/api/settlements/{original_source['id']}/void", {"reason": "Rebuild complete client bill"})
        account_id = first["accounts"][0]["id"]
        recalculated = _create(client, "/api/settlements/calculate", {
            "client_id": first["client"]["id"], "platform_id": first["platforms"][0]["id"], "fee_plan_id": first["plan"]["id"],
            "year": 2026, "quarter": 1, "account_lines": [{"account_id": account_id,
                "beginning_snapshot_id": original_source["account_lines"][0]["beginning_snapshot_id"],
                "closing_snapshot_id": original_source["account_lines"][0]["closing_snapshot_id"], "original_hwm": "1000.00"}],
        })
        _create(client, f"/api/settlements/{recalculated['id']}/finalize", {})
        second = _second_plan(client, first, "FIXPLAN")
        _finalized_settlement(client, second, 0, year=2026, quarter=1, closing="1200.00")
        replacement = _issue_draft(client, _draft(client, second, year=2026, quarter=1))
        assert replacement["amount"] == "120.00" and replacement["source_count"] == 2
        completed = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {
            "replacement_invoice_id": replacement["id"],
            "retained_allocations": [{"payment_id": payment["payments"][0]["id"], "amount": "100.00"}],
            "company_difference": "20.00", "difference_reason": "Company calculation omission; customer does not pay extra",
        })
        assert completed["status"] == "COMPLETED"
        current = client.get(f"/api/invoices/{replacement['id']}").json()
        assert (current["paid_amount"], current["adjustment_amount"], current["outstanding_amount"], current["payment_status"]) == ("100.00", "20.00", "0.00", "PAID")
        assert client.post(f"/api/invoices/{original['id']}/pdf?language=en").content == original_pdf
        with closing(sqlite3.connect(get_settings().database_path)) as sql:
            assert sql.execute("SELECT count(*), sum(amount_cents) FROM payments WHERE invoice_id IN (?, ?)", (original["id"], replacement["id"])).fetchone() == (1, 10000)


def test_losing_plan_is_included_with_zero_fee_without_netting_another_account_profit():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        first = _group(client, "LOSSPLAN", platform_count=1)
        second = _second_plan(client, first, "LOSSPLAN")
        _finalized_settlement(client, first, 0, year=2026, quarter=1, closing="1100.00")
        loss = _finalized_settlement(client, second, 0, year=2026, quarter=1, closing="500.00")
        assert loss["service_fee"] == "0.00"
        invoice = _issue_draft(client, client.post("/api/invoices", json={"client_id": first["client"]["id"], "year": 2026, "quarter": 1}))
        assert invoice["amount"] == "20.00" and invoice["source_count"] == 2
        assert {line["service_fee"] for line in invoice["account_lines"]} == {"0.00", "20.00"}


def test_concurrent_different_plan_requests_create_only_one_complete_bill():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        first = _group(client, "RACEPLAN", platform_count=1)
        second = _second_plan(client, first, "RACEPLAN")
        _finalized_settlement(client, first, 0, year=2026, quarter=1)
        _finalized_settlement(client, second, 0, year=2026, quarter=1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda data: _draft(client, data, year=2026, quarter=1), [first, second]))
        assert sorted(response.status_code for response in responses) == [201, 409]
        invoice = next(response.json() for response in responses if response.status_code == 201)
        assert invoice["amount"] == "30.00" and invoice["source_count"] == 2


def test_company_only_correction_keeps_all_plans_and_their_frozen_fees():
    from test_invoice_company_correction import _company
    with TestClient(app, headers=WRITE_HEADERS) as client:
        first = _group(client, "PAYEEMPLAN", platform_count=1)
        second = _second_plan(client, first, "PAYEEMPLAN")
        _finalized_settlement(client, first, 0, year=2026, quarter=1)
        _finalized_settlement(client, second, 0, year=2026, quarter=1)
        original = _issue_draft(client, _draft(client, first, year=2026, quarter=1))
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {"reason": "Correct receiving company only", "target_company_id": target["id"]})
        replacement = _issue_draft(client, _draft(client, second, year=2026, quarter=1))
        assert replacement["amount"] == original["amount"] == "30.00"
        assert replacement["settlement_ids"] == original["settlement_ids"]
        assert replacement["fee_plan_ids"] == original["fee_plan_ids"]
        assert replacement["invoice_number"].startswith(target["name"] + "-")
        completed = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {"replacement_invoice_id": replacement["id"]})
        assert completed["status"] == "COMPLETED"
