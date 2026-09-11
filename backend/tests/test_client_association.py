from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from sqlalchemy import select, text

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Client, StatementImport, SubAccount
from app.routes import master
from app.schemas import ClientMergeRequest
from app.services.entity_ids import allocate_entity_id


def post(api, path, payload):
    response = api.post(path, json=payload)
    assert response.is_success, response.text
    return response.json()


def statement(name, number, scheme, day="2026-03-31", balance="1000.00"):
    payload = dict(client_name=name, account_number=number, scheme_name=scheme,
                   as_of_date=day, total_balance=balance, holdings=[])
    buffer = BytesIO()
    metadata = PngInfo()
    metadata.add_text("sample", uuid4().hex)
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG", pnginfo=metadata)
    path = get_settings().data_root / "statement_imports" / f"association-{uuid4().hex}.png"
    path.write_bytes(buffer.getvalue())
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        item = StatementImport(id=allocate_entity_id(db, StatementImport), original_name=path.name,
                               stored_path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                               mime_type="image/png", status="NEEDS_REVIEW", parser_version="test",
                               extracted_json={**payload, "document_type": "empf_account_page"},
                               confidence_json={}, warnings_json=[])
        db.add(item)
        db.commit()
        return item.id, payload


def confirm(api, name, number, scheme, **selection):
    import_id, payload = statement(name, number, scheme)
    return api.post(f"/api/statement-imports/{import_id}/confirm", json={**payload, **selection})


def seed_pair(api):
    tag = uuid4().hex[:12]
    company = post(api, "/api/companies", {"name": f"ASSOCIATION {tag}", "code": tag})
    fc = post(api, "/api/fcs", {"company_id": company["id"], "name": "TEST FC", "code": "FC"})
    scheme = f"SYNTHETIC SCHEME {tag}"
    platform = post(api, "/api/platforms", {"name": scheme, "code": tag})
    plans = [post(api, "/api/fee-plans", {"company_id": company["id"], "name": f"Plan {rate}",
              "code": str(rate), "fee_rate_percent": rate}) for rate in (20, 10)]
    data = {"name": f"SYNTHETIC CLIENT {tag}", "company_id": company["id"], "fc_id": fc["id"],
            "management_start_date": "2026-03-31", "status": "ACTIVE"}
    clients = [post(api, "/api/clients", data) for _ in range(2)]
    accounts, snapshots, imports = [], [], []
    for index, client in enumerate(clients):
        account = post(api, "/api/accounts", {"client_id": client["id"], "platform_id": platform["id"],
                       "fee_plan_id": plans[index]["id"], "account_number": f"ACC-{tag}-{index}",
                       "scheme_name": scheme, "start_date": "2026-03-31", "status": "ACTIVE"})
        accounts.append(account)
        dates = []
        for day, balance in (("2026-03-31", "1000.00"), ("2026-06-30", "1200.00")):
            import_id, payload = statement(client["name"], account["account_number"], scheme, day, balance)
            result = post(api, f"/api/statement-imports/{import_id}/confirm", {
                **payload, "account_id": account["id"], "account_platform_id": platform["id"]})
            dates.append(result["snapshot"]["id"])
            imports.append(import_id)
        snapshots.append(dates)
    return dict(clients=clients, accounts=accounts, snapshots=snapshots, imports=imports,
                plans=plans, platform=platform, name=data["name"], scheme=scheme)


def merge(api, data):
    return api.post(f"/api/clients/{data['clients'][1]['id']}/merge", json={
        "target_client_id": data["clients"][0]["id"], "reason": "财务已确认两个档案属于同一人"})


def account_owners(data):
    with SessionLocal() as db:
        return [db.get(SubAccount, item["id"]).client_id for item in data["accounts"]]


def test_new_account_requires_existing_customer_selection_and_keeps_one_customer():
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as api:
        name = f"NEW CUSTOMER {uuid4().hex}"
        first = confirm(api, name, f"FIRST-{uuid4().hex}", "IMPORT ASSOCIATION SCHEME")
        assert first.status_code == 200, first.text
        owner = first.json()["client_id"]
        assert first.json()["created_client"] is True
        second_number = f"SECOND-{uuid4().hex}"
        rejected = confirm(api, f"  {name.lower().replace(' ', '   ')}  ", second_number, "IMPORT ASSOCIATION SCHEME")
        assert rejected.status_code == 409 and "已有同名客户" in rejected.text
        second = confirm(api, name, second_number, "IMPORT ASSOCIATION SCHEME", client_id=owner)
        assert second.status_code == 200, second.text
        assert second.json()["client_id"] == owner
        assert second.json()["created_draft"] is True and second.json()["created_client"] is False
        with SessionLocal() as db:
            assert len(db.scalars(select(Client).where(Client.name == name)).all()) == 1
            assert len(db.scalars(select(SubAccount).where(SubAccount.client_id == owner)).all()) == 2


def test_customer_name_and_existing_account_owner_must_agree():
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as api:
        d = seed_pair(api)
        target, source = [c["id"] for c in d["clients"]]
        response = confirm(api, d["name"], d["accounts"][1]["account_number"], d["scheme"],
                           client_id=target, account_id=d["accounts"][1]["id"], account_platform_id=d["platform"]["id"])
        assert response.status_code == 409 and "不属于" in response.text
        response = confirm(api, "DIFFERENT PERSON", "DIFFERENT-ACCOUNT", d["scheme"], client_id=source)
        assert response.status_code == 409 and "Client Name" in response.text
        response = confirm(api, d["name"], d["accounts"][1]["account_number"], d["scheme"], client_id=target)
        assert response.status_code == 409 and "其他客户" in response.text
        assert account_owners(d) == [target, source]


