from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook
import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import TransactionRecord
from app.routes import invoices as invoices_module
from app.services.calculation import quarter_dates


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _snapshot(client: TestClient, account_id: int, as_of_date: str, balance: str, *, closing: bool) -> dict:
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
    item = response.json()
    uploaded = client.post(
        "/api/attachments",
        data={"entity_type": "SNAPSHOT", "entity_id": str(item["id"])},
        files={"file": (f"snapshot-{item['id']}.pdf", b"%PDF-1.4\nreview\n%%EOF", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return item


def _master(client: TestClient, suffix: str, *, start_date: str = "2025-01-01") -> dict:
    company = client.post(
        "/api/companies", json={"name": f"Review Company {suffix}", "code": f"RC{suffix}"}
    ).json()
    fc = client.post(
        "/api/fcs",
        json={"company_id": company["id"], "name": f"Review FC {suffix}", "code": f"F{suffix}"},
    ).json()
    platform = client.post(
        "/api/platforms", json={"name": f"Review Platform {suffix}", "code": f"P{suffix}"}
    ).json()
    plan = client.post(
        "/api/fee-plans",
        json={
            "company_id": company["id"],
            "name": f"Review Plan {suffix}",
            "code": f"PL{suffix}",
            "fee_rate_percent": "20.00",
        },
    ).json()
    customer = client.post(
        "/api/clients",
        json={
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"Review Client {suffix}",
            "management_start_date": start_date,
            "status": "ACTIVE",
        },
    ).json()
    account = client.post(
        "/api/accounts",
        json={
            "client_id": customer["id"],
            "platform_id": platform["id"],
            "fee_plan_id": plan["id"],
            "account_number": f"ACC-{suffix}",
            "start_date": start_date,
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


def _settlement(
    client: TestClient,
    data: dict,
    *,
    year: int,
    quarter: int,
    beginning: str = "1000.00",
    closing: str = "1100.00",
) -> dict:
    start_date, closing_date = quarter_dates(year, quarter)
    account_id = data["account"]["id"]
    closing_snapshot = _snapshot(
        client, account_id, closing_date.isoformat(), closing, closing=True
    )
    finalized = [
        item
        for item in client.get("/api/settlements").json()
        if item["status"] == "FINALIZED"
        and any(line["account_id"] == account_id for line in item["account_lines"])
        and item["year"] * 4 + item["quarter"] < year * 4 + quarter
    ]
    line = {"account_id": account_id, "closing_snapshot_id": closing_snapshot["id"]}
    if not finalized:
        beginning_snapshot = _snapshot(
            client, account_id, start_date.isoformat(), beginning, closing=False
        )
        line.update({"beginning_snapshot_id": beginning_snapshot["id"], "original_hwm": beginning})
    response = client.post(
        "/api/settlements/calculate",
        json={
            "client_id": data["client"]["id"],
            "platform_id": data["platform"]["id"],
            "fee_plan_id": data["plan"]["id"],
            "year": year,
            "quarter": quarter,
            "start_date": start_date.isoformat(),
            "closing_date": closing_date.isoformat(),
            "account_lines": [line],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _finalize(client: TestClient, settlement: dict) -> dict:
    response = client.post(f"/api/settlements/{settlement['id']}/finalize")
    assert response.status_code == 200, response.text
    return response.json()


def _fake_pdf(*, output_path: Path, **_kwargs) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"%PDF-1.4\nreview-test\n%%EOF")
    return output_path


def test_financial_inputs_reject_silent_precision_loss() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "PREC")
        invalid_transaction = client.post(
            "/api/transactions",
            json={
                "account_id": data["account"]["id"],
                "transaction_date": "2026-02-01",
                "transaction_type": "CONTRIBUTION",
                "amount": "0.004",
            },
        )
        assert invalid_transaction.status_code == 422
        valid_transaction = client.post(
            "/api/transactions",
            json={
                "account_id": data["account"]["id"],
                "transaction_date": "2026-02-01",
                "transaction_type": "CONTRIBUTION",
                "amount": "0.010",
            },
        )
        assert valid_transaction.status_code == 201
        assert valid_transaction.json()["amount"] == "0.01"

        assert client.post(
            "/api/balance-snapshots",
            json={
                "account_id": data["account"]["id"],
                "as_of_date": "2026-03-31",
                "total_balance": "1.001",
            },
        ).status_code == 422
        assert client.post(
            "/api/invoices/999999/payments",
            json={"payment_date": "2026-04-01", "amount": "0.004", "method": "TEST"},
        ).status_code == 422
        assert client.post(
            "/api/fee-plans",
            json={
                "company_id": data["company"]["id"],
                "name": "Invalid Precision",
                "code": "BADPREC",
                "fee_rate_percent": "20.999",
            },
        ).status_code == 422
        exact_rate = client.post(
            "/api/fee-plans",
            json={
                "company_id": data["company"]["id"],
                "name": "Exact Precision",
                "code": "GOODPREC",
                "fee_rate_percent": "20.990",
            },
        )
        assert exact_rate.status_code == 201
        assert exact_rate.json()["fee_rate_percent"] == 20.99


def test_quarter_end_snapshot_respects_manual_opt_out() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "SNAP")
        response = client.post(
            "/api/balance-snapshots",
            json={
                "account_id": data["account"]["id"],
                "as_of_date": "2026-03-31",
                "total_balance": "1000.00",
                "eligible_for_closing": False,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["eligible_for_closing"] is False


def test_finalized_period_blocks_backfill_and_hwm_chain_mutation() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "CHAIN")
        first = _finalize(client, _settlement(client, data, year=2026, quarter=1))
        backfill = client.post(
            "/api/transactions",
            json={
                "account_id": data["account"]["id"],
                "transaction_date": "2026-02-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "10.00",
            },
        )
        assert backfill.status_code == 409
        with SessionLocal() as db:
            db.add(
                TransactionRecord(
                    account_id=data["account"]["id"],
                    transaction_date=date(2026, 2, 15),
                    transaction_type="CONTRIBUTION",
                    amount_cents=1000,
                )
            )
            with pytest.raises(IntegrityError, match="transaction_in_finalized_period"):
                db.commit()
            db.rollback()

        second = _settlement(client, data, year=2026, quarter=2, beginning="1100.00", closing="1200.00")
        assert second["previous_settlement_id"] == first["id"]
        second = _finalize(client, second)
        blocked_void = client.post(
            f"/api/settlements/{first['id']}/void", json={"reason": "不能破坏后续HWM"}
        )
        assert blocked_void.status_code == 409
        assert client.post(
            f"/api/settlements/{second['id']}/void", json={"reason": "按时间倒序作废"}
        ).status_code == 200
        assert client.post(
            f"/api/settlements/{first['id']}/void", json={"reason": "下游已先作废"}
        ).status_code == 200


def test_cannot_create_earlier_settlement_after_later_period_exists() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "ORDER")
        _finalize(client, _settlement(client, data, year=2026, quarter=3))
        earlier = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["client"]["id"],
                "platform_id": data["platform"]["id"],
                "fee_plan_id": data["plan"]["id"],
                "year": 2026,
                "quarter": 2,
                "account_lines": [
                    {"account_id": data["account"]["id"], "closing_snapshot_id": 999999, "original_hwm": "1000.00"}
                ],
            },
        )
        assert earlier.status_code == 409


def test_draft_invoice_blocks_settlement_void() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "DRAFTINV")
        settlement = _finalize(client, _settlement(client, data, year=2026, quarter=1))
        invoice = client.post("/api/invoices", json={"settlement_id": settlement["id"], "language": "zh"})
        assert invoice.status_code == 201, invoice.text
        assert client.post(
            f"/api/settlements/{settlement['id']}/void", json={"reason": "仍有Draft Invoice"}
        ).status_code == 409
        assert client.post(
            f"/api/invoices/{invoice.json()['id']}/void", json={"reason": "先作废Draft Invoice"}
        ).status_code == 200
        assert client.post(
            f"/api/settlements/{settlement['id']}/void", json={"reason": "关联Invoice已作废"}
        ).status_code == 200


def test_ocr_draft_records_can_be_completed_and_activated() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "DRAFTFLOW")
        draft_client = client.post(
            "/api/clients", json={"name": "OCR Draft Client", "status": "DRAFT"}
        ).json()
        draft_account = client.post(
            "/api/accounts",
            json={
                "client_id": draft_client["id"],
                "account_number": "OCR-DRAFT-FLOW",
                "status": "DRAFT",
            },
        ).json()
        assert client.patch(
            f"/api/clients/{draft_client['id']}",
            json={
                "company_id": data["company"]["id"],
                "fc_id": data["fc"]["id"],
                "management_start_date": "2025-01-01",
                "status": "ACTIVE",
            },
        ).status_code == 200
        activated = client.patch(
            f"/api/accounts/{draft_account['id']}",
            json={
                "platform_id": data["platform"]["id"],
                "fee_plan_id": data["plan"]["id"],
                "start_date": "2025-01-01",
                "status": "ACTIVE",
            },
        )
        assert activated.status_code == 200, activated.text
        completed_data = {
            **data,
            "client": {"id": draft_client["id"]},
            "account": {"id": draft_account["id"]},
        }
        settlement = _settlement(client, completed_data, year=2026, quarter=1)
        assert settlement["status"] == "DRAFT"


