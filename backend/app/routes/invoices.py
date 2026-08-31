from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..database import get_db
from ..models import (
    AuditEvent,
    Attachment,
    Client,
    ExportRecord,
    Invoice,
    InvoiceAdjustment,
    InvoiceCorrection,
    InvoiceIssueAttempt,
    InvoiceLine,
    InvoiceSequence,
    InvoiceSource,
    Payment,
    PaymentAllocation,
    PaymentRefund,
    QuarterlySettlement,
    SettlementAccountLine,
)
from ..money import to_cents
from ..schemas import (
    InvoiceDraftCreate,
    InvoiceCorrectionComplete,
    InvoiceCorrectionCreate,
    InvoiceIssueRecoveryRequest,
    InvoiceIssueRequest,
    PaymentCreate,
    VoidRequest,
)
from ..serializers import invoice_accounting_cents, invoice_correction_dict, invoice_dict
from ..services.excel_export import file_sha256
from ..services.invoice_archive import (
    invoice_archive_paths,
    invoice_download_filename,
    invoice_recovery_path_sets,
)
from ..services.pdf_invoice import generate_invoice_pdf
from ..services.storage import is_within, sha256_file


router = APIRouter(prefix="/api/invoices", tags=["invoices"])
correction_router = APIRouter(prefix="/api/invoice-corrections", tags=["invoice-corrections"])
_invoice_issue_lock = Lock()
_active_issue_ids: set[int] = set()
ACTIVE_INVOICE_STATUSES = ("DRAFT", "ISSUING", "ISSUED")


def _invoice_query():
    return select(Invoice).options(
        selectinload(Invoice.company),
        selectinload(Invoice.fc),
        selectinload(Invoice.client),
        selectinload(Invoice.fee_plan),
        selectinload(Invoice.payments).selectinload(Payment.allocations),
        selectinload(Invoice.payments).selectinload(Payment.refunds),
        selectinload(Invoice.payment_allocations).selectinload(PaymentAllocation.payment),
        selectinload(Invoice.payment_allocations).selectinload(PaymentAllocation.correction),
        selectinload(Invoice.adjustments).selectinload(InvoiceAdjustment.correction),
        selectinload(Invoice.sources).selectinload(InvoiceSource.settlement),
        selectinload(Invoice.lines)
        .selectinload(InvoiceLine.source_account_line)
        .selectinload(SettlementAccountLine.account),
        selectinload(Invoice.issue_attempts),
        selectinload(Invoice.settlement)
        .selectinload(QuarterlySettlement.account_lines)
        .selectinload(SettlementAccountLine.account),
    )


def _correction_query():
    return select(InvoiceCorrection).options(
        selectinload(InvoiceCorrection.original_invoice).selectinload(Invoice.payments),
        selectinload(InvoiceCorrection.original_invoice)
        .selectinload(Invoice.sources)
        .selectinload(InvoiceSource.settlement),
        selectinload(InvoiceCorrection.replacement_invoice),
        selectinload(InvoiceCorrection.allocations).selectinload(PaymentAllocation.payment),
        selectinload(InvoiceCorrection.refunds),
        selectinload(InvoiceCorrection.adjustments),
    )


def _is_sqlite_busy(exc: OperationalError) -> bool:
    message = str(exc.orig).casefold()
    return "database is locked" in message or "database table is locked" in message or "database is busy" in message


def _begin_immediate(db: Session) -> None:
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_busy(exc):
            raise HTTPException(status_code=409, detail="数据库正在处理另一笔财务写入，请稍后重试") from exc
        raise


def _flush_financial_state(db: Session, detail: str) -> None:
    try:
        db.flush()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=detail) from exc


