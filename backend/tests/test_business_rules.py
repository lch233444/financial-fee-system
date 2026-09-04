from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import engine
from app.main import app
from app.models import Attachment, AuditEvent


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


def test_first_calendar_quarter_uses_previous_quarter_end_and_includes_first_day_flow() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "QBOUNDARY1")
        account_id = data["account"]["id"]
        beginning = _snapshot(client, account_id, "2026-03-31", "1000.00", closing=True)
        closing = _snapshot(client, account_id, "2026-06-30", "1200.00", closing=True)
        transaction_response = client.post(
            "/api/transactions",
            json={
                "account_id": account_id,
                "transaction_date": "2026-04-01",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        )
        assert transaction_response.status_code == 201, transaction_response.text
        transaction = transaction_response.json()
        proof_response = client.post(
            "/api/attachments",
            data={"entity_type": "TRANSACTION", "entity_id": str(transaction["id"])},
            files={
                "file": (
                    "quarter-opening-contribution.pdf",
                    b"%PDF-1.4\nquarter-opening\n%%EOF",
                    "application/pdf",
                )
            },
        )
        assert proof_response.status_code == 201, proof_response.text
        proof = proof_response.json()

        calculated = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["client"]["id"],
                "platform_id": data["platform"]["id"],
                "fee_plan_id": data["plan"]["id"],
                "year": 2026,
                "quarter": 2,
                "account_lines": [
                    {
                        "account_id": account_id,
                        "start_date": "2026-04-01",
                        "closing_date": "2026-06-30",
                        "beginning_snapshot_id": beginning["id"],
                        "closing_snapshot_id": closing["id"],
                        "original_hwm": "1000.00",
                    }
                ],
            },
        )
        assert calculated.status_code == 200, calculated.text
        settlement = calculated.json()
        assert settlement["beginning"] == "1000.00"
        assert settlement["contribution"] == "100.00"
        assert settlement["gain_loss"] == "100.00"
        assert settlement["service_fee"] == "20.00"

        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text

        with pytest.raises(IntegrityError, match="attachment_used_by_finalized_settlement"):
            with engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM attachments WHERE id = :attachment_id"),
                    {"attachment_id": proof["id"]},
                )