def test_finalized_settlement_freezes_fc_ownership() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "OWNER")
        settlement = _finalize(client, _settlement(client, data, year=2026, quarter=1))
        replacement = client.post(
            "/api/fcs",
            json={
                "company_id": data["company"]["id"],
                "name": "Replacement FC",
                "code": "REPL",
            },
        ).json()
        changed = client.patch(
            f"/api/clients/{data['client']['id']}", json={"fc_id": replacement["id"]}
        )
        assert changed.status_code == 200, changed.text

        report = client.get("/api/reports/fc?year=2026&quarter=1").json()
        original_row = next(row for row in report if row["fc_id"] == data["fc"]["id"])
        replacement_row = next(row for row in report if row["fc_id"] == replacement["id"])
        assert original_row["service_fee_generated"] == settlement["service_fee"]
        assert replacement_row["service_fee_generated"] == "0.00"
        invoice = client.post("/api/invoices", json={"settlement_id": settlement["id"], "language": "zh"})
        assert invoice.status_code == 201, invoice.text
        assert invoice.json()["fc_name"] == data["fc"]["name"]


def test_dashboard_and_fc_report_share_requested_year(monkeypatch) -> None:
    monkeypatch.setattr(invoices_module, "generate_settlement_pdf", _fake_pdf)
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "PERIOD")
        before = client.get("/api/dashboard?year=2026").json()
        old_settlement = _finalize(client, _settlement(client, data, year=2025, quarter=4))
        old_draft = client.post(
            "/api/invoices", json={"settlement_id": old_settlement["id"], "language": "zh"}
        ).json()
        old_invoice = client.post(
            f"/api/invoices/{old_draft['id']}/issue", json={"issue_date": "2026-01-05", "language": "zh"}
        )
        assert old_invoice.status_code == 200, old_invoice.text
        assert client.post(
            f"/api/invoices/{old_draft['id']}/payments",
            json={"payment_date": "2026-01-10", "amount": old_invoice.json()["amount"], "method": "TEST"},
        ).status_code == 201
        after_old_payment = client.get("/api/dashboard?year=2026").json()
        assert after_old_payment["generated_service_fee"] == before["generated_service_fee"]
        assert after_old_payment["paid_amount"] == before["paid_amount"]
        assert after_old_payment["outstanding_amount"] == before["outstanding_amount"]

        current = _finalize(
            client, _settlement(client, data, year=2026, quarter=1, beginning="1100.00", closing="1200.00")
        )
        dashboard = client.get("/api/dashboard?year=2026").json()
        assert dashboard["period"] == {"year": 2026, "quarter": None}
        assert Decimal(dashboard["generated_service_fee"]) - Decimal(before["generated_service_fee"]) == Decimal(
            current["service_fee"]
        )
        assert dashboard["paid_amount"] == before["paid_amount"]
        assert dashboard["outstanding_amount"] == before["outstanding_amount"]


