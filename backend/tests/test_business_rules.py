from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _snapshot(client: TestClient, account_id: int, as_of_date: str, balance: str, *, closing: bool) -> dict:
    item = client.post(
        "/api/balance-snapshots",
        json={
            "account_id": account_id,
            "as_of_date": as_of_date,
            "total_balance": balance,
            "eligible_for_closing": closing,
        },
    ).json()
    uploaded = client.post(
        "/api/attachments",
        data={"entity_type": "SNAPSHOT", "entity_id": str(item["id"])},
        files={"file": (f"snapshot-{item['id']}.pdf", b"%PDF-1.4\ntest\n%%EOF", "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    return item


def _master(client: TestClient, suffix: str) -> dict:
    company = client.post(
        "/api/companies",
        json={"name": f"Rules Company {suffix}", "code": f"R{suffix}", "payment_terms_days": 10},
    ).json()
    fc = client.post(
        "/api/fcs",
        json={"company_id": company["id"], "name": f"Rules FC {suffix}", "code": f"F{suffix}"},
    ).json()
    platform = client.post(
        "/api/platforms", json={"name": f"Rules Platform {suffix}", "code": f"P{suffix}"}
    ).json()
    plan = client.post(
        "/api/fee-plans",
        json={
            "company_id": company["id"],
            "name": f"Rules Plan {suffix}",
            "code": f"PLAN{suffix}",
            "fee_rate_percent": "20",
        },
    ).json()
    customer = client.post(
        "/api/clients",
        json={
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"Rules Client {suffix}",
            "management_start_date": "2026-01-15",
            "status": "ACTIVE",
        },
    ).json()
    account = client.post(
        "/api/accounts",
        json={
            "client_id": customer["id"],
            "platform_id": platform["id"],
            "fee_plan_id": plan["id"],
            "account_number": f"RULE-{suffix}",
            "start_date": "2026-01-15",
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


def _settlement(client: TestClient, data: dict, quarter: int, beginning: str, closing: str) -> dict:
    start_dates = {1: "2026-01-15", 2: "2026-04-01", 3: "2026-07-01", 4: "2026-10-01"}
    closing_dates = {1: "2026-03-31", 2: "2026-06-30", 3: "2026-09-30", 4: "2026-12-31"}
    account_id = data["account"]["id"]
    closing_snapshot = _snapshot(client, account_id, closing_dates[quarter], closing, closing=True)
    finalized = [
        item
        for item in client.get("/api/settlements").json()
        if item["status"] == "FINALIZED"
        and any(line["account_id"] == account_id for line in item["account_lines"])
        and item["year"] * 4 + item["quarter"] < 2026 * 4 + quarter
    ]
    line = {"account_id": account_id, "closing_snapshot_id": closing_snapshot["id"]}
    if not finalized:
        beginning_snapshot = _snapshot(client, account_id, start_dates[quarter], beginning, closing=False)
        line.update({"beginning_snapshot_id": beginning_snapshot["id"], "original_hwm": beginning})
    payload = {
        "client_id": data["client"]["id"],
        "platform_id": data["platform"]["id"],
        "fee_plan_id": data["plan"]["id"],
        "year": 2026,
        "quarter": quarter,
        "start_date": start_dates[quarter],
        "closing_date": closing_dates[quarter],
        "account_lines": [line],
    }
    response = client.post("/api/settlements/calculate", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_non_quarter_snapshot_cannot_be_closing_candidate() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "A1")
        rejected = client.post(
            "/api/balance-snapshots",
            json={
                "account_id": data["account"]["id"],
                "as_of_date": "2026-05-20",
                "total_balance": "9736.57",
                "eligible_for_closing": True,
            },
        )
        assert rejected.status_code == 400
        accepted = client.post(
            "/api/balance-snapshots",
            json={
                "account_id": data["account"]["id"],
                "as_of_date": "2026-05-20",
                "total_balance": "9736.57",
                "eligible_for_closing": False,
            },
        )
        assert accepted.status_code == 201
        assert accepted.json()["eligible_for_closing"] is False


def test_zero_denominator_blocks_finalization() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "A2")
        settlement = _settlement(client, data, 1, "0.00", "1.00")
        assert settlement["period_rate"] is None
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 400


def test_invoice_number_never_reuses_void_and_hwm_inherits() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "A3")
        first = _settlement(client, data, 1, "1000.00", "1100.00")
        assert client.post(f"/api/settlements/{first['id']}/finalize").status_code == 200
        first_draft = client.post(
            "/api/invoices",
            json={
                "client_id": data["client"]["id"],
                "year": 2026,
                "quarter": 1,
                "fee_plan_id": data["plan"]["id"],
                "language": "zh",
            },
        ).json()
        first_issued = client.post(
            f"/api/invoices/{first_draft['id']}/issue",
            json={"issue_date": "2026-04-05", "language": "zh"},
        )
        assert first_issued.status_code == 200, first_issued.text
        assert first_issued.json()["invoice_number"].endswith("-001")
        assert client.post(
            f"/api/invoices/{first_draft['id']}/void", json={"reason": "测试作废后编号不复用"}
        ).status_code == 200

        second = _settlement(client, data, 2, "1100.00", "1200.00")
        assert second["original_hwm"] == first["next_hwm"]
        assert second["account_lines"][0]["previous_line_id"] == first["account_lines"][0]["id"]
        assert second["account_lines"][0]["beginning_snapshot_id"] == first["account_lines"][0]["closing_snapshot_id"]
        assert second["account_lines"][0]["original_hwm"] == first["account_lines"][0]["next_hwm"]
        assert client.post(f"/api/settlements/{second['id']}/finalize").status_code == 200
        second_draft = client.post(
            "/api/invoices",
            json={
                "client_id": data["client"]["id"],
                "year": 2026,
                "quarter": 2,
                "fee_plan_id": data["plan"]["id"],
                "language": "en",
            },
        ).json()
        second_issued = client.post(
            f"/api/invoices/{second_draft['id']}/issue",
            json={"issue_date": "2026-07-05", "language": "en"},
        )
        assert second_issued.status_code == 200, second_issued.text
        assert second_issued.json()["invoice_number"].endswith("-002")
