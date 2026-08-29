from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..database import get_db
from ..models import (
    AuditEvent,
    Client,
    ExportRecord,
    Invoice,
    InvoiceIssueAttempt,
    InvoiceLine,
    InvoiceSequence,
    InvoiceSource,
    Payment,
    QuarterlySettlement,
    SettlementAccountLine,
)
from ..money import to_cents
from ..schemas import (
    InvoiceDraftCreate,
    InvoiceIssueRecoveryRequest,
    InvoiceIssueRequest,
    PaymentCreate,
    VoidRequest,
)
from ..serializers import invoice_dict
from ..services.excel_export import file_sha256
from ..services.invoice_archive import (
    invoice_archive_paths,
    invoice_download_filename,
    invoice_recovery_path_sets,
)
from ..services.pdf_invoice import generate_invoice_pdf
from ..services.storage import is_within


router = APIRouter(prefix="/api/invoices", tags=["invoices"])
_invoice_issue_lock = Lock()
_active_issue_ids: set[int] = set()
ACTIVE_INVOICE_STATUSES = ("DRAFT", "ISSUING", "ISSUED")


def _invoice_query():
    return select(Invoice).options(
        selectinload(Invoice.company),
        selectinload(Invoice.fc),
        selectinload(Invoice.client),
        selectinload(Invoice.fee_plan),
        selectinload(Invoice.payments),
        selectinload(Invoice.sources).selectinload(InvoiceSource.settlement),
        selectinload(Invoice.lines)
        .selectinload(InvoiceLine.source_account_line)
        .selectinload(SettlementAccountLine.account),
        selectinload(Invoice.issue_attempts),
        selectinload(Invoice.settlement)
        .selectinload(QuarterlySettlement.account_lines)
        .selectinload(SettlementAccountLine.account),
    )


def _draft_settlement_query():
    return select(QuarterlySettlement).options(
        selectinload(QuarterlySettlement.company),
        selectinload(QuarterlySettlement.fc),
        selectinload(QuarterlySettlement.client),
        selectinload(QuarterlySettlement.platform),
        selectinload(QuarterlySettlement.fee_plan),
        selectinload(QuarterlySettlement.account_lines).selectinload(SettlementAccountLine.account),
    )


def _reload_invoice(db: Session, invoice_id: int) -> Invoice:
    item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
    if not item:
        raise HTTPException(status_code=404, detail="Invoice不存在")
    return item