def test_generated_exports_require_post_and_excel_uses_locked_fee() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "EXPORT")
        settlement = _finalize(
            client,
            _settlement(
                client, data, year=2026, quarter=1, beginning="0.01", closing="0.04"
            ),
        )
        assert settlement["service_fee"] == "0.01"
        assert client.get(f"/api/exports/excel?settlement_ids={settlement['id']}").status_code in {404, 405}
        assert client.get(
            f"/api/exports/pdf?settlement_id={settlement['id']}&language=zh"
        ).status_code in {404, 405}
        response = client.post(f"/api/exports/excel?settlement_ids={settlement['id']}")
        assert response.status_code == 200, response.text
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        assert workbook["利润20%"]["V3"].value == 0.01


def test_invoice_issue_reserves_unique_numbers_without_render_write_transaction(monkeypatch) -> None:
    monkeypatch.setattr(invoices_module, "generate_settlement_pdf", _fake_pdf)
    with TestClient(app, headers=WRITE_HEADERS) as client:
        first_data = _master(client, "CONCUR")
        second_client = client.post(
            "/api/clients",
            json={
                "company_id": first_data["company"]["id"],
                "fc_id": first_data["fc"]["id"],
                "name": "Concurrent Client Two",
                "management_start_date": "2025-01-01",
                "status": "ACTIVE",
            },
        ).json()
        second_account = client.post(
            "/api/accounts",
            json={
                "client_id": second_client["id"],
                "platform_id": first_data["platform"]["id"],
                "fee_plan_id": first_data["plan"]["id"],
                "account_number": "ACC-CONCUR-2",
                "start_date": "2025-01-01",
                "status": "ACTIVE",
            },
        ).json()
        second_data = {**first_data, "client": second_client, "account": second_account}
        settlements = [
            _finalize(client, _settlement(client, data, year=2026, quarter=1))
            for data in (first_data, second_data)
        ]
        drafts = [
            client.post("/api/invoices", json={"settlement_id": item["id"], "language": "zh"}).json()
            for item in settlements
        ]

        def issue(invoice_id: int) -> dict:
            response = client.post(
                f"/api/invoices/{invoice_id}/issue",
                json={"issue_date": "2026-04-05", "language": "zh"},
            )
            assert response.status_code == 200, response.text
            return response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            issued = list(executor.map(issue, [item["id"] for item in drafts]))
        numbers = {item["invoice_number"] for item in issued}
        assert len(numbers) == 2
        assert {number.rsplit("-", 1)[1] for number in numbers if number} == {"001", "002"}
        for item in issued:
            assert client.get(f"/api/invoices/{item['id']}/pdf?language=zh").status_code in {404, 405}
            assert client.post(f"/api/invoices/{item['id']}/pdf?language=zh").status_code == 200
