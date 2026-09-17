from hashlib import sha256

from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, BalanceSnapshot, Client, StatementImport, SubAccount
from app.services.entity_ids import allocate_entity_id
from test_payment_corrections import WRITE_HEADERS


@pytest.mark.parametrize("damage", [None, "missing", "changed", "outside"])
def test_confirmation_requires_original_source_before_any_business_write(tmp_path, damage):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        source = get_settings().data_root / "statement_imports" / f"confirm-source-{damage}.png"
        Image.new("RGB", (12 + [None, "missing", "changed", "outside"].index(damage), 12), "white").save(source)
        expected_hash = sha256(source.read_bytes()).hexdigest()
        if damage == "outside":
            outside = tmp_path / "outside.png"
            outside.write_bytes(source.read_bytes())
            source = outside
        with SessionLocal() as db:
            db.execute(text("BEGIN IMMEDIATE"))
            item = StatementImport(id=allocate_entity_id(db, StatementImport), original_name=source.name,
                stored_path=str(source), sha256=expected_hash, mime_type="image/png", parser_name="SYNTHETIC", parser_version="1",
                status="NEEDS_REVIEW", extracted_json={"document_type": "empf_account_page", "holdings": []}, confidence_json={}, warnings_json=[])
            db.add(item)
            db.commit()
            import_id = item.id
            before = [db.scalar(select(func.count()).select_from(model)) for model in (Client, SubAccount, BalanceSnapshot, AuditEvent)]
        if damage == "missing":
            source.unlink()
        elif damage == "changed":
            Image.new("RGB", (15, 15), "black").save(source)
        response = client.post(f"/api/statement-imports/{import_id}/confirm", json={
            "client_name": f"Synthetic source {damage}", "account_number": f"SRC-{damage}",
            "scheme_name": "Synthetic", "as_of_date": "2026-06-30", "total_balance": "123.45"})
        if damage is None:
            assert response.status_code == 200, response.text
            with SessionLocal() as db:
                assert db.get(StatementImport, import_id).status == "CONFIRMED"
        else:
            assert response.status_code == 409, response.text
            assert "原件" in response.json()["detail"]
            with SessionLocal() as db:
                item = db.get(StatementImport, import_id)
                assert item.status == "NEEDS_REVIEW" and item.confirmed_snapshot_id is None
                assert [db.scalar(select(func.count()).select_from(model)) for model in (Client, SubAccount, BalanceSnapshot, AuditEvent)] == before