def _require_unclaimed_proof(db: Session, attachment_id: int, entity_type: str) -> Attachment:
    attachment = db.get(Attachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=400, detail="付款凭证记录不存在")
    if attachment.entity_type != entity_type:
        raise HTTPException(status_code=400, detail=f"凭证类型必须为{entity_type}")
    if attachment.entity_id is not None:
        raise HTTPException(status_code=409, detail="凭证已被其他财务记录认领")

    path = Path(attachment.stored_path)
    root = get_settings().data_root / "attachments"
    if not is_within(path, root):
        raise HTTPException(status_code=400, detail="凭证文件路径不在系统受控目录")
    try:
        if not path.is_file():
            raise HTTPException(status_code=400, detail="凭证原文件不存在或不是普通文件")
        size_bytes = path.stat().st_size
        if size_bytes <= 0:
            raise HTTPException(status_code=400, detail="凭证原文件为空")
        if size_bytes != attachment.size_bytes:
            raise HTTPException(status_code=400, detail="凭证文件大小与记录不一致")
        digest = sha256_file(path)
    except OSError as exc:
        raise HTTPException(status_code=400, detail="凭证原文件无法读取") from exc
    if digest.casefold() != attachment.sha256.casefold():
        raise HTTPException(status_code=400, detail="凭证文件SHA-256与记录不一致")
    return attachment


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


def _reload_correction(db: Session, correction_id: int) -> InvoiceCorrection:
    item = db.scalar(_correction_query().where(InvoiceCorrection.id == correction_id))
    if item is None:
        raise HTTPException(status_code=404, detail="Invoice更正记录不存在")
    return item


def _active_apply_allocations(item: Invoice) -> list[PaymentAllocation]:
    reversed_ids = {
        allocation.reverses_allocation_id
        for allocation in item.payment_allocations
        if allocation.entry_type == "REVERSAL"
    }
    return [
        allocation
        for allocation in item.payment_allocations
        if allocation.entry_type == "APPLY" and allocation.id not in reversed_ids
    ]


def _settlements_follow_original(
    db: Session, original: Invoice, settlements: list[QuarterlySettlement]
) -> bool:
    original_ids = {source.settlement_id for source in original.sources}
    if not original_ids or len(settlements) != len(original_ids):
        return False

    matched_original_ids: set[int] = set()
    for settlement in settlements:
        if settlement.version_no <= 1:
            return False
        current = settlement
        visited: set[int] = set()
        matched = None
        while current is not None and current.id not in visited:
            visited.add(current.id)
            replaced_id = current.replaces_settlement_id
            if replaced_id is None:
                break
            if replaced_id in original_ids:
                matched = replaced_id
                break
            current = db.get(QuarterlySettlement, replaced_id)
        if matched is None or matched in matched_original_ids:
            return False
        matched_original_ids.add(matched)
    return matched_original_ids == original_ids


def _replacement_sources_follow_original(
    db: Session, original: Invoice, replacement: Invoice
) -> bool:
    active_sources = [source for source in replacement.sources if source.active]
    if any(source.settlement is None for source in active_sources):
        return False
    return _settlements_follow_original(
        db,
        original,
        [source.settlement for source in active_sources],
    )


def _open_correction_for_group(
    db: Session, *, client_id: int, year: int, quarter: int, fee_plan_id: int
) -> InvoiceCorrection | None:
    return db.scalar(
        _correction_query()
        .join(Invoice, Invoice.id == InvoiceCorrection.original_invoice_id)
        .where(
            InvoiceCorrection.status == "OPEN",
            Invoice.client_id == client_id,
            Invoice.year == year,
            Invoice.quarter == quarter,
            Invoice.fee_plan_id == fee_plan_id,
        )
        .order_by(InvoiceCorrection.id)
        .limit(1)
    )


def _validate_open_correction_sources(
    db: Session,
    *,
    client_id: int,
    year: int,
    quarter: int,
    fee_plan_id: int,
    settlements: list[QuarterlySettlement],
) -> None:
    correction = _open_correction_for_group(
        db,
        client_id=client_id,
        year=year,
        quarter=quarter,
        fee_plan_id=fee_plan_id,
    )
    if correction and not _settlements_follow_original(db, correction.original_invoice, settlements):
        raise HTTPException(
            status_code=409,
            detail="该分组正在更正，必须先作废原Settlement并使用完整的替代版本链重建Invoice",
        )


