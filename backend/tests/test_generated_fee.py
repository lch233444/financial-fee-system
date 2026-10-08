from decimal import Decimal

from fastapi.testclient import TestClient

from app.main import app
from app.routes import invoices as invoices_module
from test_review_fixes import WRITE_HEADERS, _fake_pdf, _finalize, _invoice_draft, _master, _settlement


def test_generated_fee_excludes_void_combined_invoice_until_successful_reissue(monkeypatch):
    monkeypatch.setattr(invoices_module, "generate_invoice_pdf", _fake_pdf)
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "FEEVOID")
        before = Decimal(client.get("/api/dashboard?year=2026&quarter=1").json()["generated_service_fee"])
        first = _finalize(client, _settlement(client, data, year=2026, quarter=1))
        second_platform = client.post("/api/platforms", json={"name": "Second Fee Platform", "code": "SFEE"}).json()
        second_account = client.post("/api/accounts", json={
            "client_id": data["client"]["id"], "platform_id": second_platform["id"],
            "fee_plan_id": data["plan"]["id"], "account_number": "FEE-2", "start_date": "2025-01-01", "status": "ACTIVE",
        }).json()
        second = _finalize(client, _settlement(client, {**data, "platform": second_platform, "account": second_account}, year=2026, quarter=1, closing="1200.00"))
        fee = Decimal(first["service_fee"]) + Decimal(second["service_fee"])

        def check(expected):
            summary = client.get("/api/dashboard?year=2026&quarter=1").json()
            assert Decimal(summary["generated_service_fee"]) == before + expected
            rows = client.get("/api/reports/fc?year=2026&quarter=1").json()
            row = next(row for row in rows if row["fc_id"] == data["fc"]["id"])
            assert Decimal(row["service_fee_generated"]) == expected
            assert row["charged_client_count"] == int(expected > 0)

        check(fee)  # Finalized, never invoiced: still generated.
        draft = _invoice_draft(client, data, year=2026, quarter=1).json()
        assert draft["source_count"] == 2
        check(fee)
        assert client.post(f"/api/invoices/{draft['id']}/issue", json={"issue_date": "2026-04-01", "language": "zh"}).status_code == 200
        check(fee)
        assert client.post(f"/api/invoices/{draft['id']}/void", json={"reason": "测试作废合并账单"}).status_code == 200
        check(Decimal(0))
        assert client.post(f"/api/invoices/{draft['id']}/void", json={"reason": "重复作废应被拒绝"}).status_code == 409
        check(Decimal(0))
        replacement = _invoice_draft(client, data, year=2026, quarter=1).json()
        check(Decimal(0))  # Rebuilding a draft does not restore a cancelled fee.

        def failed_pdf(**_kwargs):
            raise RuntimeError("synthetic PDF failure")

        monkeypatch.setattr(invoices_module, "generate_invoice_pdf", failed_pdf)
        failed = client.post(f"/api/invoices/{replacement['id']}/issue", json={"issue_date": "2026-04-02", "language": "zh"})
        assert failed.status_code >= 400
        check(Decimal(0))
        monkeypatch.setattr(invoices_module, "generate_invoice_pdf", _fake_pdf)
        assert client.post(f"/api/invoices/{replacement['id']}/issue", json={"issue_date": "2026-04-02", "language": "zh"}).status_code == 200
        check(fee)  # Same two source settlements are counted once despite invoice history.
        assert client.post(f"/api/invoices/{replacement['id']}/void", json={"reason": "再次作废仍排除"}).status_code == 200
        check(Decimal(0))


def test_void_draft_and_void_settlement_do_not_inflate_other_periods():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "FEEDRAFT")
        before = client.get("/api/dashboard?year=2026").json()["generated_service_fee"]
        quarter_two = client.get("/api/dashboard?year=2026&quarter=2").json()["generated_service_fee"]
        settlement = _finalize(client, _settlement(client, data, year=2026, quarter=1))
        invoice = _invoice_draft(client, data, year=2026, quarter=1).json()
        assert client.post(f"/api/invoices/{invoice['id']}/void", json={"reason": "作废尚未出具的账单"}).status_code == 200
        assert client.get("/api/dashboard?year=2026").json()["generated_service_fee"] == before
        assert client.get("/api/dashboard?year=2026&quarter=2").json()["generated_service_fee"] == quarter_two
        assert client.post(f"/api/settlements/{settlement['id']}/void", json={"reason": "重算前作废结算"}).status_code == 200
        assert client.get("/api/dashboard?year=2026").json()["generated_service_fee"] == before
