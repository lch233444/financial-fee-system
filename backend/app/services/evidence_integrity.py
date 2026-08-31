from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Attachment, BalanceSnapshot, StatementImport, TransactionRecord
from .storage import is_within, sha256_file


def _file_integrity_problem(
    *,
    stored_path: str,
    root: Path,
    expected_sha256: str,
    expected_size_bytes: int | None,
) -> str | None:
    path = Path(stored_path)
    if not is_within(path, root):
        return "文件路径不在系统受控目录"
    try:
        if not path.is_file():
            return "文件不存在或不是普通文件"
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            return "文件为空"
        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            return "文件大小与记录不一致"
        digest = sha256_file(path)
    except OSError:
        return "文件无法读取"
    if digest.casefold() != expected_sha256.casefold():
        return "文件SHA-256与记录不一致"
    return None


def _attachment_problem(
    attachment: Attachment | None, *, entity_type: str, entity_id: int
) -> str | None:
    if attachment is None:
        return "凭证记录不存在"
    if attachment.entity_type != entity_type or attachment.entity_id != entity_id:
        return "凭证记录与财务记录不匹配"
    return _file_integrity_problem(
        stored_path=attachment.stored_path,
        root=get_settings().data_root / "attachments",
        expected_sha256=attachment.sha256,
        expected_size_bytes=attachment.size_bytes,
    )


def snapshot_evidence_problem(db: Session, snapshot: BalanceSnapshot) -> str | None:
    if snapshot.statement_import_id is not None:
        statement = db.get(StatementImport, snapshot.statement_import_id)
        if statement is None:
            return "原始账单记录不存在"
        if statement.status != "CONFIRMED":
            return "原始账单尚未完成人工确认"
        if (
            statement.confirmed_account_id != snapshot.account_id
            or statement.confirmed_snapshot_id != snapshot.id
        ):
            return "原始账单与余额快照的确认关系不一致"
        return _file_integrity_problem(
            stored_path=statement.stored_path,
            root=get_settings().data_root / "statement_imports",
            expected_sha256=statement.sha256,
            expected_size_bytes=None,
        )

    attachments = db.scalars(
        select(Attachment).where(
            Attachment.entity_type == "SNAPSHOT",
            Attachment.entity_id == snapshot.id,
        )
    ).all()
    if not attachments:
        return "缺少余额快照凭证"
    problems = [
        _attachment_problem(attachment, entity_type="SNAPSHOT", entity_id=snapshot.id)
        for attachment in attachments
    ]
    if any(problem is None for problem in problems):
        return None
    return problems[0]


def transaction_evidence_problem(db: Session, transaction: TransactionRecord) -> str | None:
    generic_attachments = db.scalars(
        select(Attachment).where(
            Attachment.entity_type == "TRANSACTION",
            Attachment.entity_id == transaction.id,
        )
    ).all()
    attachments: list[Attachment | None] = []
    if transaction.attachment_id is not None:
        attachments.append(db.get(Attachment, transaction.attachment_id))
    known_ids = {attachment.id for attachment in attachments if attachment is not None}
    attachments.extend(
        attachment for attachment in generic_attachments if attachment.id not in known_ids
    )
    if not attachments:
        return "缺少资金流水凭证"
    problems = [
        _attachment_problem(attachment, entity_type="TRANSACTION", entity_id=transaction.id)
        for attachment in attachments
    ]
    if any(problem is None for problem in problems):
        return None
    return problems[0]