def _pending_replacement_correction(
    db: Session, invoice: Invoice
) -> InvoiceCorrection | None:
    """Return the OPEN correction for which ``invoice`` is a valid replacement.

    A replacement Invoice must stay financially blank until the correction is
    completed.  Otherwise a user could record a second cash receipt on the new
    Invoice and make the already-open correction impossible to complete.
    """

    correction = _open_correction_for_group(
        db,
        client_id=invoice.client_id,
        year=invoice.year,
        quarter=invoice.quarter,
        fee_plan_id=invoice.fee_plan_id,
    )
    if (
        correction is None
        or correction.original_invoice_id == invoice.id
        or not _replacement_sources_follow_original(db, correction.original_invoice, invoice)
    ):
        return None
    return correction


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
    _validate_open_correction_sources(
        db,
        client_id=payload.client_id,
        year=payload.year,
        quarter=payload.quarter,
        fee_plan_id=payload.fee_plan_id,
        settlements=settlements,
    )

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
    _validate_open_correction_sources(
        db,
        client_id=invoice.client_id,
        year=invoice.year,
        quarter=invoice.quarter,
        fee_plan_id=invoice.fee_plan_id,
        settlements=[source.settlement for source in invoice.sources if source.active],
    )


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
    if _active_apply_allocations(item):
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


@router.post("/{invoice_id}/corrections", status_code=201)
def create_invoice_correction(
    invoice_id: int,
    payload: InvoiceCorrectionCreate,
    db: Session = Depends(get_db),
) -> dict:
    _begin_immediate(db)
    item = _reload_invoice(db, invoice_id)
    if item.lifecycle_status != "ISSUED":
        raise HTTPException(status_code=409, detail="只有Issued Invoice可以发起受控更正")
    pending_replacement = _pending_replacement_correction(db, item)
    if pending_replacement is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该Invoice是更正 #{pending_replacement.id} 的待关联替代单；"
                "请先完成上一笔更正，不能在此期间再发起Correction"
            ),
        )
    if item.original_correction is not None:
        raise HTTPException(status_code=409, detail="该Invoice已有更正记录")

    active_allocations = _active_apply_allocations(item)
    if active_allocations:
        _, _, outstanding_cents = invoice_accounting_cents(item)
        if outstanding_cents != 0:
            raise HTTPException(status_code=409, detail="原Invoice付款台账未完整平账，不能发起更正")
        if any(allocation.payment is None for allocation in active_allocations):
            raise HTTPException(status_code=409, detail="原Invoice的现金分配缺少Payment历史")
    elif item.adjustments:
        raise HTTPException(status_code=409, detail="原Invoice只有差额而没有现金分配，请先人工核对")

    correction = InvoiceCorrection(
        original_invoice_id=item.id,
        status="OPEN",
        reason=payload.reason,
        opened_at=datetime.now(timezone.utc),
    )
    db.add(correction)
    _flush_financial_state(db, "Invoice状态或更正记录已变化，更正未生效")

    reversals: list[PaymentAllocation] = []
    for allocation in active_allocations:
        reversal = PaymentAllocation(
            payment_id=allocation.payment_id,
            invoice_id=item.id,
            amount_cents=allocation.amount_cents,
            entry_type="REVERSAL",
            reverses_allocation_id=allocation.id,
            correction_id=correction.id,
        )
        db.add(reversal)
        reversals.append(reversal)
    _flush_financial_state(db, "Invoice付款分配已变化，更正未生效")

    item.lifecycle_status = "VOID"
    item.voided_at = datetime.now(timezone.utc)
    item.void_reason = payload.reason
    _flush_financial_state(db, "Invoice状态或作废审计已变化，更正未生效")

    db.add(
        AuditEvent(
            action="INVOICE_CORRECTION_OPENED",
            entity_type="INVOICE_CORRECTION",
            entity_id=correction.id,
            details_json={"original_invoice_id": item.id, "reason": payload.reason},
        )
    )
    db.add(
        AuditEvent(
            action="INVOICE_VOIDED_FOR_CORRECTION",
            entity_type="INVOICE",
            entity_id=item.id,
            details_json={"correction_id": correction.id, "reason": payload.reason},
        )
    )
    for reversal in reversals:
        db.add(
            AuditEvent(
                action="PAYMENT_ALLOCATION_REVERSED",
                entity_type="PAYMENT_ALLOCATION",
                entity_id=reversal.id,
                details_json={
                    "correction_id": correction.id,
                    "payment_id": reversal.payment_id,
                    "reverses_allocation_id": reversal.reverses_allocation_id,
                    "amount_cents": reversal.amount_cents,
                },
            )
        )
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invoice状态或付款台账已变化，更正未生效") from exc
    db.expire_all()
    return invoice_correction_dict(_reload_correction(db, correction.id))


