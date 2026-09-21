"""Real synthetic evidence for explicit financial record test setup."""
from io import BytesIO
from uuid import uuid4
from PIL import Image, PngImagePlugin


def synthetic_image() -> bytes:
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("synthetic-fixture", uuid4().hex)
    Image.new("RGB", (4, 4), "white").save(output, format="PNG", pnginfo=info)
    return output.getvalue()


def upload_evidence(client, entity_type: str, entity_id: int | None = None) -> int:
    data = {"entity_type": entity_type}
    if entity_id is not None:
        data["entity_id"] = str(entity_id)
    response = client.post("/api/attachments", data=data,
                           files={"file": ("synthetic-proof.png", synthetic_image(), "image/png")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def legacy_snapshot(account_id, as_of_date, balance):
    """Seed pre-meeting proofless history, never a current API shortcut."""
    from datetime import date
    from decimal import Decimal
    from app.database import SessionLocal
    from app.models import BalanceSnapshot
    from app.services.entity_ids import allocate_entity_id
    from sqlalchemy import text
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        row = BalanceSnapshot(id=allocate_entity_id(db, BalanceSnapshot), account_id=account_id,
                              as_of_date=date.fromisoformat(as_of_date),
                              total_balance_cents=int(Decimal(balance) * 100), source_type="MANUAL")
        db.add(row)
        db.commit()
        return {"id": row.id, "account_id": account_id, "as_of_date": as_of_date, "total_balance": balance}


def legacy_transaction(account_id, transaction_date, amount, transaction_type="CONTRIBUTION", remark=None):
    """Seed an original proofless transaction for legacy-finalize guard tests."""
    from datetime import date
    from decimal import Decimal
    from app.database import SessionLocal
    from app.models import TransactionRecord
    with SessionLocal() as db:
        row = TransactionRecord(account_id=account_id, transaction_date=date.fromisoformat(transaction_date),
                                transaction_type=transaction_type, amount_cents=int(Decimal(amount) * 100), remark=remark)
        db.add(row)
        db.commit()
        return {"id": row.id, "account_id": account_id, "transaction_date": transaction_date,
                "amount": amount, "transaction_type": transaction_type, "remark": remark}
