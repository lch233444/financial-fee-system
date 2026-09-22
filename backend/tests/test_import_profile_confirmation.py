from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, BalanceSnapshot, Client, StatementImport, SubAccount
from app.routes import statements
from app.schemas import StatementConfirmRequest
from import_profile_fixtures import confirmation_profile
from test_client_association import post, statement


@pytest.fixture
def api():
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as client:
        yield client


def case(api):
    tag = uuid4().hex[:12]
    import_id, payload = statement(f"PROFILE {tag}", f"PROFILE-{tag}", f"SCHEME {tag}")
    payload["profile"] = confirmation_profile(api)
    return import_id, payload


def counts():
    with SessionLocal() as db:
        return [db.scalar(select(func.count()).select_from(model))
                for model in (Client, SubAccount, BalanceSnapshot, AuditEvent)]


def legacy_case(api):
    import_id, payload = case(api)
    result = post(api, f"/api/statement-imports/{import_id}/confirm", payload)
    # Reproduce a pre-upgrade confirmed statement linked to incomplete profiles.
    with SessionLocal() as db:
        account = db.get(SubAccount, result["account_id"])
        account.status = "DRAFT"
        account.fee_plan_id = None
        account.start_date = None
        account.client.status = "DRAFT"
        account.client.fc_id = None
        account.client.management_start_date = None
        db.commit()
    return import_id, payload, result


def test_one_confirmation_activates_new_client_account_and_posts_one_snapshot(api):
    import_id, payload = case(api)
    result = post(api, f"/api/statement-imports/{import_id}/confirm", payload)
    assert result["created_account"] and not result["created_draft"]
    with SessionLocal() as db:
        account = db.get(SubAccount, result["account_id"])
        assert account.status == account.client.status == "ACTIVE"
        assert account.fee_plan_id == payload["profile"]["fee_plan_id"]
        assert account.client.fc_id == payload["profile"]["fc_id"]
        assert account.start_date == date(2026, 1, 1)
        assert db.get(BalanceSnapshot, result["snapshot"]["id"]).account_id == account.id
    assert api.post(f"/api/statement-imports/{import_id}/confirm", json=payload).status_code == 409


@pytest.mark.parametrize("change", ["missing", "fc", "plan", "dates"])
def test_invalid_profile_does_not_leave_partial_client_account_or_balance(api, change):
    import_id, payload = case(api)
    if change == "missing":
        payload.pop("profile")
    elif change == "fc":
        payload["profile"].pop("fc_id")
    elif change == "plan":
        payload["profile"]["fee_plan_id"] = 999999999
    else:
        payload["profile"]["end_date"] = "2025-12-31"
    before = counts()
    assert api.post(f"/api/statement-imports/{import_id}/confirm", json=payload).status_code in (400, 404)
    assert counts() == before
    with SessionLocal() as db:
        assert db.get(StatementImport, import_id).status == "NEEDS_REVIEW"


def test_existing_draft_client_and_account_are_completed_without_duplicate_identity(api):
    import_id, payload = case(api)
    owner = post(api, "/api/clients", {"name": payload["client_name"]})
    platform = post(api, "/api/platforms", {"name": payload["scheme_name"], "code": uuid4().hex})
    account = post(api, "/api/accounts", {"client_id": owner["id"], "platform_id": platform["id"],
                   "account_number": payload["account_number"], "scheme_name": payload["scheme_name"]})
    payload.update(client_id=owner["id"], account_id=account["id"], account_platform_id=platform["id"])
    before = counts()
    result = post(api, f"/api/statement-imports/{import_id}/confirm", payload)
    assert result["client_id"] == owner["id"] and result["account_id"] == account["id"]
    assert not result["created_client"] and not result["created_account"]
    assert counts()[:2] == before[:2]


def test_existing_active_client_details_cannot_be_overwritten(api):
    import_id, payload = case(api)
    p = payload["profile"]
    owner = post(api, "/api/clients", {"name": payload["client_name"], "fc_id": p["fc_id"],
                 "management_start_date": "2025-01-01", "status": "ACTIVE", "contact": "ORIGINAL"})
    payload["client_id"] = owner["id"]
    before = counts()
    assert api.post(f"/api/statement-imports/{import_id}/confirm", json=payload).status_code == 409
    assert counts() == before
    p.pop("fc_id")
    p.pop("management_start_date")
    result = post(api, f"/api/statement-imports/{import_id}/confirm", payload)
    with SessionLocal() as db:
        assert db.get(Client, owner["id"]).contact == "ORIGINAL"
        assert db.get(SubAccount, result["account_id"]).status == "ACTIVE"


