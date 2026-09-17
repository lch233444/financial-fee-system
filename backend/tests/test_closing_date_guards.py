from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import SubAccount
from app.routes import master
from test_settlement_finalize_integrity import WRITE_HEADERS, _master, _snapshot


def _exit_case(client, suffix):
    data = _master(client, suffix.replace("-", ""))
    account_id = data["account"]["id"]
    response = client.patch(f"/api/accounts/{account_id}", json={"end_date": "2026-02-28"})
    assert response.status_code == 200, response.text
    beginning = _snapshot(client, account_id, "2026-01-01", "1000", closing=False)
    closing = _snapshot(client, account_id, "2026-02-28", "1200", closing=True)
    payload = {
        "client_id": data["client"]["id"], "platform_id": data["platform"]["id"],
        "fee_plan_id": data["plan"]["id"], "year": 2026, "quarter": 1,
        "account_lines": [{"account_id": account_id,
                           "beginning_snapshot_id": beginning["id"],
                           "closing_snapshot_id": closing["id"], "closing_date": "2026-02-28"}],
    }
    return account_id, payload


def _change_end_date(account_id, end_date):
    # Simulate the stale eligibility left by the old account-update race.
    with sqlite3.connect(get_settings().database_path) as connection:
        connection.execute("UPDATE sub_accounts SET end_date=? WHERE id=?", (end_date, account_id))


def test_end_date_update_serializes_concurrent_exit_snapshot_creation(monkeypatch):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "ENDSERIAL")
        account_id = data["account"]["id"]
        assert client.patch(f"/api/accounts/{account_id}", json={"end_date": "2026-02-28"}).status_code == 200
        reached_commit, resume_commit, snapshot_done = Event(), Event(), Event()
        original_commit = master._commit

        def paused_commit(db, message="资料重复或关联不正确"):
            if any(isinstance(row, SubAccount) and row.id == account_id and row.end_date is None for row in db.dirty):
                reached_commit.set()
                assert resume_commit.wait(10), "account update did not resume"
            return original_commit(db, message)

        def create_snapshot():
            try:
                return client.post("/api/balance-snapshots", json={
                    "account_id": account_id, "as_of_date": "2026-02-28",
                    "total_balance": "1200", "eligible_for_closing": True,
                })
            finally:
                snapshot_done.set()

        monkeypatch.setattr(master, "_commit", paused_commit)
        with ThreadPoolExecutor(max_workers=2) as executor:
            update = executor.submit(client.patch, f"/api/accounts/{account_id}", json={"end_date": None})
            try:
                assert reached_commit.wait(10)
                snapshot = executor.submit(create_snapshot)
                # Without the writer lock the actual competing HTTP request finishes
                # against the old date; with it, it waits until the update commits.
                snapshot_done.wait(0.3)
            finally:
                resume_commit.set()
            updated = update.result(timeout=10)
            created = snapshot.result(timeout=10)
        assert updated.status_code == 200, updated.text
        assert created.status_code == 400, created.text
        assert "实际退出日" in created.json()["detail"]
        assert client.get("/api/balance-snapshots", params={"account_id": account_id}).json() == []


@pytest.mark.parametrize("end_date", [None, "2026-03-15"])
def test_calculate_rechecks_current_end_date_despite_stale_eligibility(end_date):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        account_id, payload = _exit_case(client, f"CALC{end_date}")
        _change_end_date(account_id, end_date)
        response = client.post("/api/settlements/calculate", json=payload)
        assert response.status_code == 400, response.text
        assert "实际退出日" in response.json()["detail"]


@pytest.mark.parametrize("end_date", [None, "2026-03-15"])
def test_finalize_rechecks_current_end_date_despite_stale_eligibility(end_date):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        account_id, payload = _exit_case(client, f"FIN{end_date}")
        calculated = client.post("/api/settlements/calculate", json=payload)
        assert calculated.status_code == 200, calculated.text
        _change_end_date(account_id, end_date)
        response = client.post(f"/api/settlements/{calculated.json()['id']}/finalize")
        assert response.status_code == 409, response.text
        assert "实际退出日" in response.json()["detail"]


@pytest.mark.parametrize("end_date", [None, "2026-03-15"])
def test_direct_finalize_rejects_closing_that_is_no_longer_actual_exit(end_date):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        account_id, payload = _exit_case(client, f"SQL{end_date}")
        calculated = client.post("/api/settlements/calculate", json=payload)
        assert calculated.status_code == 200, calculated.text
        _change_end_date(account_id, end_date)
        with sqlite3.connect(get_settings().database_path) as connection:
            with pytest.raises(sqlite3.IntegrityError, match="settlement_closing_date_invalid"):
                connection.execute(
                    "UPDATE quarterly_settlements SET status='FINALIZED', finalized_at=CURRENT_TIMESTAMP WHERE id=?",
                    (calculated.json()["id"],),
                )


@pytest.mark.parametrize("status", [None, "DRAFT", "CLOSED", "ACTIVE"])
def test_closed_client_cannot_create_account_in_any_status(status):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, f"CLOSED{status}")
        client_id = data["client"]["id"]
        assert client.patch(f"/api/clients/{client_id}", json={"status": "CLOSED"}).status_code == 200
        before = client.get("/api/accounts", params={"client_id": client_id}).json()
        payload = {"client_id": client_id, "platform_id": data["platform"]["id"],
                   "fee_plan_id": data["plan"]["id"], "account_number": f"NEW{status}",
                   "start_date": "2026-04-01"}
        if status is not None:
            payload["status"] = status
        response = client.post("/api/accounts", json=payload)
        assert response.status_code == 400, response.text
        assert "已关闭" in response.json()["detail"]
        assert client.get("/api/accounts", params={"client_id": client_id}).json() == before


def test_draft_client_can_still_create_draft_account():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        customer = client.post("/api/clients", json={"name": "Synthetic draft closing guard", "status": "DRAFT"})
        assert customer.status_code == 201, customer.text
        response = client.post("/api/accounts", json={"client_id": customer.json()["id"], "account_number": "DRAFT-GUARD"})
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "DRAFT"
