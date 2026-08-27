from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.main import app


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _master(client: TestClient, suffix: str, *, accounts: int = 1) -> dict:
    company = client.post("/api/companies", json={"name": f"Account HWM {suffix}", "code": f"AH{suffix}"}).json()
    fc = client.post("/api/fcs", json={"company_id": company["id"], "name": f"FC {suffix}", "code": f"F{suffix}"}).json()
    platform = client.post("/api/platforms", json={"name": f"Platform {suffix}", "code": f"P{suffix}"}).json()
    plan = client.post(
        "/api/fee-plans",
        json={"company_id": company["id"], "name": "利润20%", "code": f"PL{suffix}", "fee_rate_percent": "20"},
    ).json()
    customer = client.post(
        "/api/clients",
        json={
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"Client {suffix}",
            "management_start_date": "2026-01-01",
            "status": "ACTIVE",
        },
    ).json()
    created_accounts = []
    for number in range(1, accounts + 1):
        created_accounts.append(
            client.post(
                "/api/accounts",
                json={
                    "client_id": customer["id"],
                    "platform_id": platform["id"],
                    "fee_plan_id": plan["id"],
                    "account_number": f"{suffix}-{number}",
                    "start_date": "2026-01-01",
                    "status": "ACTIVE",
                },
            ).json()
        )
    return {"company": company, "fc": fc, "platform": platform, "plan": plan, "client": customer, "accounts": created_accounts}


def _attach(client: TestClient, entity_type: str, entity_id: int) -> None:
    response = client.post(
        "/api/attachments",
        data={"entity_type": entity_type, "entity_id": str(entity_id)},
        files={"file": (f"proof-{entity_type}-{entity_id}.pdf", b"%PDF-1.4\nproof\n%%EOF", "application/pdf")},
    )
    assert response.status_code == 201, response.text


def _snapshot(
    client: TestClient,
    account_id: int,
    as_of_date: str,
    balance: str,
    *,
    closing: bool,
    evidence: bool,
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
    item = response.json()
    if evidence:
        _attach(client, "SNAPSHOT", item["id"])
    return item


def _calculate(client: TestClient, data: dict, lines: list[dict], *, quarter: int = 1) -> dict:
    response = client.post(
        "/api/settlements/calculate",
        json={
            "client_id": data["client"]["id"],
            "platform_id": data["platform"]["id"],
            "fee_plan_id": data["plan"]["id"],
            "year": 2026,
            "quarter": quarter,
            "account_lines": lines,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_each_account_fee_is_calculated_before_group_total() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "OFFSET", accounts=2)
        lines = []
        for account, closing_balance in zip(data["accounts"], ["1100.00", "800.00"], strict=True):
            beginning = _snapshot(client, account["id"], "2026-01-01", "1000.00", closing=False, evidence=True)
            closing = _snapshot(client, account["id"], "2026-03-31", closing_balance, closing=True, evidence=True)
            lines.append(
                {
                    "account_id": account["id"],
                    "beginning_snapshot_id": beginning["id"],
                    "closing_snapshot_id": closing["id"],
                    "original_hwm": "1000.00",
                }
            )
        settlement = _calculate(client, data, lines)
        assert settlement["calculation_mode"] == "ACCOUNT_HWM"
        assert settlement["formula_version"] == "HWM-2.0-ACCOUNT"
        assert [line["service_fee"] for line in settlement["account_lines"]] == ["20.00", "0.00"]
        assert settlement["watermark_difference"] == "-100.00"
        assert settlement["chargeable_above_hwm"] == "100.00"
        assert settlement["service_fee"] == "20.00"
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        excel = client.post(f"/api/exports/excel?settlement_ids={settlement['id']}")
        assert excel.status_code == 200, excel.text
        sheet = load_workbook(BytesIO(excel.content), data_only=False)["利润20%"]
        assert [sheet["H3"].value, sheet["H4"].value] == ["OFFSET-1", "OFFSET-2"]
        assert [sheet["V3"].value, sheet["V4"].value] == [20, 0]
        pdf = client.post(f"/api/exports/pdf?settlement_id={settlement['id']}&language=zh")
        assert pdf.status_code == 200, pdf.text


def test_draft_allows_missing_snapshot_evidence_but_finalize_blocks_it() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "EVIDENCE")
        account = data["accounts"][0]
        beginning = _snapshot(client, account["id"], "2026-01-01", "1000.00", closing=False, evidence=False)
        closing = _snapshot(client, account["id"], "2026-03-31", "1100.00", closing=True, evidence=False)
        settlement = _calculate(
            client,
            data,
            [{
                "account_id": account["id"],
                "beginning_snapshot_id": beginning["id"],
                "closing_snapshot_id": closing["id"],
                "original_hwm": "1000.00",
            }],
        )
        blocked = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert blocked.status_code == 400
        assert "缺少凭证" in blocked.json()["detail"]
        _attach(client, "SNAPSHOT", beginning["id"])
        _attach(client, "SNAPSHOT", closing["id"])
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text


def test_transaction_requires_its_own_evidence_before_finalize() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "TXPROOF")
        account = data["accounts"][0]
        beginning = _snapshot(client, account["id"], "2026-01-01", "1000.00", closing=False, evidence=True)
        closing = _snapshot(client, account["id"], "2026-03-31", "1210.00", closing=True, evidence=True)
        transaction = client.post(
            "/api/transactions",
            json={
                "account_id": account["id"],
                "transaction_date": "2026-02-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        ).json()
        settlement = _calculate(
            client,
            data,
            [{
                "account_id": account["id"],
                "beginning_snapshot_id": beginning["id"],
                "closing_snapshot_id": closing["id"],
                "original_hwm": "1000.00",
            }],
        )
        assert settlement["account_lines"][0]["contribution"] == "100.00"
        blocked = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert blocked.status_code == 400
        assert "CONTRIBUTION" in blocked.json()["detail"]
        _attach(client, "TRANSACTION", transaction["id"])
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
