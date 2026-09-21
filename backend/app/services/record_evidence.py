"""Validate and atomically claim pre-uploaded financial record evidence."""
from io import BytesIO
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_, select

from ..models import Attachment
from .evidence_integrity import _file_integrity_problem
from ..config import get_settings


def validate_image(data: bytes) -> str:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("unsupported image")
            mime_type = "image/png" if image.format == "PNG" else "image/jpeg"
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
        return mime_type
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "历史结余凭证必须是完整可读取的PNG或JPEG图片") from exc


def unclaimed_evidence(db, ids: list[int], entity_type: str) -> list[Attachment]:
    if not ids or len(ids) != len(set(ids)) or any(value <= 0 for value in ids):
        raise HTTPException(400, "凭证ID必须是非空且不重复的正整数")
    result = []
    for attachment_id in ids:
        item = db.get(Attachment, attachment_id)
        if item is None or item.entity_type != entity_type:
            raise HTTPException(400, "凭证不存在或类型不匹配")
        if item.entity_id is not None or item.superseded:
            raise HTTPException(409, "凭证已被使用，请重新上传本次凭证")
        problem = _file_integrity_problem(
            stored_path=item.stored_path, root=get_settings().data_root / "attachments",
            expected_sha256=item.sha256, expected_size_bytes=item.size_bytes,
        )
        if problem:
            raise HTTPException(400, f"凭证校验失败：{problem}")
        if entity_type == "SNAPSHOT":
            validate_image(Path(item.stored_path).read_bytes())
        result.append(item)
    return result


def transaction_attachments(db, transaction) -> list[Attachment]:
    return list(db.scalars(select(Attachment).where(or_(
        (Attachment.entity_type == "TRANSACTION") & (Attachment.entity_id == transaction.id),
        Attachment.id == transaction.attachment_id if transaction.attachment_id else False,
    )).order_by(Attachment.id)).all())


def claim_evidence(items: list[Attachment], entity_id: int) -> None:
    for item in items:
        item.entity_id = entity_id
