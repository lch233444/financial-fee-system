from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.types import Receive, Scope, Send

from ..config import get_settings
from ..models import Attachment, BalanceSnapshot, StatementImport, TransactionRecord
from .storage import detect_statement_format, is_within, sha256_file


class _VerifiedEvidenceResponse(FileResponse):
    def __init__(self, *, temporary: tempfile.TemporaryDirectory, **kwargs) -> None:
        self._temporary = temporary
        super().__init__(**kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # A server pathsend may read the path after returning to this response;
        # stream it here so cleanup always follows the last read, including errors.
        scope = {**scope, "extensions": {
            key: value for key, value in scope.get("extensions", {}).items()
            if key != "http.response.pathsend"
        }}
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._temporary.cleanup()


def evidence_file_response(
    *,
    stored_path: str,
    root: Path,
    expected_sha256: str,
    expected_size_bytes: int | None,
    filename: str,
    media_type: str | None,
    statement_preview: bool = False,
) -> FileResponse:
    """Verify and serve one bounded-memory snapshot, never reopen the source.

    Keep the snapshot below the data root's temporary directory, outside archived
    evidence. FileResponse retains download names and range/conditional-range
    behavior, while its owner cleans up after success, disconnect or rejection.
    """
    temporary = None
    path = Path(stored_path)
    try:
        if not is_within(path, root) or not path.exists():
            raise HTTPException(404, "凭证原文件不存在或不在系统受控目录")
        if not path.is_file():
            raise HTTPException(409, "凭证原文件不是普通文件")
        with path.open("rb") as source:
            source_stat = os.fstat(source.fileno())
            if not stat.S_ISREG(source_stat.st_mode):
                raise HTTPException(409, "凭证原文件不是普通文件")
            if source_stat.st_size <= 0:
                raise HTTPException(409, "凭证原文件为空")
            if expected_size_bytes is not None and source_stat.st_size != expected_size_bytes:
                raise HTTPException(409, "凭证文件大小与记录不一致")
            temporary = tempfile.TemporaryDirectory(
                prefix="evidence-read-", dir=get_settings().data_root / "tmp"
            )
            snapshot = Path(temporary.name) / "verified"
            digest = hashlib.sha256()
            size_bytes = 0
            signature = b""
            with snapshot.open("wb") as output:
                while chunk := source.read(64 * 1024):
                    if not signature:
                        signature = chunk[:8]
                    size_bytes += len(chunk)
                    if size_bytes > source_stat.st_size:
                        raise HTTPException(409, "凭证文件大小在读取期间发生变化")
                    digest.update(chunk)
                    output.write(chunk)
            if size_bytes != source_stat.st_size:
                raise HTTPException(409, "凭证文件大小在读取期间发生变化")
            if digest.hexdigest().casefold() != expected_sha256.casefold():
                raise HTTPException(409, "凭证文件SHA-256与记录不一致")
        if statement_preview:
            detected = detect_statement_format(signature)
            if detected is None:
                raise HTTPException(415, "原始文件格式无法安全预览")
            media_type = detected[1]
        return _VerifiedEvidenceResponse(
            temporary=temporary,
            path=snapshot,
            stat_result=source_stat,
            media_type=media_type,
            filename=filename,
            content_disposition_type="inline" if statement_preview else "attachment",
            headers={"X-Content-Type-Options": "nosniff"} if statement_preview else None,
        )
    except BaseException as exc:
        if temporary is not None:
            temporary.cleanup()
        if isinstance(exc, FileNotFoundError):
            raise HTTPException(404, "凭证原文件不存在") from exc
        if isinstance(exc, OSError):
            raise HTTPException(409, "凭证原文件无法安全读取") from exc
        raise


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
    if attachment.entity_type != entity_type or attachment.entity_id != entity_id or attachment.superseded:
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
            return "原始账单与历史结余的确认关系不一致"
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
            Attachment.superseded.is_(False),
        )
    ).all()
    if not attachments:
        return "缺少历史结余凭证"
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
            Attachment.superseded.is_(False),
        )
    ).all()
    attachments: list[Attachment | None] = []
    if transaction.attachment_id is not None:
        legacy = db.get(Attachment, transaction.attachment_id)
        if legacy is None or not legacy.superseded:
            attachments.append(legacy)
    known_ids = {attachment.id for attachment in attachments if attachment is not None}
    attachments.extend(
        attachment for attachment in generic_attachments if attachment.id not in known_ids
    )
    if not attachments:
        return "缺少资金记录凭证"
    problems = [
        _attachment_problem(attachment, entity_type="TRANSACTION", entity_id=transaction.id)
        for attachment in attachments
    ]
    if any(problem is None for problem in problems):
        return None
    return problems[0]
