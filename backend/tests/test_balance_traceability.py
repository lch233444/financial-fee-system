from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, StatementImport, SubAccount
from app.services.storage import store_bytes


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _post(client: TestClient, path: str, payload: dict) -> dict:
    response = client.post(path, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _create_statement_record(*, token: str, extracted: dict) -> tuple[int, bytes]:
    source_bytes = b"\x89PNG\r\n\x1a\n" + f"synthetic-statement-{token}".encode()
    stored_path, digest = store_bytes(
        data=source_bytes,
        original_name=f"synthetic-{token}.png",
        directory=get_settings().data_root / "statement_imports",
    )
    with SessionLocal() as db:
        statement = StatementImport(
            original_name=f"synthetic-{token}.png",
            stored_path=str(stored_path),
            sha256=digest,
            mime_type="image/png",
            parser_name="LOCAL_OCR_EMPF_ACCOUNT_PAGE",
            parser_version="1.1",
            status="NEEDS_REVIEW",
            extracted_json=extracted,
            confidence_json={"overall": 1.0},
            warnings_json=[],
        )
        db.add(statement)
        db.commit()
        db.refresh(statement)
        return statement.id, source_bytes


def _attach_snapshot(client: TestClient, snapshot_id: int) -> None:
    response = client.post(
        "/api/attachments",
        data={"entity_type": "SNAPSHOT", "entity_id": str(snapshot_id)},
        files={
            "file": (
                f"snapshot-{snapshot_id}.pdf",
                b"%PDF-1.4\nsynthetic proof\n%%EOF",
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201, response.text


def _active_settlement_case(client: TestClient, label: str) -> dict:
    short = label[:8].upper()
    company = _post(
        client,
        "/api/companies",
        {"name": f"State Company {label}", "code": f"C{short}"},
    )
    fc = _post(
        client,
        "/api/fcs",
        {"company_id": company["id"], "name": f"State FC {label}", "code": "SF"},
    )
    platform = _post(
        client,
        "/api/platforms",
        {"name": f"State Platform {label}", "code": f"P{short}"},
    )
    plan = _post(
        client,
        "/api/fee-plans",
        {
            "company_id": company["id"],
            "name": f"State Plan {label}",
            "code": "STATE20",
            "fee_rate_percent": "20.00",
        },
    )
    customer = _post(
        client,
        "/api/clients",
        {
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"State Client {label}",
            "management_start_date": "2026-07-01",
            "status": "ACTIVE",
        },
    )
    account = _post(
        client,
        "/api/accounts",
        {
            "client_id": customer["id"],
            "platform_id": platform["id"],
            "fee_plan_id": plan["id"],
            "account_number": f"STATE-{label}",
            "start_date": "2026-07-01",
            "status": "ACTIVE",
        },
    )
    beginning = _post(
        client,
        "/api/balance-snapshots",
        {
            "account_id": account["id"],
            "as_of_date": "2026-07-01",
            "total_balance": "1000.00",
            "eligible_for_closing": False,
        },
    )
    closing = _post(
        client,
        "/api/balance-snapshots",
        {
            "account_id": account["id"],
            "as_of_date": "2026-09-30",
            "total_balance": "1200.00",
            "eligible_for_closing": True,
        },
    )
    settlement_payload = {
        "client_id": customer["id"],
        "platform_id": platform["id"],
        "fee_plan_id": plan["id"],
        "year": 2026,
        "quarter": 3,
        "account_lines": [
            {
                "account_id": account["id"],
                "beginning_snapshot_id": beginning["id"],
                "closing_snapshot_id": closing["id"],
                "original_hwm": "1000.00",
            }
        ],
    }
    return {
        "company": company,
        "fc": fc,
        "platform": platform,
        "plan": plan,
        "customer": customer,
        "account": account,
        "beginning": beginning,
        "closing": closing,
        "settlement_payload": settlement_payload,
    }


def test_statement_confirmation_and_cross_platform_account_traceability() -> None:
    token = uuid4().hex
    short = token[:8].upper()
    shared_account_number = f"SHARED-{short}"
    client_name = f"Trace Client {short}"
    scheme_a = f"Trace Scheme A {short}"
    scheme_b = f"Trace Scheme B {short}"
    holding = {"fund_name": "Synthetic Fund", "market_value": "1234.56"}
    stored_holding = {
        **holding,
        "investment_gain_loss": None,
        "portfolio_percent": None,
        "units": None,
        "unit_price": None,
        "mandatory_contributions": None,
        "voluntary_contributions": None,
        "balance_as_of": None,
    }

    with TestClient(app, headers=WRITE_HEADERS) as client:
        company = _post(
            client,
            "/api/companies",
            {"name": f"Trace Company {short}", "code": f"TC{short}"},
        )
        fc = _post(
            client,
            "/api/fcs",
            {"company_id": company["id"], "name": f"Trace FC {short}", "code": "TF"},
        )
        platform_a = _post(
            client,
            "/api/platforms",
            {"name": f"Trace Platform A {short}", "code": f"TPA{short}"},
        )
        platform_b = _post(
            client,
            "/api/platforms",
            {"name": f"Trace Platform B {short}", "code": f"TPB{short}"},
        )
        plan = _post(
            client,
            "/api/fee-plans",
            {
                "company_id": company["id"],
                "name": f"Trace Plan {short}",
                "code": "PS20",
                "fee_rate_percent": "20.00",
            },
        )
        customer = _post(
            client,
            "/api/clients",
            {
                "company_id": company["id"],
                "fc_id": fc["id"],
                "name": client_name,
                "management_start_date": "2026-07-01",
                "status": "ACTIVE",
            },
        )
        account_a = _post(
            client,
            "/api/accounts",
            {
                "client_id": customer["id"],
                "platform_id": platform_a["id"],
                "fee_plan_id": plan["id"],
                "account_number": shared_account_number,
                "scheme_name": scheme_a,
                "start_date": "2026-07-01",
                "status": "ACTIVE",
            },
        )
        account_b = _post(
            client,
            "/api/accounts",
            {
                "client_id": customer["id"],
                "platform_id": platform_b["id"],
                "fee_plan_id": plan["id"],
                "account_number": shared_account_number,
                "scheme_name": scheme_b,
                "start_date": "2026-07-01",
                "status": "ACTIVE",
            },
        )

        statement_id, source_bytes = _create_statement_record(
            token=token,
            extracted={
                "document_type": "empf_account_page",
                "client_name": client_name,
                "account_number": shared_account_number,
                "scheme_name": scheme_a,
                "as_of_date": "2026-09-30",
                "total_balance": "1234.56",
                "holdings": [holding],
            },
        )

        assert client.get(f"/api/transactions?account_id={account_a['id']}").json() == []
        confirm_payload = {
            "client_name": client_name,
            "account_number": shared_account_number,
            "scheme_name": scheme_a,
            "as_of_date": "2026-09-30",
            "total_balance": "1234.56",
            "account_id": account_a["id"],
            "holdings": [holding],
        }
        missing_platform = client.post(
            f"/api/statement-imports/{statement_id}/confirm",
            json=confirm_payload,
        )
        assert missing_platform.status_code == 400
        assert "必须同时确认Platform" in missing_platform.json()["detail"]
        wrong_platform = client.post(
            f"/api/statement-imports/{statement_id}/confirm",
            json={**confirm_payload, "account_platform_id": platform_b["id"]},
        )
        assert wrong_platform.status_code == 409
        assert "Platform已变化" in wrong_platform.json()["detail"]
        confirmed = client.post(
            f"/api/statement-imports/{statement_id}/confirm",
            json={
                **confirm_payload,
                "account_platform_id": platform_a["id"],
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_body = confirmed.json()
        snapshot_id = confirmed_body["snapshot"]["id"]
        assert confirmed_body["statement_import"]["confirmed_account_id"] == account_a["id"]
        assert confirmed_body["statement_import"]["confirmed_snapshot_id"] == snapshot_id
        assert confirmed_body["snapshot"] == {
            "id": snapshot_id,
            "as_of_date": "2026-09-30",
            "total_balance": "1234.56",
            "eligible_for_closing": True,
        }

        import_record = client.get(f"/api/statement-imports/{statement_id}")
        assert import_record.status_code == 200, import_record.text
        assert import_record.json()["confirmed_account_id"] == account_a["id"]
        assert import_record.json()["confirmed_snapshot_id"] == snapshot_id
        assert "account_platform_id" not in import_record.json()["reviewed"]
        source_file = client.get(f"/api/statement-imports/{statement_id}/file")
        assert source_file.status_code == 200, source_file.text
        assert source_file.content == source_bytes

        account_a_transactions = client.get(
            f"/api/transactions?account_id={account_a['id']}"
        )
        assert account_a_transactions.status_code == 200
        assert account_a_transactions.json() == []

        account_a_snapshots = client.get(
            f"/api/balance-snapshots?account_id={account_a['id']}"
        )
        assert account_a_snapshots.status_code == 200
        imported_snapshot = account_a_snapshots.json()[0]
        assert imported_snapshot == {
            "id": snapshot_id,
            "account_id": account_a["id"],
            "account_number": shared_account_number,
            "client_id": customer["id"],
            "client_name": client_name,
            "platform_id": platform_a["id"],
            "platform_name": platform_a["name"],
            "fee_plan_id": plan["id"],
            "fee_plan_name": plan["name"],
            "scheme_name": scheme_a,
            "as_of_date": "2026-09-30",
            "total_balance": "1234.56",
            "currency": "HKD",
            "source_type": "STATEMENT_IMPORT",
            "statement_import_id": statement_id,
            "holdings": [stored_holding],
            "eligible_for_closing": True,
            "remark": "季末/退出日Closing候选",
            "evidence_count": 1,
            "evidence_complete": True,
        }

        manual_snapshot = _post(
            client,
            "/api/balance-snapshots",
            {
                "account_id": account_b["id"],
                "as_of_date": "2026-09-30",
                "total_balance": "900.00",
                "eligible_for_closing": True,
            },
        )
        _post(
            client,
            "/api/transactions",
            {
                "account_id": account_a["id"],
                "transaction_date": "2026-08-15",
                "transaction_type": "CONTRIBUTION",
                "amount": "100.00",
            },
        )
        _post(
            client,
            "/api/transactions",
            {
                "account_id": account_b["id"],
                "transaction_date": "2026-08-16",
                "transaction_type": "WITHDRAWAL",
                "amount": "50.00",
            },
        )

        matching_transactions = [
            item
            for item in client.get("/api/transactions").json()
            if item["account_number"] == shared_account_number
        ]
        assert len(matching_transactions) == 2
        transactions_by_platform = {
            item["platform_id"]: item for item in matching_transactions
        }
        for platform, account, scheme in (
            (platform_a, account_a, scheme_a),
            (platform_b, account_b, scheme_b),
        ):
            item = transactions_by_platform[platform["id"]]
            assert item["account_id"] == account["id"]
            assert item["client_id"] == customer["id"]
            assert item["client_name"] == client_name
            assert item["platform_name"] == platform["name"]
            assert item["fee_plan_id"] == plan["id"]
            assert item["fee_plan_name"] == plan["name"]
            assert item["scheme_name"] == scheme

        matching_snapshots = [
            item
            for item in client.get("/api/balance-snapshots").json()
            if item["account_number"] == shared_account_number
        ]
        assert len(matching_snapshots) == 2
        snapshots_by_platform = {item["platform_id"]: item for item in matching_snapshots}
        assert snapshots_by_platform[platform_a["id"]]["id"] == snapshot_id
        assert snapshots_by_platform[platform_b["id"]]["id"] == manual_snapshot["id"]
        assert snapshots_by_platform[platform_b["id"]]["statement_import_id"] is None
        assert snapshots_by_platform[platform_b["id"]]["holdings"] == []
        assert snapshots_by_platform[platform_a["id"]]["scheme_name"] == scheme_a
        assert snapshots_by_platform[platform_b["id"]]["scheme_name"] == scheme_b


@pytest.mark.parametrize(
    ("target", "status"),
    [
        ("client", "DRAFT"),
        ("client", "CLOSED"),
        ("account", "DRAFT"),
        ("account", "CLOSED"),
    ],
)
def test_settlement_calculate_requires_active_client_and_account(
    target: str, status: str
) -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _active_settlement_case(client, uuid4().hex)
        target_id = data["customer"]["id"] if target == "client" else data["account"]["id"]
        changed = client.patch(f"/api/{target}s/{target_id}", json={"status": status})
        assert changed.status_code == 200, changed.text

        rejected = client.post(
            "/api/settlements/calculate", json=data["settlement_payload"]
        )
        assert rejected.status_code == 409, rejected.text
        assert "Active" in rejected.json()["detail"]


@pytest.mark.parametrize(
    ("target", "status"),
    [
        ("client", "DRAFT"),
        ("client", "CLOSED"),
        ("account", "DRAFT"),
        ("account", "CLOSED"),
    ],
)
def test_settlement_finalize_rechecks_active_state_after_calculate(
    target: str, status: str
) -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _active_settlement_case(client, uuid4().hex)
        calculated = client.post(
            "/api/settlements/calculate", json=data["settlement_payload"]
        )
        assert calculated.status_code == 200, calculated.text
        settlement_id = calculated.json()["id"]

        target_id = data["customer"]["id"] if target == "client" else data["account"]["id"]
        changed = client.patch(f"/api/{target}s/{target_id}", json={"status": status})
        assert changed.status_code == 200, changed.text

        rejected = client.post(f"/api/settlements/{settlement_id}/finalize")
        assert rejected.status_code == 409, rejected.text
        assert "Active" in rejected.json()["detail"]
        still_draft = client.get(f"/api/settlements/{settlement_id}")
        assert still_draft.status_code == 200, still_draft.text
        assert still_draft.json()["status"] == "DRAFT"


def test_imported_exit_snapshot_requalifies_then_enters_settlement() -> None:
    token = uuid4().hex
    short = token[:8].upper()
    client_name = f"Exit Client {short}"
    account_number = f"EXIT-{short}"
    scheme_name = f"Exit Scheme {short}"
    exit_date = "2026-08-31"

    with TestClient(app, headers=WRITE_HEADERS) as client:
        company = _post(
            client,
            "/api/companies",
            {"name": f"Exit Company {short}", "code": f"E{short}"},
        )
        fc = _post(
            client,
            "/api/fcs",
            {"company_id": company["id"], "name": f"Exit FC {short}", "code": "EF"},
        )
        plan = _post(
            client,
            "/api/fee-plans",
            {
                "company_id": company["id"],
                "name": f"Exit Plan {short}",
                "code": "EXIT20",
                "fee_rate_percent": "20.00",
            },
        )
        statement_id, source_bytes = _create_statement_record(
            token=token,
            extracted={
                "document_type": "empf_account_page",
                "client_name": client_name,
                "account_number": account_number,
                "scheme_name": scheme_name,
                "as_of_date": exit_date,
                "total_balance": "1200.00",
                "holdings": [],
            },
        )

        confirmed = client.post(
            f"/api/statement-imports/{statement_id}/confirm",
            json={
                "client_name": client_name,
                "account_number": account_number,
                "scheme_name": scheme_name,
                "trustee": f"Exit Trustee {short}",
                "as_of_date": exit_date,
                "total_balance": "1200.00",
                "holdings": [],
            },
        )
        assert confirmed.status_code == 200, confirmed.text
        confirmed_body = confirmed.json()
        assert confirmed_body["created_draft"] is True
        assert confirmed_body["snapshot"]["eligible_for_closing"] is False
        account_id = confirmed_body["account_id"]
        snapshot_id = confirmed_body["snapshot"]["id"]

        source_file = client.get(f"/api/statement-imports/{statement_id}/file")
        assert source_file.status_code == 200, source_file.text
        assert source_file.content == source_bytes

        account = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account_id
        )
        customer_id = account["client_id"]
        activated_client = client.patch(
            f"/api/clients/{customer_id}",
            json={
                "company_id": company["id"],
                "fc_id": fc["id"],
                "management_start_date": "2026-07-01",
                "status": "ACTIVE",
            },
        )
        assert activated_client.status_code == 200, activated_client.text
        activated_account = client.patch(
            f"/api/accounts/{account_id}",
            json={
                "fee_plan_id": plan["id"],
                "start_date": "2026-07-01",
                "end_date": exit_date,
                "status": "ACTIVE",
            },
        )
        assert activated_account.status_code == 200, activated_account.text

        requalified = client.get(f"/api/balance-snapshots?account_id={account_id}")
        assert requalified.status_code == 200, requalified.text
        imported_snapshot = next(
            item for item in requalified.json() if item["id"] == snapshot_id
        )
        assert imported_snapshot["eligible_for_closing"] is True
        assert imported_snapshot["remark"] == "季末/退出日Closing候选"

        with SessionLocal() as db:
            audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "ACCOUNT_END_DATE_UPDATED",
                    AuditEvent.entity_type == "ACCOUNT",
                    AuditEvent.entity_id == account_id,
                )
                .order_by(AuditEvent.id.desc())
            )
            assert audit is not None
            assert audit.details_json == {
                "old_end_date": None,
                "new_end_date": exit_date,
                "snapshot_eligibility_changes": [
                    {
                        "snapshot_id": snapshot_id,
                        "old_eligible_for_closing": False,
                        "new_eligible_for_closing": True,
                    }
                ],
            }

        beginning = _post(
            client,
            "/api/balance-snapshots",
            {
                "account_id": account_id,
                "as_of_date": "2026-07-01",
                "total_balance": "1000.00",
                "eligible_for_closing": False,
            },
        )
        _attach_snapshot(client, beginning["id"])
        settlement = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": customer_id,
                "platform_id": account["platform_id"],
                "fee_plan_id": plan["id"],
                "year": 2026,
                "quarter": 3,
                "start_date": "2026-07-01",
                "closing_date": exit_date,
                "account_lines": [
                    {
                        "account_id": account_id,
                        "start_date": "2026-07-01",
                        "closing_date": exit_date,
                        "beginning_snapshot_id": beginning["id"],
                        "closing_snapshot_id": snapshot_id,
                        "original_hwm": "1000.00",
                    }
                ],
            },
        )
        assert settlement.status_code == 200, settlement.text

        blocked_end_date_change = client.patch(
            f"/api/accounts/{account_id}", json={"end_date": "2026-09-01"}
        )
        assert blocked_end_date_change.status_code == 409
        assert f"#{snapshot_id}" in blocked_end_date_change.json()["detail"]
        current_account = next(
            item for item in client.get("/api/accounts").json() if item["id"] == account_id
        )
        assert current_account["end_date"] == exit_date
        current_snapshot = next(
            item
            for item in client.get(f"/api/balance-snapshots?account_id={account_id}").json()
            if item["id"] == snapshot_id
        )
        assert current_snapshot["eligible_for_closing"] is True

        finalized = client.post(f"/api/settlements/{settlement.json()['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["status"] == "FINALIZED"


def test_active_account_requires_start_date_and_legacy_missing_date_cannot_settle() -> None:
    token = uuid4().hex[:8].upper()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _active_settlement_case(client, f"START{token}")
        missing_start_payload = {
            "client_id": data["customer"]["id"],
            "platform_id": data["platform"]["id"],
            "fee_plan_id": data["plan"]["id"],
            "account_number": f"NO-START-{token}",
            "status": "ACTIVE",
        }
        rejected_create = client.post("/api/accounts", json=missing_start_payload)
        assert rejected_create.status_code == 400
        assert "开始管理日期" in rejected_create.json()["detail"]

        draft = _post(
            client,
            "/api/accounts",
            {**missing_start_payload, "status": "DRAFT"},
        )
        rejected_activation = client.patch(
            f"/api/accounts/{draft['id']}", json={"status": "ACTIVE"}
        )
        assert rejected_activation.status_code == 400
        assert "开始管理日期" in rejected_activation.json()["detail"]

        with SessionLocal() as db:
            legacy_account = db.get(SubAccount, draft["id"])
            assert legacy_account is not None
            legacy_account.status = "ACTIVE"
            db.commit()

        beginning = _post(
            client,
            "/api/balance-snapshots",
            {
                "account_id": draft["id"],
                "as_of_date": "2026-07-01",
                "total_balance": "1000.00",
                "eligible_for_closing": False,
            },
        )
        closing = _post(
            client,
            "/api/balance-snapshots",
            {
                "account_id": draft["id"],
                "as_of_date": "2026-09-30",
                "total_balance": "1200.00",
                "eligible_for_closing": True,
            },
        )
        rejected_settlement = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["customer"]["id"],
                "platform_id": data["platform"]["id"],
                "fee_plan_id": data["plan"]["id"],
                "year": 2026,
                "quarter": 3,
                "account_lines": [
                    {
                        "account_id": draft["id"],
                        "beginning_snapshot_id": beginning["id"],
                        "closing_snapshot_id": closing["id"],
                        "original_hwm": "1000.00",
                    }
                ],
            },
        )
        assert rejected_settlement.status_code == 409
        assert "缺少开始管理日期" in rejected_settlement.json()["detail"]


