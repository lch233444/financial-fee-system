from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def create_master_data(client: TestClient) -> dict:
    company = client.post(
        "/api/companies",
        json={
            "name": "Alpha Advisory Limited",
            "code": "AAA",
            "payment_terms_days": 14,
            "bank_information": "Bank A / Account 123",
        },
    ).json()
    fc = client.post(
        "/api/fcs", json={"company_id": company["id"], "name": "Tony Wu", "code": "TW"}
    ).json()
    platform = client.post(
        "/api/platforms", json={"name": "BCT (MPF) Pro Choice", "code": "BCT", "trustee": "BCT"}
    ).json()
    plan = client.post(
        "/api/fee-plans",
        json={
            "company_id": company["id"],
            "name": "Profit Sharing 20%",
            "code": "PS20",
            "fee_rate_percent": "20.00",
        },
    ).json()
    customer = client.post(
        "/api/clients",
        json={
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": "Client One",
            "management_start_date": "2026-08-01",
            "status": "ACTIVE",
        },
    ).json()
    account = client.post(
        "/api/accounts",
        json={
            "client_id": customer["id"],
            "platform_id": platform["id"],
            "fee_plan_id": plan["id"],
            "account_number": "AC-001",
            "start_date": "2026-08-01",
            "status": "ACTIVE",
        },
    ).json()
    return {"company": company, "fc": fc, "platform": platform, "plan": plan, "client": customer, "account": account}


def test_full_settlement_invoice_and_payment_flow() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        assert client.get("/api/health").status_code == 200
        data = create_master_data(client)
        account_id = data["account"]["id"]
        client.post(
            "/api/transactions",
            json={
                "account_id": account_id,
                "transaction_date": "2026-08-01",
                "transaction_type": "CONTRIBUTION",
                "amount": "5000.00",
                "remark": "开始日资金应计入Beginning，因此结算不会纳入本期Contribution",
            },
        )
        client.post(
            "/api/transactions",
            json={
                "account_id": account_id,
                "transaction_date": "2026-08-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "1000.00",
            },
        )
        response = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["client"]["id"],
                "platform_id": data["platform"]["id"],
                "fee_plan_id": data["plan"]["id"],
                "year": 2026,
                "quarter": 3,
                "start_date": "2026-08-01",
                "closing_date": "2026-09-30",
                "original_hwm": "5000.00",
                "account_lines": [{"account_id": account_id, "beginning": "5000.00", "closing": "6500.00"}],
            },
        )
        assert response.status_code == 200, response.text
        settlement = response.json()
        assert settlement["contribution"] == "1000.00"
        assert settlement["gain_loss"] == "500.00"
        assert settlement["service_fee"] == "100.00"
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text

        draft = client.post("/api/invoices", json={"settlement_id": settlement["id"], "language": "zh"})
        assert draft.status_code == 201, draft.text
        invoice_id = draft.json()["id"]
        issued = client.post(
            f"/api/invoices/{invoice_id}/issue",
            json={"issue_date": "2026-10-05", "language": "zh"},
        )
        assert issued.status_code == 200, issued.text
        assert issued.json()["invoice_number"] == "AAA-TW-202608-001"
        assert issued.json()["due_date"] == "2026-10-19"

        partial = client.post(
            f"/api/invoices/{invoice_id}/payments",
            json={"payment_date": "2026-10-10", "amount": "40.00", "method": "BANK_TRANSFER"},
        )
        assert partial.status_code == 201, partial.text
        assert partial.json()["payment_status"] == "PARTIALLY_PAID"
        paid = client.post(
            f"/api/invoices/{invoice_id}/payments",
            json={"payment_date": "2026-10-12", "amount": "60.00", "method": "BANK_TRANSFER"},
        )
        assert paid.status_code == 201, paid.text
        assert paid.json()["payment_status"] == "PAID"


def test_zero_fee_settlement_cannot_create_invoice() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        # 使用不同编码以免与前一个测试共享唯一键冲突。
        company = client.post("/api/companies", json={"name": "Beta Limited", "code": "BBB"}).json()
        fc = client.post("/api/fcs", json={"company_id": company["id"], "name": "Amy Li", "code": "AL"}).json()
        platform = client.post("/api/platforms", json={"name": "Platform Beta", "code": "PB"}).json()
        plan = client.post(
            "/api/fee-plans",
            json={"company_id": company["id"], "name": "PS20 Beta", "code": "PS20", "fee_rate_percent": "20"},
        ).json()
        customer = client.post(
            "/api/clients",
            json={
                "company_id": company["id"],
                "fc_id": fc["id"],
                "name": "Loss Client",
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
                "account_number": "LOSS-001",
                "status": "ACTIVE",
            },
        ).json()
        settlement = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": customer["id"],
                "platform_id": platform["id"],
                "fee_plan_id": plan["id"],
                "year": 2026,
                "quarter": 1,
                "original_hwm": "1000.00",
                "account_lines": [{"account_id": account["id"], "beginning": "1000.00", "closing": "800.00"}],
            },
        ).json()
        client.post(f"/api/settlements/{settlement['id']}/finalize")
        response = client.post("/api/invoices", json={"settlement_id": settlement["id"], "language": "zh"})
        assert response.status_code == 400
