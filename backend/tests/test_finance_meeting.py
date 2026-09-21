from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import Attachment, AuditEvent, TransactionRecord
from evidence_fixtures import upload_evidence
from test_settlement_finalize_integrity import WRITE_HEADERS, _master, _snapshot, _calculate


def transaction_payload(client, account_id, kind="CONTRIBUTION", **changes):
    return {"account_id": account_id, "transaction_date": "2026-02-01",
            "transaction_type": kind, "amount": "100.00", "remark": "银行加款原件",
            "attachment_ids": [upload_evidence(client, "TRANSACTION")], **changes}


def test_three_types_sum_without_reclassifying_legacy_and_date_only_closing():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "MT" + uuid4().hex[:8])
        account = data["account"]
        for kind in ("CONTRIBUTION", "MONTHLY_CONTRIBUTION", "WITHDRAWAL"):
            response = client.post("/api/transactions", json=transaction_payload(client, account["id"], kind))
            assert response.status_code == 201, response.text
            if kind == "CONTRIBUTION":
                assert response.json()["remark"] == f"到账日期：2026-02-01\n户口：{account['account_number']}\n金额：HKD 100.00\n备注：银行加款原件"
                assert response.json()["remark_note"] == "银行加款原件"
        opening = _snapshot(client, account["id"], "2026-01-01", "1000", closing=False)
        closing = _snapshot(client, account["id"], "2026-03-31", "1500", closing=False)
        assert "eligible_for_closing" not in closing
        draft = _calculate(client, data, opening, closing)
        assert (draft["contribution"], draft["withdrawal"], draft["gain_loss"], draft["service_fee"]) == ("200.00", "100.00", "400.00", "80.00")
        # Direct SQL uses the same two incoming types and accepts inert false flags.
        with SessionLocal() as db:
            db.execute(text("UPDATE quarterly_settlements SET status='FINALIZED', finalized_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": draft["id"]})
            db.commit()


def test_creation_requires_real_unclaimed_proof_and_topup_note():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "REQ" + uuid4().hex[:8])
        body = transaction_payload(client, data["account"]["id"])
        for update in ({"attachment_ids": []}, {"attachment_ids": [99999999]}, {"remark": " "}, {"remark": "长" * 500}):
            assert client.post("/api/transactions", json={**body, **update}).status_code in {400, 422}
        missing = {key: value for key, value in body.items() if key != "attachment_ids"}
        assert client.post("/api/transactions", json=missing).status_code == 422
        wrong = upload_evidence(client, "SNAPSHOT")
        assert client.post("/api/transactions", json={**body, "attachment_ids": [wrong]}).status_code == 400
        with SessionLocal() as db:
            proof = db.get(Attachment, body["attachment_ids"][0])
            path = Path(proof.stored_path)
            original = path.read_bytes()
        try:
            path.write_bytes(b"corrupt")
            assert client.post("/api/transactions", json=body).status_code == 400
        finally:
            path.write_bytes(original)
        with SessionLocal() as db:
            assert db.get(Attachment, body["attachment_ids"][0]).entity_id is None
            assert not db.scalar(select(TransactionRecord.id).where(TransactionRecord.account_id == data["account"]["id"]))
        created = client.post("/api/transactions", json=body)
        assert created.status_code == 201
        assert client.post("/api/transactions", json=body).status_code == 409
        fake_image = client.post("/api/attachments", data={"entity_type": "SNAPSHOT"}, files={"file": ("fake.png", b"not an image", "image/png")})
        assert fake_image.status_code == 400
        snapshot_body = {"account_id": data["account"]["id"], "as_of_date": "2026-01-01", "total_balance": "1000"}
        assert client.post("/api/balance-snapshots", json=snapshot_body).status_code == 422
        images = [upload_evidence(client, "SNAPSHOT"), upload_evidence(client, "SNAPSHOT")]
        response = client.post("/api/balance-snapshots", json={**snapshot_body, "attachment_ids": images})
        assert response.status_code == 201, response.text
        proofs = client.get("/api/attachments", params={"entity_type": "SNAPSHOT", "entity_id": response.json()["id"]}).json()
        assert {item["id"] for item in proofs} == set(images)