def test_confirmation_commit_failure_rolls_back_profile_and_snapshot(api, monkeypatch):
    import_id, payload = case(api)
    before = counts()
    with SessionLocal() as db:
        def fail():
            raise RuntimeError("injected commit failure")
        monkeypatch.setattr(db, "commit", fail)
        with pytest.raises(RuntimeError, match="injected"):
            statements.confirm_statement(import_id, StatementConfirmRequest(**payload), db)
    assert counts() == before


def test_legacy_confirmation_keeps_original_snapshot_review_and_file(api):
    import_id, payload, result = legacy_case(api)
    before = api.get(f"/api/statement-imports/{import_id}").json()
    original_file = api.get(f"/api/statement-imports/{import_id}/file").content
    with SessionLocal() as db:
        balance_before = db.get(BalanceSnapshot, result["snapshot"]["id"]).total_balance_cents
    snapshot_count = counts()[2]
    response = api.post(f"/api/statement-imports/{import_id}/confirm-profile", json=payload["profile"])
    assert response.status_code == 200, response.text
    after = api.get(f"/api/statement-imports/{import_id}").json()
    assert after == before
    assert counts()[2] == snapshot_count
    assert api.get(f"/api/statement-imports/{import_id}/file").content == original_file
    with SessionLocal() as db:
        account = db.get(SubAccount, result["account_id"])
        assert account.status == account.client.status == "ACTIVE"
        assert db.get(BalanceSnapshot, result["snapshot"]["id"]).total_balance_cents == balance_before


def test_concurrent_legacy_confirmation_only_activates_once(api):
    import_id, payload, result = legacy_case(api)
    before = counts()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: api.post(f"/api/statement-imports/{import_id}/confirm-profile", json=payload["profile"]), range(2)))
    assert sorted(response.status_code for response in results) == [200, 409]
    assert counts() == [*before[:3], before[3] + 1]


def test_active_account_import_cannot_change_plan_or_dates(api):
    import_id, payload = case(api)
    first = post(api, f"/api/statement-imports/{import_id}/confirm", payload)
    next_id, next_payload = statement(payload["client_name"], payload["account_number"], payload["scheme_name"], day="2026-06-30")
    next_payload.update(client_id=first["client_id"], profile=payload["profile"])
    before = counts()
    assert api.post(f"/api/statement-imports/{next_id}/confirm", json=next_payload).status_code == 409
    assert counts() == before
    next_payload.pop("profile")
    assert api.post(f"/api/statement-imports/{next_id}/confirm", json=next_payload).status_code == 200


def test_duplicate_snapshot_rolls_back_existing_draft_activation(api):
    import_id, payload, first = legacy_case(api)
    next_id, next_payload = statement(payload["client_name"], payload["account_number"], payload["scheme_name"])
    next_payload.update(client_id=first["client_id"], profile=payload["profile"])
    before = counts()
    response = api.post(f"/api/statement-imports/{next_id}/confirm", json=next_payload)
    assert response.status_code == 409 and "同一天" in response.text
    assert counts() == before
    with SessionLocal() as db:
        assert db.get(SubAccount, first["account_id"]).status == "DRAFT"
        assert db.get(Client, first["client_id"]).status == "DRAFT"
        assert db.get(StatementImport, next_id).status == "NEEDS_REVIEW"


def test_draft_without_platform_can_be_completed_in_the_import(api):
    import_id, payload = case(api)
    owner = post(api, "/api/clients", {"name": payload["client_name"]})
    account = post(api, "/api/accounts", {"client_id": owner["id"], "account_number": payload["account_number"]})
    platform = post(api, "/api/platforms", {"name": payload["scheme_name"], "code": uuid4().hex})
    payload.update(client_id=owner["id"], account_id=account["id"], account_platform_id=None)
    payload["profile"]["platform_id"] = platform["id"]
    response = api.post(f"/api/statement-imports/{import_id}/confirm", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["account_id"] == account["id"]