@router.post("/{invoice_id}/payments", status_code=201)
def create_payment(invoice_id: int, payload: PaymentCreate, db: Session = Depends(get_db)) -> dict:
    _begin_immediate(db)
    item = _reload_invoice(db, invoice_id)
    if item.lifecycle_status != "ISSUED":
        raise HTTPException(status_code=409, detail="只有Issued Invoice可以登记付款")
    pending_replacement = _pending_replacement_correction(db, item)
    if pending_replacement is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"该Invoice是更正 #{pending_replacement.id} 的待关联替代单；"
                "请先完成更正中的现金转配或退款，不能登记新的Payment"
            ),
        )
    amount_cents = to_cents(payload.amount)
    difference_cents = to_cents(payload.company_difference)
    paid_cents, adjustment_cents, outstanding_cents = invoice_accounting_cents(item)
    if item.payments or item.payment_allocations or item.adjustments or paid_cents or adjustment_cents:
        raise HTTPException(status_code=409, detail="Invoice已完成付款确认，不能重复登记")
    if outstanding_cents != item.amount_cents:
        raise HTTPException(status_code=409, detail="Invoice付款台账状态异常，请先人工核对")
    if amount_cents + difference_cents != item.amount_cents:
        raise HTTPException(
            status_code=400,
            detail="现金付款与公司承担差额必须精确等于Invoice金额，不允许部分付款",
        )
    _require_unclaimed_proof(db, payload.proof_attachment_id, "PAYMENT")
    # The database creates both the optional immutable company adjustment and
    # the initial APPLY row from this Payment in one AFTER INSERT trigger.  A
    # standalone ordinary adjustment is deliberately impossible, so direct SQL
    # cannot mark an Invoice PAID without the matching cash fact.
    payment = Payment(
        invoice_id=item.id,
        payment_date=payload.payment_date,
        amount_cents=amount_cents,
        company_difference_cents=difference_cents,
        difference_reason=payload.difference_reason,
        method=payload.method,
        proof_attachment_id=payload.proof_attachment_id,
        remark=payload.remark,
    )
    db.add(payment)
    try:
        db.flush()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Invoice状态、凭证或付款确认已被并发请求改变",
        ) from exc
    allocation = db.scalar(
        select(PaymentAllocation).where(
            PaymentAllocation.payment_id == payment.id,
            PaymentAllocation.invoice_id == item.id,
            PaymentAllocation.entry_type == "APPLY",
            PaymentAllocation.correction_id.is_(None),
        )
    )
    if allocation is None:
        db.rollback()
        raise HTTPException(status_code=409, detail="Payment现金分配台账未能原子建立")
    adjustment = db.scalar(
        select(InvoiceAdjustment).where(InvoiceAdjustment.payment_id == payment.id)
    )
    if (difference_cents > 0) != (adjustment is not None):
        db.rollback()
        raise HTTPException(status_code=409, detail="Payment公司差额台账未能原子建立")
    db.add(
        AuditEvent(
            action="PAYMENT_RECORDED",
            entity_type="PAYMENT",
            entity_id=payment.id,
            details_json={
                "invoice_id": item.id,
                "amount_cents": amount_cents,
                "method": payload.method,
                "proof_attachment_id": payload.proof_attachment_id,
            },
        )
    )
    db.add(
        AuditEvent(
            action="PAYMENT_ALLOCATION_APPLIED",
            entity_type="PAYMENT_ALLOCATION",
            entity_id=allocation.id,
            details_json={"invoice_id": item.id, "payment_id": payment.id, "amount_cents": amount_cents},
        )
    )
    if adjustment is not None:
        db.add(
            AuditEvent(
                action="INVOICE_ADJUSTMENT_RECORDED",
                entity_type="INVOICE_ADJUSTMENT",
                entity_id=adjustment.id,
                details_json={
                    "invoice_id": item.id,
                    "amount_cents": difference_cents,
                    "reason": payload.difference_reason,
                },
            )
        )
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Invoice状态、凭证或付款确认已被并发请求改变") from exc
    db.expire_all()
    return invoice_dict(_reload_invoice(db, invoice_id))


