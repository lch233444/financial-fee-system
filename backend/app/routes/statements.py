from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import AuditEvent, BalanceSnapshot, Client, Platform, StatementImport, SubAccount
from ..money import money_string, to_cents
from ..schemas import StatementConfirmRequest, StatementHoldingInput
from ..services.calculation import is_quarter_end
from ..services.codex_app_server import (
    AI_PARSER_VERSION,
    FIXED_AI_MODEL,
    CodexIntegrationError,
    build_ai_review_result,
    get_codex_app_server,
)
from ..services.statement_parser import DOCUMENT_TYPE_LABELS, OCR_PARSER_VERSION, parse_empf_statement
from ..services.storage import detect_statement_format, is_within, store_bytes
from .ai_assistant import raise_ai_http_error, require_financial_system_request


router = APIRouter(prefix="/api/statement-imports", tags=["statement-imports"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_STATEMENT_RECOGNITION_LOCK = threading.Lock()


def _statement_dict(item: StatementImport) -> dict:
    return {
        "id": item.id,
        "original_name": item.original_name,
        "mime_type": item.mime_type,
        "parser_name": item.parser_name,
        "parser_version": item.parser_version,
        "status": item.status,
        "extracted": item.extracted_json or {},
        "reviewed": item.reviewed_json,
        "revision_log": item.revision_log_json or [],
        "confidence": item.confidence_json or {},
        "warnings": item.warnings_json or [],
        "ai_recognition": item.ai_recognition_json,
        "ai_status": item.ai_status,
        "ai_model": item.ai_model,
        "ai_recognized_at": item.ai_recognized_at.isoformat() if item.ai_recognized_at else None,
        "duplicate_of_id": item.duplicate_of_id,
        "confirmed_account_id": item.confirmed_account_id,
        "confirmed_snapshot_id": item.confirmed_snapshot_id,
        "created_at": item.created_at.isoformat(),
    }


def _require_statement(db: Session, import_id: int) -> StatementImport:
    item = db.get(StatementImport, import_id)
    if item is None:
        raise HTTPException(status_code=404, detail="导入记录不存在")
    return item


@router.get("")
def list_statement_imports(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(StatementImport).order_by(StatementImport.created_at.desc())).all()
    return [_statement_dict(item) for item in items]


@router.post("", status_code=201)
async def upload_statement(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件不能超过25MB")
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    detected_format = detect_statement_format(data)
    if not detected_format:
        raise HTTPException(
            status_code=415,
            detail="文件内容不是有效的JPG、PNG或PDF；已拒绝伪装或未知格式文件",
        )
    detected_suffix, detected_mime = detected_format

    settings = get_settings()
    stored_path, digest = store_bytes(
        data=data,
        # The storage suffix and parser path are derived from magic bytes.
        original_name=f"statement{detected_suffix}",
        directory=settings.data_root / "statement_imports",
    )
    duplicate = db.scalar(select(StatementImport).where(StatementImport.sha256 == digest))
    if duplicate:
        return {**_statement_dict(duplicate), "duplicate": True}

    parsed = parse_empf_statement(stored_path)
    semantic_duplicate = None
    semantic_key = (
        parsed.account_number,
        parsed.as_of_date.isoformat() if parsed.as_of_date else None,
        parsed.total_balance,
    )
    if all(semantic_key):
        for previous in db.scalars(select(StatementImport).order_by(StatementImport.id.desc())).all():
            values = previous.reviewed_json or previous.extracted_json or {}
            previous_key = (
                values.get("account_number"),
                values.get("as_of_date"),
                values.get("total_balance"),
            )
            if tuple(str(value) for value in previous_key) == tuple(str(value) for value in semantic_key):
                semantic_duplicate = previous
                parsed.warnings.insert(0, f"疑似重复账单：关键字段与导入记录#{previous.id}相同，请核对")
                break
    item = StatementImport(
        original_name=file.filename or stored_path.name,
        stored_path=str(stored_path),
        sha256=digest,
        mime_type=detected_mime,
        parser_name=f"LOCAL_OCR_{parsed.document_type.upper()}",
        status="NEEDS_REVIEW",
        parser_version=OCR_PARSER_VERSION,
        raw_text=parsed.raw_text,
        extracted_json=parsed.extracted_dict(),
        confidence_json=parsed.confidence,
        warnings_json=parsed.warnings,
        duplicate_of_id=semantic_duplicate.id if semantic_duplicate else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {**_statement_dict(item), "duplicate": False}


@router.get("/{import_id}")
def get_statement_import(import_id: int, db: Session = Depends(get_db)) -> dict:
    return _statement_dict(_require_statement(db, import_id))


@router.delete("/{import_id}")
def delete_statement_import(import_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete an unconfirmed import and its private source file.

    The shared recognition lock prevents a delete from racing OCR, Luna, or
    financial confirmation. Confirmed or snapshot-linked evidence is immutable.
    """

    with _STATEMENT_RECOGNITION_LOCK:
        item = _require_statement(db, import_id)
        linked_snapshot_id = db.scalar(
            select(BalanceSnapshot.id).where(BalanceSnapshot.statement_import_id == item.id).limit(1)
        )
        if item.status == "CONFIRMED" or linked_snapshot_id is not None:
            raise HTTPException(status_code=409, detail="已确认入账或已关联余额快照的导入记录不能删除")

        settings = get_settings()
        source_path = Path(item.stored_path)
        statement_root = settings.data_root / "statement_imports"
        if not is_within(source_path, statement_root):
            raise HTTPException(status_code=409, detail="导入原件路径不安全，已停止删除")
        if source_path.exists() and not source_path.is_file():
            raise HTTPException(status_code=409, detail="导入原件路径不是文件，已停止删除")

        source_file_deleted = source_path.exists()
        if source_file_deleted:
            source_path.unlink()

        db.add(
            AuditEvent(
                action="STATEMENT_IMPORT_DELETED",
                entity_type="STATEMENT_IMPORT",
                entity_id=item.id,
                details_json={
                    "sha256": item.sha256,
                    "previous_status": item.status,
                    "had_ai_recognition": item.ai_recognition_json is not None,
                    "source_file_found": source_file_deleted,
                },
            )
        )
        db.execute(
            update(StatementImport)
            .where(StatementImport.duplicate_of_id == item.id)
            .values(duplicate_of_id=None)
        )
        db.delete(item)
        db.commit()
        return {"deleted": True, "import_id": import_id, "source_file_deleted": source_file_deleted}


@router.get("/{import_id}/file")
def get_statement_file(import_id: int, db: Session = Depends(get_db)) -> FileResponse:
    item = _require_statement(db, import_id)
    path = Path(item.stored_path)
    settings = get_settings()
    if not path.exists() or not is_within(path, settings.data_root / "statement_imports"):
        raise HTTPException(status_code=404, detail="原始文件不存在")
    try:
        detected_format = detect_statement_format(path.read_bytes()[:8])
    except OSError as exc:
        raise HTTPException(status_code=404, detail="原始文件无法读取") from exc
    if not detected_format:
        raise HTTPException(status_code=415, detail="原始文件格式无法安全预览")
    _detected_suffix, detected_mime = detected_format
    response = FileResponse(
        path,
        media_type=detected_mime,
        filename=item.original_name,
        content_disposition_type="inline",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/{import_id}/reparse")
def reparse_statement(import_id: int, db: Session = Depends(get_db)) -> dict:
    with _STATEMENT_RECOGNITION_LOCK:
        item = _require_statement(db, import_id)
        if item.status == "CONFIRMED":
            raise HTTPException(status_code=409, detail="已确认入账的文件不能重新识别")
        if item.ai_recognition_json is not None:
            raise HTTPException(
                status_code=409,
                detail="Luna已基于当前OCR结果完成比对；为保留审计基线，不能重新执行OCR",
            )
        parsed = parse_empf_statement(Path(item.stored_path))
        item.parser_name = f"LOCAL_OCR_{parsed.document_type.upper()}"
        item.parser_version = OCR_PARSER_VERSION
        item.raw_text = parsed.raw_text
        item.extracted_json = parsed.extracted_dict()
        item.confidence_json = parsed.confidence
        item.warnings_json = parsed.warnings
        db.commit()
        return _statement_dict(item)


@router.post("/{import_id}/ai-recognize")
def recognize_statement_with_ai(
    import_id: int,
    db: Session = Depends(get_db),
    _request_guard: None = Depends(require_financial_system_request),
) -> dict:
    """Create a separate Luna review candidate for an unconfirmed import.

    This route deliberately does not update OCR extraction, reviewed values,
    account data, balance snapshots, transactions, or settlements.
    """

    # The app-server is a single local channel. Holding this route-level lock
    # also makes the idempotency check atomic, so concurrent clicks cannot
    # consume the user's subscription twice for the same statement.
    with _STATEMENT_RECOGNITION_LOCK:
        item = _require_statement(db, import_id)
        if item.status == "CONFIRMED":
            raise HTTPException(status_code=409, detail="已确认入账的文件不能再进行AI识别")
        if item.ai_recognition_json is not None:
            return {**_statement_dict(item), "idempotent": True}
        path = Path(item.stored_path)
        statement_root = get_settings().data_root / "statement_imports"
        if not path.exists() or not is_within(path, statement_root):
            raise HTTPException(status_code=404, detail="原始文件不存在或路径不安全")
        ocr_values = dict(item.extracted_json or {})

        try:
            ai_values = get_codex_app_server().recognize_statement(path)
        except CodexIntegrationError as exc:
            raise_ai_http_error(exc)

        review = build_ai_review_result(ocr_values, ai_values)
        recognized_at = datetime.now(timezone.utc)
        item.ai_recognition_json = review
        item.ai_status = review["status"]
        item.ai_model = FIXED_AI_MODEL
        item.ai_recognized_at = recognized_at
        db.add(
            AuditEvent(
                action="STATEMENT_AI_RECOGNIZED",
                entity_type="STATEMENT_IMPORT",
                entity_id=item.id,
                details_json={
                    "model": FIXED_AI_MODEL,
                    "parser_version": AI_PARSER_VERSION,
                    "status": review["status"],
                    "conflict_fields": [value["field"] for value in review["conflicts"]],
                    "model_escalation": "DISABLED",
                    "financial_data_mutated": False,
                },
            )
        )
        db.commit()
        db.refresh(item)
        return _statement_dict(item)


def _platform_for_statement(db: Session, scheme_name: str | None, trustee: str | None) -> Platform:
    target_name = scheme_name or trustee or "eMPF 待确认平台"
    existing = db.scalar(select(Platform).where(Platform.name == target_name))
    if existing:
        return existing
    base_code = "EMPF"
    code = base_code
    suffix = 1
    while db.scalar(select(Platform).where(Platform.code == code)):
        suffix += 1
        code = f"{base_code}-{suffix}"
    item = Platform(name=target_name, code=code, trustee=trustee, remark="由eMPF账单导入自动创建，待财务确认")
    db.add(item)
    db.flush()
    return item


def _normalized_holdings(value: object) -> list[dict]:
    """Canonicalize every source through the same strict finance schema."""

    rows = value if isinstance(value, list) else []
    try:
        return [
            StatementHoldingInput.model_validate(row).model_dump(mode="json")
            for row in rows
        ]
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="已识别的持仓数据格式异常，财务必须重新核对并提交持仓明细",
        ) from exc


@router.post("/{import_id}/confirm")
def confirm_statement(
    import_id: int,
    payload: StatementConfirmRequest,
    db: Session = Depends(get_db),
) -> dict:
    # Confirmation, OCR reparse, and Luna recognition all share one critical
    # section. A snapshot can therefore never be posted from a baseline that
    # changes while an independent recognition is still in flight.
    with _STATEMENT_RECOGNITION_LOCK:
        return _confirm_statement_locked(import_id, payload, db)


def _confirm_statement_locked(
    import_id: int,
    payload: StatementConfirmRequest,
    db: Session,
) -> dict:
    item = _require_statement(db, import_id)
    if item.status == "CONFIRMED":
        raise HTTPException(status_code=409, detail="该文件已经确认入账")

    extracted = item.extracted_json or {}
    document_type = extracted.get("document_type")
    ai_review = item.ai_recognition_json or {}
    ai_values = ai_review.get("values") or ai_review.get("extracted") or {}
    luna_document_type = ai_values.get("document_type") if isinstance(ai_values, dict) else None
    luna_balance_page_override = (
        document_type == "unknown"
        and luna_document_type == "empf_account_page"
        and item.ai_status in {"AGREED", "CONFLICT", "INCOMPLETE"}
    )
    if document_type != "empf_account_page" and not luna_balance_page_override:
        label = DOCUMENT_TYPE_LABELS.get(str(document_type), DOCUMENT_TYPE_LABELS["unknown"])
        raise HTTPException(
            status_code=409,
            detail=f"该文件被识别为“{label}”，不是账户余额页面，禁止生成余额快照",
        )
    if luna_balance_page_override and payload.luna_document_type_reviewed is not True:
        raise HTTPException(
            status_code=409,
            detail="本地OCR未能确认文档类型；财务必须查看原件并勾选已确认采用Luna余额页分类",
        )

    ai_requires_acknowledgement = bool(
        item.ai_recognition_json
        and (
            item.ai_status != "AGREED"
            or ai_review.get("conflicts")
            or ai_review.get("uncorroborated")
            or ai_review.get("validation_failures")
            or ai_review.get("uncertain_critical_fields")
        )
    )
    if ai_requires_acknowledgement and payload.ai_conflicts_reviewed is not True:
        raise HTTPException(
            status_code=409,
            detail="Luna识别存在冲突、不确定或校验异常；财务必须勾选已逐项人工核对后才能入账",
        )

    account: SubAccount | None = None
    if payload.account_id:
        account = db.get(SubAccount, payload.account_id)
        if not account:
            raise HTTPException(status_code=404, detail="指定的Sub Account不存在")
        if account.account_number != payload.account_number:
            raise HTTPException(status_code=400, detail="Account Number与指定账户不一致")
    else:
        candidates = db.scalars(
            select(SubAccount).where(SubAccount.account_number == payload.account_number)
        ).all()
        if len(candidates) == 1:
            account = candidates[0]
        elif len(candidates) > 1:
            scheme_matches = [
                candidate
                for candidate in candidates
                if payload.scheme_name
                and candidate.scheme_name
                and candidate.scheme_name.strip().casefold() == payload.scheme_name.strip().casefold()
            ]
            if len(scheme_matches) == 1:
                account = scheme_matches[0]
            else:
                raise HTTPException(
                    status_code=409,
                    detail="多个Platform存在相同Account Number，请在复核页明确选择Sub Account",
                )

    created_draft = False
    if not account:
        platform = _platform_for_statement(db, payload.scheme_name, payload.trustee)
        client = Client(name=payload.client_name, status="DRAFT", remark="由eMPF账单导入创建，待补全Company和FC")
        db.add(client)
        db.flush()
        account = SubAccount(
            client_id=client.id,
            platform_id=platform.id,
            fee_plan_id=None,
            account_number=payload.account_number,
            scheme_name=payload.scheme_name,
            currency="HKD",
            status="DRAFT",
            remark="由eMPF账单导入创建，待补全Fee Plan",
        )
        db.add(account)
        db.flush()
        created_draft = True
    elif account.client.name.strip().casefold() != payload.client_name.strip().casefold():
        raise HTTPException(status_code=409, detail="Account Number已存在，但Client Name不一致")
    elif (
        payload.scheme_name
        and account.scheme_name
        and account.scheme_name.strip().casefold() != payload.scheme_name.strip().casefold()
    ):
        raise HTTPException(status_code=409, detail="账单Scheme与选定Sub Account不一致")

    recognized_holdings = _normalized_holdings(extracted.get("holdings"))
    ai_holdings = _normalized_holdings(
        ai_values.get("holdings") if isinstance(ai_values, dict) else None
    )
    submitted_holdings = (
        _normalized_holdings([holding.model_dump(mode="json") for holding in payload.holdings])
        if payload.holdings is not None
        else None
    )
    confirmed_holdings = submitted_holdings if submitted_holdings is not None else recognized_holdings
    if submitted_holdings is None:
        holdings_source = "LOCAL_OCR_DEFAULT"
    elif submitted_holdings == recognized_holdings:
        holdings_source = "LOCAL_OCR_SELECTED"
    elif item.ai_recognition_json is not None and submitted_holdings == ai_holdings:
        holdings_source = "LUNA_SELECTED"
    else:
        holdings_source = "FINANCE_EDITED"

    eligible = is_quarter_end(payload.as_of_date) or (account.end_date == payload.as_of_date)
    snapshot = BalanceSnapshot(
        account_id=account.id,
        as_of_date=payload.as_of_date,
        total_balance_cents=to_cents(payload.total_balance),
        currency="HKD",
        source_type="STATEMENT_IMPORT",
        statement_import_id=item.id,
        holdings_json=confirmed_holdings,
        eligible_for_closing=eligible,
        remark="季末/退出日Closing候选" if eligible else "非季末余额快照，不可直接作为Closing",
    )
    db.add(snapshot)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该账户在同一天已经有余额快照") from exc

    reviewed_values = {
        **extracted,
        **payload.model_dump(
            mode="json",
            exclude={
                "account_id",
                "ai_conflicts_reviewed",
                "luna_document_type_reviewed",
                "holdings",
            },
        ),
        "document_type": "empf_account_page",
        "holdings": confirmed_holdings,
    }
    changes = {
        key: {"recognized": extracted.get(key), "confirmed": value}
        for key, value in reviewed_values.items()
        if extracted.get(key) != value and key != "holdings"
    }
    changes["holdings"] = {
        "source": holdings_source,
        "recognized_count": len(recognized_holdings),
        "luna_count": len(ai_holdings),
        "confirmed_count": len(confirmed_holdings),
        "changed": recognized_holdings != confirmed_holdings,
    }
    item.reviewed_json = reviewed_values
    confirmed_at = datetime.now(timezone.utc)
    item.revision_log_json = [
        *((item.revision_log_json or [])),
        {
            "reviewed_at": confirmed_at.isoformat(),
            "changes": changes,
            "ai_review_acknowledged": bool(payload.ai_conflicts_reviewed),
            "luna_document_type_reviewed": bool(payload.luna_document_type_reviewed),
        },
    ]
    item.status = "CONFIRMED"
    item.confirmed_account_id = account.id
    item.confirmed_snapshot_id = snapshot.id
    item.confirmed_at = confirmed_at
    db.add(
        AuditEvent(
            action="STATEMENT_CONFIRMED",
            entity_type="STATEMENT_IMPORT",
            entity_id=item.id,
            details_json={
                "account_id": account.id,
                "snapshot_id": snapshot.id,
                "changes": changes,
                "ai_status": item.ai_status,
                "ai_review_acknowledged": bool(payload.ai_conflicts_reviewed),
                "document_type_source": (
                    "LUNA_HUMAN_CONFIRMED" if luna_balance_page_override else "LOCAL_OCR"
                ),
            },
        )
    )
    db.commit()
    db.refresh(snapshot)
    return {
        "statement_import": _statement_dict(item),
        "account_id": account.id,
        "created_draft": created_draft,
        "snapshot": {
            "id": snapshot.id,
            "as_of_date": snapshot.as_of_date.isoformat(),
            "total_balance": money_string(snapshot.total_balance_cents),
            "eligible_for_closing": snapshot.eligible_for_closing,
        },
    }