def test_inherited_quarter_boundary_includes_first_day_flow() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "QBOUNDARY2")
        first = _settlement(client, data, 1, "1000.00", "1100.00")
        finalized_first = client.post(f"/api/settlements/{first['id']}/finalize")
        assert finalized_first.status_code == 200, finalized_first.text

        transaction_response = client.post(
            "/api/transactions",
            json={
                "account_id": data["account"]["id"],
                "transaction_date": "2026-04-01",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        )
        assert transaction_response.status_code == 201, transaction_response.text
        uploaded = client.post(
            "/api/attachments",
            data={
                "entity_type": "TRANSACTION",
                "entity_id": str(transaction_response.json()["id"]),
            },
            files={
                "file": (
                    "inherited-quarter-opening.pdf",
                    b"%PDF-1.4\ninherited-opening\n%%EOF",
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text

        second = _settlement(client, data, 2, "1100.00", "1300.00")
        assert second["account_lines"][0]["beginning_snapshot_id"] == first["account_lines"][0]["closing_snapshot_id"]
        assert second["contribution"] == "100.00"
        assert second["gain_loss"] == "100.00"
        assert second["service_fee"] == "20.00"
        finalized_second = client.post(f"/api/settlements/{second['id']}/finalize")
        assert finalized_second.status_code == 200, finalized_second.text


def test_unfinalized_transaction_correction_preserves_evidence_and_updates_quarter() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "TXCORRECT1")
        account_id = data["account"]["id"]
        transaction_response = client.post(
            "/api/transactions",
            json={
                "account_id": account_id,
                "transaction_date": "2026-03-31",
                "transaction_type": "CONTRIBUTION",
                "amount": "2192.00",
                "remark": "待更正",
            },
        )
        assert transaction_response.status_code == 201, transaction_response.text
        transaction_id = transaction_response.json()["id"]
        uploaded = client.post(
            "/api/attachments",
            data={"entity_type": "TRANSACTION", "entity_id": str(transaction_id)},
            files={
                "file": (
                    "transaction-correction-proof.pdf",
                    b"%PDF-1.4\ntransaction-correction\n%%EOF",
                    "application/pdf",
                )
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        attachment_id = uploaded.json()["id"]

        corrected = client.patch(
            f"/api/transactions/{transaction_id}",
            json={
                "transaction_date": "2026-04-14",
                "transaction_type": "CONTRIBUTION",
                "amount": "2192.42",
                "remark": "按资金实际生效日期更正",
                "correction_reason": "原记录误用了供款月份截止日",
            },
        )
        assert corrected.status_code == 200, corrected.text
        corrected_payload = corrected.json()
        expected_values = {
            "id": transaction_id,
            "transaction_date": "2026-04-14",
            "transaction_type": "CONTRIBUTION",
            "amount": "2192.42",
            "remark": "按资金实际生效日期更正",
            "evidence_complete": True,
            "correction_allowed": True,
            "locked_settlement_id": None,
        }
        assert {
            key: corrected_payload[key] for key in expected_values
        } == expected_values

        duplicate = client.patch(
            f"/api/transactions/{transaction_id}",
            json={
                "transaction_date": "2026-04-14",
                "transaction_type": "CONTRIBUTION",
                "amount": "2192.42",
                "remark": "按资金实际生效日期更正",
                "correction_reason": "重复提交同一更正",
            },
        )
        assert duplicate.status_code == 400
        assert duplicate.json()["detail"] == "更正后的资金流水与原记录相同"

        beginning = _snapshot(client, account_id, "2026-03-31", "299621.36", closing=True)
        closing = _snapshot(client, account_id, "2026-06-30", "326123.51", closing=True)
        calculated = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["client"]["id"],
                "platform_id": data["platform"]["id"],
                "fee_plan_id": data["plan"]["id"],
                "year": 2026,
                "quarter": 2,
                "start_date": "2026-04-01",
                "closing_date": "2026-06-30",
                "account_lines": [
                    {
                        "account_id": account_id,
                        "beginning_snapshot_id": beginning["id"],
                        "closing_snapshot_id": closing["id"],
                        "original_hwm": "299621.36",
                    }
                ],
            },
        )
        assert calculated.status_code == 200, calculated.text
        assert calculated.json()["contribution"] == "2192.42"

        with Session(engine) as db:
            attachment = db.get(Attachment, attachment_id)
            assert attachment is not None
            assert (attachment.entity_type, attachment.entity_id) == (
                "TRANSACTION",
                transaction_id,
            )
            audits = db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "TRANSACTION_CORRECTED",
                    AuditEvent.entity_type == "TRANSACTION",
                    AuditEvent.entity_id == transaction_id,
                )
            ).all()
            assert len(audits) == 1
            assert audits[0].details_json == {
                "reason": "原记录误用了供款月份截止日",
                "before": {
                    "account_id": account_id,
                    "transaction_date": "2026-03-31",
                    "transaction_type": "CONTRIBUTION",
                    "amount_cents": 219200,
                    "remark": "待更正",
                },
                "after": {
                    "account_id": account_id,
                    "transaction_date": "2026-04-14",
                    "transaction_type": "CONTRIBUTION",
                    "amount_cents": 219242,
                    "remark": "按资金实际生效日期更正",
                },
            }


def test_transaction_correction_rejects_finalized_source_or_target_period() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "TXCORRECT2")
        account_id = data["account"]["id"]
        locked_transaction = client.post(
            "/api/transactions",
            json={
                "account_id": account_id,
                "transaction_date": "2026-02-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        )
        assert locked_transaction.status_code == 201, locked_transaction.text
        locked_id = locked_transaction.json()["id"]
        proof = client.post(
            "/api/attachments",
            data={"entity_type": "TRANSACTION", "entity_id": str(locked_id)},
            files={
                "file": (
                    "finalized-transaction-proof.pdf",
                    b"%PDF-1.4\nfinalized-transaction\n%%EOF",
                    "application/pdf",
                )
            },
        )
        assert proof.status_code == 201, proof.text
        settlement = _settlement(client, data, 1, "1000.00", "1200.00")
        finalized = client.post(f"/api/settlements/{settlement['id']}/finalize")
        assert finalized.status_code == 200, finalized.text

        change_locked = client.patch(
            f"/api/transactions/{locked_id}",
            json={
                "transaction_date": "2026-04-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
                "remark": None,
                "correction_reason": "尝试移出已结算期间",
            },
        )
        assert change_locked.status_code == 409
        assert f"Finalized Settlement #{settlement['id']}" in change_locked.json()["detail"]

        open_transaction = client.post(
            "/api/transactions",
            json={
                "account_id": account_id,
                "transaction_date": "2026-04-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "50.00",
            },
        )
        assert open_transaction.status_code == 201, open_transaction.text
        move_into_locked = client.patch(
            f"/api/transactions/{open_transaction.json()['id']}",
            json={
                "transaction_date": "2026-02-20",
                "transaction_type": "CONTRIBUTION",
                "amount": "50.00",
                "remark": None,
                "correction_reason": "尝试移入已结算期间",
            },
        )
        assert move_into_locked.status_code == 409
        assert f"Finalized Settlement #{settlement['id']}" in move_into_locked.json()["detail"]

        listed = {
            item["id"]: item for item in client.get("/api/transactions").json()
        }
        assert listed[locked_id]["correction_allowed"] is False
        assert listed[locked_id]["locked_settlement_id"] == settlement["id"]
        assert listed[open_transaction.json()["id"]]["correction_allowed"] is True


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
        assert first_issued.json()["invoice_number"].endswith("-20260405-1")
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
        assert second_issued.json()["invoice_number"].endswith("-20260705-2")