def test_replacement_keeps_immutable_originals_and_atomic_claim():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "REP" + uuid4().hex[:8])
        body = transaction_payload(client, data["account"]["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: client.post("/api/transactions", json=body), range(2)))
        assert sorted(result.status_code for result in results) == [201, 409]
        created = next(result.json() for result in results if result.status_code == 201)
        replacement = upload_evidence(client, "TRANSACTION")
        response = client.patch(f"/api/transactions/{created['id']}", json={**body, "attachment_ids": [replacement], "correction_reason": "凭证补正"})
        assert response.status_code == 200, response.text
        assert response.json()["evidence_count"] == 1
        assert response.json()["superseded_attachment_ids"] == body["attachment_ids"]
        assert client.get(f"/api/attachments/{body['attachment_ids'][0]}/file").status_code == 200
        with SessionLocal() as db:
            event = db.scalar(select(AuditEvent).where(AuditEvent.action == "TRANSACTION_CORRECTED", AuditEvent.entity_id == created["id"]))
            assert event.details_json["before_attachment_ids"] == body["attachment_ids"]
            assert event.details_json["after_attachment_ids"] == [replacement]
            for sql in ("UPDATE attachments SET superseded=0 WHERE id=:id", "DELETE FROM attachments WHERE id=:id", "UPDATE attachments SET sha256='tampered' WHERE id=:id", "UPDATE attachments SET id=id+1000000 WHERE id=:id"):
                with pytest.raises(IntegrityError, match="financial_evidence_history_immutable"):
                    db.execute(text(sql), {"id": body["attachment_ids"][0]})
                db.rollback()


def test_fc_and_plan_public_schema_has_no_code_and_same_name_is_allowed():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        for endpoint, schema in (("fcs", "FCCreate"), ("fee-plans", "FeePlanCreate")):
            ids = []
            for _ in range(2):
                result = client.post(f"/api/{endpoint}", json={"name": "同名资料"})
                assert result.status_code == 201, result.text
                assert "code" not in result.json()
                ids.append(result.json()["id"])
            assert len(set(ids)) == 2
            assert "code" not in client.get("/api/openapi.json").json()["components"]["schemas"][schema]["properties"]


def test_duplicate_snapshot_and_failed_commit_leave_uploads_unclaimed(monkeypatch):
    from app.routes import master
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "ATOMIC" + uuid4().hex[:8])
        account_id = data["account"]["id"]
        _snapshot(client, account_id, "2026-01-01", "1000", closing=False)
        proof_id = upload_evidence(client, "SNAPSHOT")
        duplicate = client.post("/api/balance-snapshots", json={"account_id": account_id,
                                "as_of_date": "2026-01-01", "total_balance": "2000", "attachment_ids": [proof_id]})
        assert duplicate.status_code == 409, duplicate.text
        with SessionLocal() as db:
            assert db.get(Attachment, proof_id).entity_id is None
        body = transaction_payload(client, account_id)
        def failed_commit(_db, _message):
            raise RuntimeError("synthetic record commit failure")
        monkeypatch.setattr(master, "_commit", failed_commit)
        with pytest.raises(RuntimeError, match="synthetic record commit failure"):
            client.post("/api/transactions", json=body)
        with SessionLocal() as db:
            assert db.get(Attachment, body["attachment_ids"][0]).entity_id is None
            assert db.scalar(select(TransactionRecord.id).where(TransactionRecord.account_id == account_id)) is None


def test_replacement_rejects_locked_original_and_target_dates_without_claiming():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "LOCK" + uuid4().hex[:8])
        account_id = data["account"]["id"]
        body = transaction_payload(client, account_id)
        original = client.post("/api/transactions", json=body).json()
        opening = _snapshot(client, account_id, "2026-01-01", "1000", closing=False)
        closing = _snapshot(client, account_id, "2026-03-31", "1200", closing=False)
        draft = _calculate(client, data, opening, closing)
        assert client.post(f"/api/settlements/{draft['id']}/finalize").status_code == 200
        replacement = upload_evidence(client, "TRANSACTION")
        future = client.post("/api/transactions", json=transaction_payload(client, account_id, transaction_date="2026-04-01")).json()
        for record, day in ((original, "2026-04-01"), (future, "2026-02-01")):
            response = client.patch(f"/api/transactions/{record['id']}", json={**body,
                                    "transaction_date": day, "attachment_ids": [replacement], "correction_reason": "日期更正"})
            assert response.status_code == 409, response.text
        with SessionLocal() as db:
            assert db.get(Attachment, replacement).entity_id is None
            assert db.get(Attachment, body["attachment_ids"][0]).superseded is False
            with pytest.raises(IntegrityError, match="attachment_used_by_finalized_settlement"):
                db.execute(text("UPDATE attachments SET superseded=1 WHERE id=:id"), {"id": body["attachment_ids"][0]})
            db.rollback()


def test_failed_concurrent_identical_upload_cannot_delete_successful_upload():
    import asyncio
    from io import BytesIO
    from threading import Event
    from fastapi import UploadFile
    from sqlalchemy.orm import Session
    from app.database import engine
    from app.routes.attachments import upload_attachment
    from evidence_fixtures import synthetic_image
    cleanup_read, second_staged, cleanup_done = Event(), Event(), Event()
    class FailingSession(Session):
        def flush(self, objects=None):
            if not self.info.get("failed") and any(isinstance(item, Attachment) for item in self.new):
                self.info["failed"] = True
                raise RuntimeError("synthetic upload failure")
            return super().flush(objects)
        def scalar(self, *args, **kwargs):
            value = super().scalar(*args, **kwargs)
            if self.info.get("failed"):
                assert value is None
                cleanup_read.set()
                assert second_staged.wait(10)
            return value
    class SuccessfulSession(Session):
        def commit(self):
            second_staged.set()
            assert cleanup_done.wait(10)
            return super().commit()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, "RACE" + uuid4().hex[:8])
        record = client.post("/api/transactions", json=transaction_payload(client, data["account"]["id"])).json()
        image_bytes = synthetic_image()
        def failed_upload():
            try:
                with FailingSession(bind=engine) as db:
                    with pytest.raises(RuntimeError, match="synthetic upload failure"):
                        asyncio.run(upload_attachment(UploadFile(BytesIO(image_bytes), filename="same.png"), "TRANSACTION", record["id"], db))
            finally:
                cleanup_done.set()
        def successful_upload():
            assert cleanup_read.wait(10)
            with SuccessfulSession(bind=engine) as db:
                return asyncio.run(upload_attachment(UploadFile(BytesIO(image_bytes), filename="same.png"), "TRANSACTION", record["id"], db))
        with ThreadPoolExecutor(max_workers=2) as pool:
            failed = pool.submit(failed_upload)
            success = pool.submit(successful_upload)
            failed.result(timeout=15)
            result = success.result(timeout=15)
        response = client.get(f"/api/attachments/{result['id']}/file")
        assert response.status_code == 200
        assert response.content == image_bytes
