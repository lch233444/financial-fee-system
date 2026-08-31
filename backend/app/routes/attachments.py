from __future__ import annotations

import mimetypes
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import (
    Attachment,
    AuditEvent,
    BalanceSnapshot,
    Payment,
    PaymentRefund,
    SubAccount,
    TransactionRecord,
)
from ..services.storage import is_within, sha256_bytes, store_bytes


router = APIRouter(prefix="/api/attachments", tags=["attachments"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf", ".xlsx", ".xls", ".csv"}
ENTITY_MODELS = {
    "ACCOUNT": SubAccount,
    "SNAPSHOT": BalanceSnapshot,
    "TRANSACTION": TransactionRecord,
    "PAYMENT": Payment,
    "PAYMENT_REFUND": PaymentRefund,
}
UNCLAIMED_PROOF_TYPES = {"PAYMENT", "PAYMENT_REFUND"}


def _attachment_dict(item: Attachment) -> dict:
    return {
        "id": item.id,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "original_name": item.original_name,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "created_at": item.created_at.isoformat(),
    }


@router.get("")
def list_attachments(
    entity_type: str | None = None,
    entity_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(Attachment).order_by(Attachment.created_at.desc())
    if entity_type:
        query = query.where(Attachment.entity_type == entity_type.upper())
    if entity_id:
        query = query.where(Attachment.entity_id == entity_id)
    return [_attachment_dict(item) for item in db.scalars(query).all()]


@router.post("", status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    entity_type: str = Form(...),
    entity_id: int | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict:
    normalized_type = entity_type.strip().upper()
    model = ENTITY_MODELS.get(normalized_type)
    if not model:
        raise HTTPException(status_code=400, detail="不支持的凭证关联类型")
    # Payment/refund evidence is uploaded before the financial record exists.
    # Its NULL association is claimed atomically by the database insert trigger.
    if normalized_type in UNCLAIMED_PROOF_TYPES and entity_id in {None, 0}:
        entity_id = None
    elif entity_id is None:
        raise HTTPException(status_code=400, detail="凭证关联记录ID不能为空")
    elif not db.get(model, entity_id):
        raise HTTPException(status_code=404, detail="凭证关联记录不存在")
    suffix = Path(file.filename or "attachment").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(status_code=415, detail="凭证只支持图片、PDF、Excel或CSV")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="凭证文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="凭证文件不能超过25MB")

    digest = sha256_bytes(data)
    existing = db.scalar(
        select(Attachment).where(
            Attachment.entity_type == normalized_type,
            Attachment.entity_id == entity_id,
            Attachment.sha256 == digest,
        )
    )
    if existing:
        return {**_attachment_dict(existing), "duplicate": True}

    settings = get_settings()
    path, digest = store_bytes(
        data=data,
        original_name=file.filename or f"attachment{suffix}",
        directory=settings.data_root / "attachments" / normalized_type.lower(),
        prefix=f"{entity_id}_" if entity_id is not None else f"unclaimed_{uuid4().hex}_",
    )
    item = Attachment(
        entity_type=normalized_type,
        entity_id=entity_id,
        original_name=file.filename or path.name,
        stored_path=str(path),
        sha256=digest,
        mime_type=file.content_type or mimetypes.guess_type(path.name)[0],
        size_bytes=len(data),
    )
    db.add(item)
    db.flush()
    db.add(
        AuditEvent(
            action="ATTACHMENT_UPLOADED",
            entity_type=normalized_type,
            entity_id=entity_id,
            details_json={"attachment_id": item.id, "sha256": digest},
        )
    )
    db.commit()
    db.refresh(item)
    return {**_attachment_dict(item), "duplicate": False}


@router.get("/{attachment_id}/file")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)) -> FileResponse:
    item = db.get(Attachment, attachment_id)
    if not item:
        raise HTTPException(status_code=404, detail="凭证不存在")
    path = Path(item.stored_path)
    root = get_settings().data_root / "attachments"
    if not path.exists() or not is_within(path, root):
        raise HTTPException(status_code=404, detail="凭证原文件不存在")
    return FileResponse(path, media_type=item.mime_type, filename=item.original_name)