def _flush_invoice_draft(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该Invoice分组或Settlement已被并发请求占用") from exc


@router.get("")
def list_invoices(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(_invoice_query().order_by(Invoice.created_at.desc())).unique().all()
    return [invoice_dict(item) for item in items]


@router.post("", status_code=201)
def create_invoice_draft(payload: InvoiceDraftCreate, db: Session = Depends(get_db)) -> dict:
    client = db.get(Client, payload.client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client不存在")
    if not client.management_start_date:
        raise HTTPException(status_code=400, detail="Client必须补全Management Start Date")

    existing_group = db.scalar(
        select(Invoice.id).where(
            Invoice.client_id == payload.client_id,
            Invoice.year == payload.year,
            Invoice.quarter == payload.quarter,
            Invoice.fee_plan_id == payload.fee_plan_id,
            Invoice.lifecycle_status.in_(ACTIVE_INVOICE_STATUSES),
        )
    )
    if existing_group is not None:
        raise HTTPException(status_code=409, detail="该客户、季度和Fee Plan已有有效Invoice")

    settlements = db.scalars(
        _draft_settlement_query()
        .where(
            QuarterlySettlement.client_id == payload.client_id,
            QuarterlySettlement.year == payload.year,
            QuarterlySettlement.quarter == payload.quarter,
            QuarterlySettlement.fee_plan_id == payload.fee_plan_id,
            QuarterlySettlement.status == "FINALIZED",
        )
        .order_by(QuarterlySettlement.platform_id, QuarterlySettlement.id)
    ).unique().all()
    if not settlements:
        raise HTTPException(status_code=404, detail="该客户、季度和Fee Plan没有Finalized Settlement")

    company_ids = {settlement.company_id for settlement in settlements}
    fc_ids = {settlement.fc_id for settlement in settlements}
    if None in company_ids or None in fc_ids:
        raise HTTPException(status_code=400, detail="Finalized Settlement缺少冻结Company或FC")
    if len(company_ids) != 1 or len(fc_ids) != 1:
        raise HTTPException(status_code=409, detail="跨Platform Settlement的冻结Company或FC不一致，不能合并Invoice")
    if any(
        not settlement.fee_plan
        or settlement.fee_plan.company_id != settlement.company_id
        or not settlement.fc
        or settlement.fc.company_id != settlement.company_id
        for settlement in settlements
    ):
        raise HTTPException(status_code=409, detail="Finalized Settlement的Company、FC与Fee Plan归属不一致")
    if any(
        settlement.client_id != payload.client_id
        or settlement.year != payload.year
        or settlement.quarter != payload.quarter
        or settlement.fee_plan_id != payload.fee_plan_id
        for settlement in settlements
    ):
        raise HTTPException(status_code=409, detail="Settlement分组与Invoice请求不一致")

    settlement_ids = [settlement.id for settlement in settlements]
    occupied_source = db.scalar(
        select(InvoiceSource.settlement_id).where(
            InvoiceSource.settlement_id.in_(settlement_ids),
            InvoiceSource.active.is_(True),
        )
    )
    if occupied_source is not None:
        raise HTTPException(status_code=409, detail=f"Settlement #{occupied_source} 已被有效Invoice占用")

    anchor = settlements[0]
    item = Invoice(
        settlement_id=anchor.id,
        client_id=payload.client_id,
        year=payload.year,
        quarter=payload.quarter,
        fee_plan_id=payload.fee_plan_id,
        company_id=anchor.company_id,
        fc_id=anchor.fc_id,
        amount_cents=0,
        language=payload.language,
        lifecycle_status="DRAFT",
    )
    db.add(item)
    _flush_invoice_draft(db)

    display_order = 0
    invoice_total_cents = 0
    for settlement in settlements:
        if not settlement.platform or not settlement.fee_plan:
            raise HTTPException(status_code=409, detail=f"Settlement #{settlement.id} 缺少冻结出单资料")
        source = InvoiceSource(
            invoice_id=item.id,
            settlement_id=settlement.id,
            locked_amount_cents=0,
            active=True,
        )
        db.add(source)
        _flush_invoice_draft(db)

        source_total_cents = 0
        if settlement.calculation_mode == "ACCOUNT_HWM":
            if not settlement.account_lines:
                raise HTTPException(status_code=409, detail=f"Settlement #{settlement.id} 缺少账户级锁定明细")
            account_lines = sorted(
                settlement.account_lines,
                key=lambda line: (
                    line.account.account_number.casefold() if line.account else "",
                    line.id,
                ),
            )
            for settlement_line in account_lines:
                if not settlement_line.account or settlement_line.service_fee_cents is None:
                    raise HTTPException(status_code=409, detail=f"Settlement #{settlement.id} 账户锁定明细不完整")
                line_fee_cents = int(settlement_line.service_fee_cents)
                db.add(
                    InvoiceLine(
                        invoice_id=item.id,
                        source_id=source.id,
                        source_settlement_id=settlement.id,
                        source_account_line_id=settlement_line.id,
                        platform_id=settlement.platform_id,
                        platform_name_snapshot=settlement.platform.name,
                        account_number_snapshot=settlement_line.account.account_number,
                        start_date=settlement_line.start_date,
                        closing_date=settlement_line.closing_date,
                        service_fee_cents=line_fee_cents,
                        display_order=display_order,
                    )
                )
                display_order += 1
                source_total_cents += line_fee_cents
        else:
            source_total_cents = int(settlement.service_fee_cents)
            db.add(
                InvoiceLine(
                    invoice_id=item.id,
                    source_id=source.id,
                    source_settlement_id=settlement.id,
                    source_account_line_id=None,
                    platform_id=settlement.platform_id,
                    platform_name_snapshot=settlement.platform.name,
                    account_number_snapshot="LEGACY GROUP SUMMARY",
                    start_date=settlement.start_date,
                    closing_date=settlement.closing_date,
                    service_fee_cents=source_total_cents,
                    display_order=display_order,
                )
            )
            display_order += 1

        if source_total_cents != settlement.service_fee_cents:
            raise HTTPException(
                status_code=409,
                detail=f"Settlement #{settlement.id} 的账户Service Fee合计与锁定总额不一致",
            )
        source.locked_amount_cents = source_total_cents
        invoice_total_cents += source_total_cents

    if invoice_total_cents <= 0:
        raise HTTPException(status_code=400, detail="该组Service Fee合计为0，只生成结算单，不生成Invoice")
    item.amount_cents = invoice_total_cents
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该Invoice分组或Settlement已被并发请求占用") from exc
    db.expire_all()
    return invoice_dict(_reload_invoice(db, item.id))


def _next_number(db: Session, invoice: Invoice, issue_date: date) -> str:
    sequence = db.scalar(
        select(InvoiceSequence).where(
            InvoiceSequence.company_id == invoice.company_id,
            InvoiceSequence.fc_id == invoice.fc_id,
        )
    )
    if not sequence:
        sequence = InvoiceSequence(company_id=invoice.company_id, fc_id=invoice.fc_id, last_number=0)
        db.add(sequence)
        db.flush()
    yyyymmdd = issue_date.strftime("%Y%m%d")
    company_name = invoice.company.name.strip()
    if not company_name:
        raise HTTPException(status_code=409, detail="Company全名为空，不能生成Invoice编号")
    while True:
        sequence.last_number += 1
        candidate = f"{company_name}-{invoice.fc.code}-{yyyymmdd}-{sequence.last_number}"
        used_by_invoice = db.scalar(
            select(Invoice.id).where(Invoice.invoice_number == candidate).limit(1)
        )
        used_by_attempt = db.scalar(
            select(InvoiceIssueAttempt.id)
            .where(InvoiceIssueAttempt.invoice_number == candidate)
            .limit(1)
        )
        if used_by_invoice is None and used_by_attempt is None:
            return candidate
        db.add(
            AuditEvent(
                action="INVOICE_NUMBER_COLLISION_SKIPPED",
                entity_type="INVOICE",
                entity_id=invoice.id,
                details_json={"invoice_number": candidate, "sequence": sequence.last_number},
            )
        )


def _validate_issue_sources(db: Session, invoice: Invoice) -> None:
    if not invoice.sources or not invoice.lines:
        raise HTTPException(status_code=409, detail="Invoice缺少冻结Settlement来源或账户明细")
    lines_by_source: dict[int, int] = {}
    for line in invoice.lines:
        lines_by_source[line.source_id] = lines_by_source.get(line.source_id, 0) + line.service_fee_cents
    if sum(line.service_fee_cents for line in invoice.lines) != invoice.amount_cents:
        raise HTTPException(status_code=409, detail="Invoice冻结账户明细与总额不一致")
    current_settlement_ids = set(
        db.scalars(
            select(QuarterlySettlement.id).where(
                QuarterlySettlement.client_id == invoice.client_id,
                QuarterlySettlement.year == invoice.year,
                QuarterlySettlement.quarter == invoice.quarter,
                QuarterlySettlement.fee_plan_id == invoice.fee_plan_id,
                QuarterlySettlement.status == "FINALIZED",
            )
        ).all()
    )
    frozen_settlement_ids = {source.settlement_id for source in invoice.sources if source.active}
    if current_settlement_ids != frozen_settlement_ids:
        raise HTTPException(
            status_code=409,
            detail="Draft建立后同组Finalized Settlement集合已变化，请先作废并重建Invoice",
        )
    for source in invoice.sources:
        settlement = source.settlement
        if not source.active:
            raise HTTPException(status_code=409, detail=f"Settlement #{source.settlement_id} 来源已失效")
        if not settlement or settlement.status != "FINALIZED":
            raise HTTPException(status_code=409, detail=f"Settlement #{source.settlement_id} 不是Finalized状态")
        if (
            settlement.client_id != invoice.client_id
            or settlement.year != invoice.year
            or settlement.quarter != invoice.quarter
            or settlement.fee_plan_id != invoice.fee_plan_id
            or settlement.company_id != invoice.company_id
            or settlement.fc_id != invoice.fc_id
        ):
            raise HTTPException(status_code=409, detail=f"Settlement #{source.settlement_id} 冻结分组已不一致")
        if (
            not settlement.fee_plan
            or settlement.fee_plan.company_id != settlement.company_id
            or not settlement.fc
            or settlement.fc.company_id != settlement.company_id
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Settlement #{source.settlement_id} 的Company、FC与Fee Plan归属不一致",
            )
        if lines_by_source.get(source.id, 0) != source.locked_amount_cents:
            raise HTTPException(status_code=409, detail=f"Settlement #{source.settlement_id} 冻结明细金额不一致")


def _invoice_final_paths(invoice_number: str) -> dict[str, Path]:
    pdf_root = get_settings().data_root / "output" / "pdf"
    return invoice_archive_paths(invoice_number, pdf_root)


def _reserved_attempt(invoice: Invoice) -> InvoiceIssueAttempt | None:
    attempts = sorted(invoice.issue_attempts, key=lambda attempt: attempt.id, reverse=True)
    return next(
        (
            attempt
            for attempt in attempts
            if attempt.invoice_number == invoice.invoice_number and attempt.status == "RESERVED"
        ),
        None,
    )


def _ensure_recovery_attempt(db: Session, invoice: Invoice) -> InvoiceIssueAttempt:
    attempt = _reserved_attempt(invoice)
    if attempt:
        return attempt
    if not invoice.invoice_number:
        raise HTTPException(status_code=409, detail="ISSUING Invoice缺少已预留编号，只能退回Draft")
    attempt = InvoiceIssueAttempt(
        invoice_id=invoice.id,
        invoice_number=invoice.invoice_number,
        status="RESERVED",
        details="Recovered legacy ISSUING state without an issue-attempt row",
    )
    db.add(attempt)
    db.flush()
    return attempt


@router.post("/{invoice_id}/issue")
def issue_invoice(
    invoice_id: int,
    payload: InvoiceIssueRequest,
    db: Session = Depends(get_db),
) -> dict:
    # Reserve the sequence and persist the recovery record in a short serialized
    # transaction. PDF rendering never holds the SQLite writer lock.
    with _invoice_issue_lock:
        item = _reload_invoice(db, invoice_id)
        if item.lifecycle_status != "DRAFT":
            raise HTTPException(status_code=409, detail="只有Draft Invoice可以Issued")
        _validate_issue_sources(db, item)
        issue_date = payload.issue_date or date.today()
        due_date = payload.due_date or (issue_date + timedelta(days=item.company.payment_terms_days))
        if due_date < issue_date:
            raise HTTPException(status_code=400, detail="Payment Due Date不能早于Issue Date")
        item.invoice_number = _next_number(db, item, issue_date)
        item.issue_date = issue_date
        item.due_date = due_date
        item.language = payload.language
        item.lifecycle_status = "ISSUING"
        attempt = InvoiceIssueAttempt(
            invoice_id=item.id,
            invoice_number=item.invoice_number,
            status="RESERVED",
            details="Invoice number reserved; bilingual PDF rendering started",
        )
        db.add(attempt)
        try:
            db.flush()
            db.add(
                AuditEvent(
                    action="INVOICE_ISSUE_RESERVED",
                    entity_type="INVOICE",
                    entity_id=item.id,
                    details_json={"invoice_number": item.invoice_number, "attempt_id": attempt.id},
                )
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Invoice来源、金额或状态已被并发请求改变，请刷新后重试",
            ) from exc
        attempt_id = attempt.id
        _active_issue_ids.add(item.id)

    pdf_paths: dict[str, str] = {}
    temp_paths: dict[str, Path] = {}
    final_paths = _invoice_final_paths(item.invoice_number)
    promoted_paths: list[Path] = []
    hashes: dict[str, str] = {}
    pdf_root = get_settings().data_root / "output" / "pdf"
    try:
        for language, output_path in final_paths.items():
            if not is_within(output_path, pdf_root) or output_path.exists():
                raise RuntimeError(f"Invoice归档目标已存在或路径无效: {output_path.name}")
            temp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
            temp_paths[language] = temp_path
            generate_invoice_pdf(invoice=item, language=language, output_path=temp_path)
            hashes[language] = file_sha256(temp_path)
        for language, temp_path in temp_paths.items():
            output_path = final_paths[language]
            temp_path.replace(output_path)
            promoted_paths.append(output_path)
            pdf_paths[language] = str(output_path)
    except Exception as exc:
        db.rollback()
        with _invoice_issue_lock:
            try:
                for path in [*temp_paths.values(), *promoted_paths]:
                    path.unlink(missing_ok=True)
                failed = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
                failed_attempt = db.get(InvoiceIssueAttempt, attempt_id)
                if failed_attempt:
                    failed_attempt.status = "FAILED"
                    failed_attempt.completed_at = datetime.now(timezone.utc)
                    failed_attempt.details = f"PDF rendering failed: {type(exc).__name__}: {exc}"
                if failed and failed.lifecycle_status == "ISSUING":
                    failed.lifecycle_status = "DRAFT"
                    failed.invoice_number = None
                    failed.issue_date = None
                    failed.due_date = None
                    failed.pdf_paths_json = None
                    db.add(
                        AuditEvent(
                            action="INVOICE_ISSUE_FAILED",
                            entity_type="INVOICE",
                            entity_id=invoice_id,
                            details_json={"attempt_id": attempt_id},
                        )
                    )
                db.commit()
            finally:
                _active_issue_ids.discard(invoice_id)
        raise HTTPException(
            status_code=500,
            detail="Invoice PDF生成失败，Invoice已恢复为Draft，已消耗编号不会复用",
        ) from exc

    try:
        with _invoice_issue_lock:
            item = _reload_invoice(db, invoice_id)
            if item.lifecycle_status != "ISSUING":
                for path in promoted_paths:
                    path.unlink(missing_ok=True)
                raise HTTPException(status_code=409, detail="Invoice签发状态已变化，请刷新后重试")
            try:
                _validate_issue_sources(db, item)
            except HTTPException:
                for path in promoted_paths:
                    path.unlink(missing_ok=True)
                failed_attempt = db.get(InvoiceIssueAttempt, attempt_id)
                if failed_attempt:
                    failed_attempt.status = "FAILED"
                    failed_attempt.completed_at = datetime.now(timezone.utc)
                    failed_attempt.details = "Finalized Settlement source set changed while PDFs were rendering"
                item.lifecycle_status = "DRAFT"
                item.invoice_number = None
                item.issue_date = None
                item.due_date = None
                item.pdf_paths_json = None
                db.add(
                    AuditEvent(
                        action="INVOICE_ISSUE_FAILED",
                        entity_type="INVOICE",
                        entity_id=invoice_id,
                        details_json={"attempt_id": attempt_id, "reason": "SOURCE_SET_CHANGED"},
                    )
                )
                db.commit()
                raise
            attempt = db.get(InvoiceIssueAttempt, attempt_id)
            for language, output_path in final_paths.items():
                db.add(
                    ExportRecord(
                        export_type="PDF_INVOICE",
                        entity_type="INVOICE",
                        entity_id=item.id,
                        stored_path=str(output_path),
                        sha256=hashes[language],
                        language=language,
                    )
                )
            item.pdf_paths_json = pdf_paths
            item.lifecycle_status = "ISSUED"
            item.issued_at = datetime.now(timezone.utc)
            if attempt:
                attempt.status = "COMPLETED"
                attempt.completed_at = item.issued_at
                attempt.details = "Bilingual PDFs archived and Invoice issued"
            db.add(
                AuditEvent(
                    action="INVOICE_ISSUED",
                    entity_type="INVOICE",
                    entity_id=item.id,
                    details_json={"invoice_number": item.invoice_number, "attempt_id": attempt_id},
                )
            )
            try:
                db.commit()
            except IntegrityError as exc:
                db.rollback()
                for path in promoted_paths:
                    path.unlink(missing_ok=True)
                failed = _reload_invoice(db, invoice_id)
                failed_attempt = db.get(InvoiceIssueAttempt, attempt_id)
                if failed_attempt:
                    failed_attempt.status = "FAILED"
                    failed_attempt.completed_at = datetime.now(timezone.utc)
                    failed_attempt.details = "Database invariant rejected Invoice completion"
                if failed.lifecycle_status == "ISSUING":
                    failed.lifecycle_status = "DRAFT"
                    failed.invoice_number = None
                    failed.issue_date = None
                    failed.due_date = None
                    failed.pdf_paths_json = None
                    db.add(
                        AuditEvent(
                            action="INVOICE_ISSUE_FAILED",
                            entity_type="INVOICE",
                            entity_id=invoice_id,
                            details_json={"attempt_id": attempt_id, "reason": "DATABASE_INVARIANT"},
                        )
                    )
                db.commit()
                raise HTTPException(
                    status_code=409,
                    detail="Invoice签发期间来源或金额发生变化，已恢复Draft且编号不会复用",
                ) from exc
    finally:
        with _invoice_issue_lock:
            _active_issue_ids.discard(invoice_id)
    db.expire_all()
    return invoice_dict(_reload_invoice(db, invoice_id))


@router.post("/{invoice_id}/recover-issuing")
def recover_issuing_invoice(
    invoice_id: int,
    payload: InvoiceIssueRecoveryRequest,
    db: Session = Depends(get_db),
) -> dict:
    with _invoice_issue_lock:
        if invoice_id in _active_issue_ids:
            raise HTTPException(status_code=409, detail="Invoice PDF仍在当前进程签发中，请等待完成")
        item = _reload_invoice(db, invoice_id)
        if item.lifecycle_status != "ISSUING":
            raise HTTPException(status_code=409, detail="只有遗留ISSUING Invoice需要恢复")

        if payload.action == "COMPLETE":
            if not item.invoice_number:
                raise HTTPException(status_code=409, detail="ISSUING Invoice缺少已预留编号，不能完成签发")
            pdf_root = get_settings().data_root / "output" / "pdf"
            recovery_path_sets = invoice_recovery_path_sets(item.invoice_number, pdf_root)
            if any(
                not is_within(path, pdf_root)
                for path_set in recovery_path_sets
                for path in path_set.values()
            ):
                raise HTTPException(status_code=409, detail="Invoice归档路径不安全，不能自动完成签发")
            complete_path_sets = [
                path_set
                for path_set in recovery_path_sets
                if all(path.is_file() for path in path_set.values())
            ]
            if not complete_path_sets:
                raise HTTPException(status_code=409, detail="中文和英文Invoice PDF必须都完整存在于安全归档目录")
            if len(complete_path_sets) > 1:
                raise HTTPException(
                    status_code=409,
                    detail="同时发现哈希归档和旧版原名双语归档，不能自动完成签发；请人工核对后仅保留一组",
                )
            final_paths = complete_path_sets[0]
            _validate_issue_sources(db, item)
            hashes = {language: file_sha256(path) for language, path in final_paths.items()}
            attempt = _ensure_recovery_attempt(db, item)
            for language, output_path in final_paths.items():
                db.add(
                    ExportRecord(
                        export_type="PDF_INVOICE",
                        entity_type="INVOICE",
                        entity_id=item.id,
                        stored_path=str(output_path),
                        sha256=hashes[language],
                        language=language,
                    )
                )
            item.pdf_paths_json = {language: str(path) for language, path in final_paths.items()}
            item.lifecycle_status = "ISSUED"
            item.issued_at = datetime.now(timezone.utc)
            attempt.status = "COMPLETED"
            attempt.completed_at = item.issued_at
            attempt.details = "Recovered ISSUING state from two complete archived PDFs"
            audit_action = "INVOICE_ISSUING_RECOVERED_COMPLETE"
        else:
            if item.invoice_number:
                pdf_root = get_settings().data_root / "output" / "pdf"
                recovery_path_sets = invoice_recovery_path_sets(item.invoice_number, pdf_root)
                cleanup_paths = [
                    path
                    for path_set in recovery_path_sets
                    for path in path_set.values()
                ]
                if pdf_root.is_dir():
                    for path_set in recovery_path_sets:
                        for final_path in path_set.values():
                            cleanup_paths.extend(pdf_root.glob(f".{final_path.name}.*.tmp"))
                for path in cleanup_paths:
                    if not is_within(path, pdf_root):
                        raise HTTPException(status_code=409, detail="Invoice部分文件路径不安全，不能自动清理")
                    if path.is_file():
                        try:
                            path.unlink()
                        except OSError as exc:
                            raise HTTPException(status_code=500, detail=f"无法清理部分Invoice文件: {path.name}") from exc
            attempt = _reserved_attempt(item)
            if not attempt and item.invoice_number:
                attempt = _ensure_recovery_attempt(db, item)
            if attempt:
                attempt.status = "RECOVERED_TO_DRAFT"
                attempt.completed_at = datetime.now(timezone.utc)
                attempt.details = "Partial files removed and Invoice returned to Draft; number remains consumed"
            previous_number = item.invoice_number
            item.lifecycle_status = "DRAFT"
            item.invoice_number = None
            item.issue_date = None
            item.due_date = None
            item.pdf_paths_json = None
            audit_action = "INVOICE_ISSUING_RETURNED_TO_DRAFT"

        db.add(
            AuditEvent(
                action=audit_action,
                entity_type="INVOICE",
                entity_id=item.id,
                details_json={
                    "action": payload.action,
                    "invoice_number": item.invoice_number if payload.action == "COMPLETE" else previous_number,
                    "attempt_id": attempt.id if attempt else None,
                },
            )
        )
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if payload.action == "COMPLETE":
                detail = "Invoice来源或金额已变化，仍保留ISSUING及双PDF，请重新核对后恢复"
            else:
                detail = "Invoice状态已变化，部分文件已清理，请刷新后再次退回Draft"
            raise HTTPException(status_code=409, detail=detail) from exc
    db.expire_all()
    return invoice_dict(_reload_invoice(db, invoice_id))


@router.post("/{invoice_id}/void")
def void_invoice(invoice_id: int, payload: VoidRequest, db: Session = Depends(get_db)) -> dict:
    item = _reload_invoice(db, invoice_id)
    if item.lifecycle_status == "VOID":
        raise HTTPException(status_code=409, detail="Invoice已经作废")
    if item.lifecycle_status == "ISSUING":
        raise HTTPException(status_code=409, detail="Invoice正在签发，不能作废")
    if item.payments:
        raise HTTPException(status_code=409, detail="已有付款记录的Invoice不能直接作废")
    item.lifecycle_status = "VOID"
    item.voided_at = datetime.now(timezone.utc)
    item.void_reason = payload.reason
    db.add(
        AuditEvent(
            action="INVOICE_VOIDED",
            entity_type="INVOICE",
            entity_id=item.id,
            details_json={"invoice_number": item.invoice_number, "reason": payload.reason},
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invoice已有付款或状态已变化，不能作废") from exc
    db.expire_all()
    return invoice_dict(_reload_invoice(db, invoice_id))


@router.post("/{invoice_id}/payments", status_code=201)
def create_payment(invoice_id: int, payload: PaymentCreate, db: Session = Depends(get_db)) -> dict:
    item = _reload_invoice(db, invoice_id)
    if item.lifecycle_status != "ISSUED":
        raise HTTPException(status_code=409, detail="只有Issued Invoice可以登记付款")
    amount_cents = to_cents(payload.amount)
    paid_cents = sum(payment.amount_cents for payment in item.payments)
    if paid_cents + amount_cents > item.amount_cents:
        raise HTTPException(status_code=400, detail="付款总额不能超过Invoice金额")
    payment = Payment(
        invoice_id=item.id,
        payment_date=payload.payment_date,
        amount_cents=amount_cents,
        method=payload.method,
        remark=payload.remark,
    )
    db.add(payment)
    db.add(
        AuditEvent(
            action="PAYMENT_RECORDED",
            entity_type="INVOICE",
            entity_id=item.id,
            details_json={"amount_cents": amount_cents, "method": payload.method},
        )
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invoice状态或累计付款金额已被并发请求改变") from exc
    db.expire_all()
    return invoice_dict(_reload_invoice(db, invoice_id))


@router.post("/{invoice_id}/pdf")
def download_invoice_pdf(
    invoice_id: int,
    language: str = Query(default="zh", pattern="^(zh|en)$"),
    db: Session = Depends(get_db),
) -> FileResponse:
    item = _reload_invoice(db, invoice_id)
    if item.lifecycle_status not in {"ISSUED", "VOID"}:
        raise HTTPException(status_code=409, detail="只有曾经Issued的Invoice才有正式PDF")
    settings = get_settings()
    filename = invoice_download_filename(item.invoice_number, item.id, language)
    paths = dict(item.pdf_paths_json or {})
    recorded_path = paths.get(language)
    if recorded_path:
        output_path = Path(recorded_path)
        if not output_path.is_file() or not is_within(output_path, settings.data_root / "output" / "pdf"):
            raise HTTPException(status_code=404, detail="原始Invoice PDF缺失，请从备份恢复")
        archive_record = db.scalar(
            select(ExportRecord)
            .where(
                ExportRecord.export_type == "PDF_INVOICE",
                ExportRecord.entity_type == "INVOICE",
                ExportRecord.entity_id == item.id,
                ExportRecord.language == language,
                ExportRecord.stored_path == recorded_path,
            )
            .order_by(ExportRecord.id.desc())
            .limit(1)
        )
        if not archive_record:
            raise HTTPException(status_code=409, detail="Invoice PDF归档审计记录缺失，请从备份恢复")
        try:
            current_hash = file_sha256(output_path)
        except OSError as exc:
            raise HTTPException(status_code=404, detail="原始Invoice PDF无法读取，请从备份恢复") from exc
        if current_hash.casefold() != archive_record.sha256.casefold():
            raise HTTPException(status_code=409, detail="Invoice PDF归档哈希校验失败，请从备份恢复")
    else:
        # Compatibility for an Invoice issued by an earlier build: create the
        # immutable archive once from its migrated frozen InvoiceLine rows.
        # The final path is never trusted unless the same transaction records it.
        with _invoice_issue_lock:
            db.expire_all()
            item = _reload_invoice(db, invoice_id)
            if item.lifecycle_status not in {"ISSUED", "VOID"}:
                raise HTTPException(status_code=409, detail="Invoice状态已变化，请刷新后重试")
            paths = dict(item.pdf_paths_json or {})
            recorded_path = paths.get(language)
            if recorded_path:
                output_path = Path(recorded_path)
                if not output_path.is_file() or not is_within(
                    output_path, settings.data_root / "output" / "pdf"
                ):
                    raise HTTPException(status_code=404, detail="原始Invoice PDF缺失，请从备份恢复")
            else:
                pdf_root = settings.data_root / "output" / "pdf"
                output_path = _invoice_final_paths(item.invoice_number)[language]
                if not is_within(output_path, pdf_root):
                    raise HTTPException(status_code=409, detail="Invoice归档路径不安全")
                if output_path.exists():
                    raise HTTPException(
                        status_code=409,
                        detail="发现未登记的Invoice归档文件，请人工核对后恢复，系统不会覆盖或直接采信",
                    )
                temp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
                try:
                    generate_invoice_pdf(invoice=item, language=language, output_path=temp_path)
                    digest = file_sha256(temp_path)
                    temp_path.replace(output_path)
                except Exception as exc:
                    temp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=500, detail="旧版Invoice PDF补建失败，未写入正式归档") from exc
                paths[language] = str(output_path)
                item.pdf_paths_json = paths
                db.add(
                    ExportRecord(
                        export_type="PDF_INVOICE",
                        entity_type="INVOICE",
                        entity_id=item.id,
                        stored_path=str(output_path),
                        sha256=digest,
                        language=language,
                    )
                )
                try:
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    output_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=500, detail="旧版Invoice PDF归档登记失败") from exc
    return FileResponse(output_path, media_type="application/pdf", filename=filename)