def test_merge_preserves_accounts_plans_snapshots_and_produces_one_multi_plan_invoice():
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as api:
        d = seed_pair(api)
        target, source = [c["id"] for c in d["clients"]]
        before = {s["id"]: s for s in api.get("/api/balance-snapshots").json() if s["id"] in sum(d["snapshots"], [])}
        assert merge(api, d).status_code == 200
        assert account_owners(d) == [target, target]
        after = {s["id"]: s for s in api.get("/api/balance-snapshots").json() if s["id"] in before}
        assert set(after) == set(before)
        for key, value in after.items():
            assert value == {**before[key], "client_id": target}
        with SessionLocal() as db:
            assert db.get(Client, source) is None
            assert [db.get(SubAccount, a["id"]).fee_plan_id for a in d["accounts"]] == [p["id"] for p in d["plans"]]
            assert [db.get(StatementImport, i).confirmed_account_id for i in d["imports"]] == [d["accounts"][0]["id"]] * 2 + [d["accounts"][1]["id"]] * 2
            audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "CLIENT_MERGED", AuditEvent.entity_id == target))
            assert audit.details_json["source_client"]["id"] == source
        for index, account in enumerate(d["accounts"]):
            settlement = post(api, "/api/settlements/calculate", {"client_id": target,
                "platform_id": d["platform"]["id"], "fee_plan_id": d["plans"][index]["id"], "year": 2026, "quarter": 2,
                "account_lines": [{"account_id": account["id"], "beginning_snapshot_id": d["snapshots"][index][0],
                                   "closing_snapshot_id": d["snapshots"][index][1], "original_hwm": "1000.00"}]})
            post(api, f"/api/settlements/{settlement['id']}/finalize", {})
        invoice = post(api, "/api/invoices", {"client_id": target, "year": 2026, "quarter": 2})
        assert invoice["amount"] == "60.00" and invoice["source_count"] == 2
        fresh = post(api, "/api/clients", {"name": f"FRESH {uuid4().hex}"})
        assert fresh["id"] > source


@pytest.mark.parametrize("change", ["name", "company_id", "fc_id", "management_start_date", "contact", "status"])
def test_merge_rejects_conflicting_or_closed_customer_metadata(change):
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as api:
        d = seed_pair(api)
        source, target = d["clients"][1]["id"], d["clients"][0]["id"]
        other = seed_pair(api) if change in ("company_id", "fc_id") else None
        with SessionLocal() as db:
            client = db.get(Client, source)
            if change in ("company_id", "fc_id"):
                # A separate valid entity proves that non-null ownership conflicts cannot be guessed away.
                setattr(client, change, other["clients"][0][change])
            elif change == "management_start_date":
                from datetime import date
                client.management_start_date = date(2026, 1, 1)
            elif change == "status":
                client.status = "CLOSED"
            elif change == "contact":
                client.contact = "CONTACT A"
                db.get(Client, target).contact = "CONTACT B"
            else:
                client.name = "DIFFERENT PERSON"
            db.commit()
        assert merge(api, d).status_code == 409
        assert account_owners(d) == [target, source]


def test_merge_rejects_any_existing_settlement_and_has_no_partial_changes():
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as api:
        d = seed_pair(api)
        target, source = [c["id"] for c in d["clients"]]
        post(api, "/api/settlements/calculate", {"client_id": source, "platform_id": d["platform"]["id"],
            "fee_plan_id": d["plans"][1]["id"], "year": 2026, "quarter": 2,
            "account_lines": [{"account_id": d["accounts"][1]["id"], "beginning_snapshot_id": d["snapshots"][1][0],
                               "closing_snapshot_id": d["snapshots"][1][1], "original_hwm": "1000.00"}]})
        assert merge(api, d).status_code == 409
        assert account_owners(d) == [target, source]


def test_merge_rolls_back_account_moves_if_commit_fails(monkeypatch):
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as api:
        d = seed_pair(api)
        target, source = [c["id"] for c in d["clients"]]
        def fail(*args, **kwargs):
            raise RuntimeError("injected commit failure")
        monkeypatch.setattr(master, "_commit_controlled_delete", fail)
        with pytest.raises(RuntimeError, match="injected"), SessionLocal() as db:
            master.merge_client(source, ClientMergeRequest(target_client_id=target, reason="同一客户重复建档"), db)
        assert account_owners(d) == [target, source]
        with SessionLocal() as db:
            assert db.get(Client, source) is not None
            assert db.scalar(select(AuditEvent).where(AuditEvent.action == "CLIENT_MERGED", AuditEvent.entity_id == target)) is None


def test_concurrent_merge_only_moves_and_audits_once():
    with TestClient(app, headers={"X-Financial-System-Request": "1"}) as api:
        d = seed_pair(api)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: merge(api, d), range(2)))
        assert sorted(r.status_code for r in responses) == [200, 404]
        assert account_owners(d) == [d["clients"][0]["id"]] * 2
        with SessionLocal() as db:
            events = db.scalars(select(AuditEvent).where(AuditEvent.action == "CLIENT_MERGED", AuditEvent.entity_id == d["clients"][0]["id"])).all()
            assert len(events) == 1