def test_finalize_rejects_legacy_active_account_missing_start_date() -> None:
    token = uuid4().hex[:8].upper()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _active_settlement_case(client, f"FINALSTART{token}")
        _attach_snapshot(client, data["beginning"]["id"])
        _attach_snapshot(client, data["closing"]["id"])

        calculated = client.post(
            "/api/settlements/calculate", json=data["settlement_payload"]
        )
        assert calculated.status_code == 200, calculated.text
        settlement_id = calculated.json()["id"]

        with SessionLocal() as db:
            legacy_account = db.get(SubAccount, data["account"]["id"])
            assert legacy_account is not None
            legacy_account.start_date = None
            db.commit()

        rejected = client.post(f"/api/settlements/{settlement_id}/finalize")
        assert rejected.status_code == 409
        assert "缺少开始管理日期" in rejected.json()["detail"]

        unchanged = client.get(f"/api/settlements/{settlement_id}")
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["status"] == "DRAFT"


def test_end_date_cannot_precede_non_void_settlement_closing() -> None:
    token = uuid4().hex[:8].upper()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _active_settlement_case(client, f"END{token}")
        _attach_snapshot(client, data["beginning"]["id"])
        _attach_snapshot(client, data["closing"]["id"])

        calculated = client.post(
            "/api/settlements/calculate", json=data["settlement_payload"]
        )
        assert calculated.status_code == 200, calculated.text
        settlement_id = calculated.json()["id"]
        finalized = client.post(f"/api/settlements/{settlement_id}/finalize")
        assert finalized.status_code == 200, finalized.text

        rejected = client.patch(
            f"/api/accounts/{data['account']['id']}",
            json={"end_date": "2026-08-31"},
        )
        assert rejected.status_code == 409
        assert f"Settlement #{settlement_id}" in rejected.json()["detail"]
        assert "Closing 2026-09-30" in rejected.json()["detail"]

        current_account = next(
            item
            for item in client.get("/api/accounts").json()
            if item["id"] == data["account"]["id"]
        )
        assert current_account["end_date"] is None
        current_closing = next(
            item
            for item in client.get(
                f"/api/balance-snapshots?account_id={data['account']['id']}"
            ).json()
            if item["id"] == data["closing"]["id"]
        )
        assert current_closing["eligible_for_closing"] is True