@correction_router.get("")
def list_invoice_corrections(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(
        _correction_query().order_by(InvoiceCorrection.opened_at.desc(), InvoiceCorrection.id.desc())
    ).unique().all()
    return [invoice_correction_dict(item) for item in items]


@correction_router.get("/{correction_id}")
def get_invoice_correction(correction_id: int, db: Session = Depends(get_db)) -> dict:
    return invoice_correction_dict(_reload_correction(db, correction_id))


@correction_router.post("/{correction_id}/complete")
def complete_invoice_correction(
    correction_id: int,
    payload: InvoiceCorrectionComplete,
    db: Session = Depends(get_db),
) -> dict:
    _begin_immediate(db)
    correction = _reload_correction(db, correction_id)
    if correction.status != "OPEN":
        raise HTTPException(status_code=409, detail="Invoice更正已完成，不能重复提交")

    original = _reload_invoice(db, correction.original_invoice_id)
    replacement = _reload_invoice(db, payload.replacement_invoice_id)
    if original.lifecycle_status != "VOID":
        raise HTTPException(status_code=409, detail="原Invoice必须保持VOID状态")
    if replacement.id == original.id or replacement.lifecycle_status != "ISSUED":
        raise HTTPException(status_code=409, detail="替代Invoice必须是另一笔Issued Invoice")
    if (
        replacement.client_id != original.client_id
        or replacement.year != original.year
        or replacement.quarter != original.quarter
        or replacement.fee_plan_id != original.fee_plan_id
    ):
        raise HTTPException(status_code=409, detail="替代Invoice与原Invoice的Client、年度、季度或Fee Plan不一致")
    if not _replacement_sources_follow_original(db, original, replacement):
        raise HTTPException(status_code=409, detail="替代Invoice的活动Settlement来源未通过完整replaces链覆盖原来源")
    if replacement.replacement_correction is not None:
        raise HTTPException(status_code=409, detail="替代Invoice已被其他更正记录占用")
    if replacement.payments or replacement.payment_allocations or replacement.adjustments:
        raise HTTPException(status_code=409, detail="替代Invoice已有资金或差额台账，不能作为本次更正的空白替代单")

    retained_by_payment: dict[int, int] = {}
    for retained in payload.retained_allocations:
        if retained.payment_id in retained_by_payment:
            raise HTTPException(status_code=400, detail="同一Payment不能重复填写保留分配")
        retained_by_payment[retained.payment_id] = to_cents(retained.amount)

    reversible_by_payment: dict[int, int] = {}
    original_payments: dict[int, Payment] = {}
    for allocation in correction.allocations:
        if allocation.entry_type != "REVERSAL":
            continue
        reversible_by_payment[allocation.payment_id] = (
            reversible_by_payment.get(allocation.payment_id, 0) + allocation.amount_cents
        )
        original_payments[allocation.payment_id] = allocation.payment
    refund_inputs_by_payment: dict[int, list] = {payment_id: [] for payment_id in original_payments}
    proof_ids: set[int] = set()
    for refund in payload.refunds:
        if refund.payment_id not in original_payments:
            raise HTTPException(status_code=400, detail="退款必须引用原Invoice的Payment")
        if refund.proof_attachment_id in proof_ids:
            raise HTTPException(status_code=400, detail="同一退款凭证不能重复使用")
        proof_ids.add(refund.proof_attachment_id)
        refund_inputs_by_payment[refund.payment_id].append(refund)

    difference_cents = to_cents(payload.company_difference)
    if original_payments:
        if set(retained_by_payment) != set(original_payments):
            raise HTTPException(status_code=400, detail="必须为原Invoice的每一笔Payment填写保留分配（可为0）")
        for payment_id in original_payments:
            refunded_cents = sum(
                to_cents(refund.amount) for refund in refund_inputs_by_payment[payment_id]
            )
            if retained_by_payment[payment_id] + refunded_cents != reversible_by_payment[payment_id]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Payment #{payment_id}的保留分配与本轮退款必须精确等于本轮可处置现金",
                )
        retained_total = sum(retained_by_payment.values())
        if retained_total + difference_cents != replacement.amount_cents:
            raise HTTPException(
                status_code=400,
                detail="替代Invoice的保留现金分配与公司承担差额必须精确等于Invoice金额",
            )
    elif retained_by_payment or payload.refunds or difference_cents:
        raise HTTPException(
            status_code=400,
            detail="原Invoice没有Payment，更正完成时只建立替代关系，替代Invoice保持UNPAID",
        )

    for refund in payload.refunds:
        _require_unclaimed_proof(db, refund.proof_attachment_id, "PAYMENT_REFUND")

    correction.replacement_invoice_id = replacement.id
    _flush_financial_state(db, "更正替代关系校验失败，本次提交已回滚")

    new_allocations: list[PaymentAllocation] = []
    for payment_id, amount_cents in retained_by_payment.items():
        if amount_cents == 0:
            continue
        allocation = PaymentAllocation(
            payment_id=payment_id,
            invoice_id=replacement.id,
            amount_cents=amount_cents,
            entry_type="APPLY",
            correction_id=correction.id,
        )
        db.add(allocation)
        new_allocations.append(allocation)

    refunds: list[PaymentRefund] = []
    for refund_input in payload.refunds:
        refund = PaymentRefund(
            payment_id=refund_input.payment_id,
            correction_id=correction.id,
            refund_date=refund_input.refund_date,
            amount_cents=to_cents(refund_input.amount),
            method=refund_input.method,
            reason=refund_input.reason,
            proof_attachment_id=refund_input.proof_attachment_id,
        )
        db.add(refund)
        refunds.append(refund)

    adjustment = None
    if difference_cents:
        adjustment = InvoiceAdjustment(
            invoice_id=replacement.id,
            correction_id=correction.id,
            adjustment_type="COMPANY_BORNE_DIFFERENCE",
            amount_cents=difference_cents,
            reason=payload.difference_reason,
        )
        db.add(adjustment)
    _flush_financial_state(db, "更正资金或凭证校验失败，本次提交已回滚")

    for allocation in new_allocations:
        db.add(
            AuditEvent(
                action="PAYMENT_ALLOCATION_RETAINED",
                entity_type="PAYMENT_ALLOCATION",
                entity_id=allocation.id,
                details_json={
                    "correction_id": correction.id,
                    "payment_id": allocation.payment_id,
                    "replacement_invoice_id": replacement.id,
                    "amount_cents": allocation.amount_cents,
                },
            )
        )
    for refund in refunds:
        db.add(
            AuditEvent(
                action="PAYMENT_REFUNDED",
                entity_type="PAYMENT_REFUND",
                entity_id=refund.id,
                details_json={
                    "correction_id": correction.id,
                    "payment_id": refund.payment_id,
                    "amount_cents": refund.amount_cents,
                    "proof_attachment_id": refund.proof_attachment_id,
                },
            )
        )
    if adjustment is not None:
        db.add(
            AuditEvent(
                action="INVOICE_ADJUSTMENT_RECORDED",
                entity_type="INVOICE_ADJUSTMENT",
                entity_id=adjustment.id,
                details_json={
                    "correction_id": correction.id,
                    "invoice_id": replacement.id,
                    "amount_cents": adjustment.amount_cents,
                    "reason": adjustment.reason,
                },
            )
        )

    correction.status = "COMPLETED"
    correction.completed_at = datetime.now(timezone.utc)
    db.add(
        AuditEvent(
            action="INVOICE_CORRECTION_COMPLETED",
            entity_type="INVOICE_CORRECTION",
            entity_id=correction.id,
            details_json={
                "original_invoice_id": original.id,
                "replacement_invoice_id": replacement.id,
            },
        )
    )
    try:
        db.commit()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="更正资金守恒、凭证或替代关系校验失败，本次提交已回滚") from exc
    db.expire_all()
    return invoice_correction_dict(_reload_correction(db, correction.id))


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
