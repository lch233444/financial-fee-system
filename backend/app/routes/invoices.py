from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..database import get_db
from ..models import (
    AuditEvent,
    Client,
    ExportRecord,
    Invoice,
    InvoiceSequence,
    Payment,
    QuarterlySettlement,
    SettlementAccountLine,
)
from ..money import to_cents
from ..schemas import InvoiceDraftCreate, InvoiceIssueRequest, PaymentCreate, VoidRequest
from ..serializers import invoice_dict
from ..services.excel_export import file_sha256
from ..services.pdf_invoice import generate_settlement_pdf
from ..services.storage import is_within


router = APIRouter(prefix="/api/invoices", tags=["invoices"])
_invoice_issue_lock = Lock()


def _invoice_query():
    return select(Invoice).options(
        selectinload(Invoice.company),
        selectinload(Invoice.fc),
        selectinload(Invoice.payments),
        selectinload(Invoice.settlement).selectinload(QuarterlySettlement.client).selectinload(Client.company),
        selectinload(Invoice.settlement).selectinload(QuarterlySettlement.client).selectinload(Client.fc),
        selectinload(Invoice.settlement).selectinload(QuarterlySettlement.platform),
        selectinload(Invoice.settlement).selectinload(QuarterlySettlement.fee_plan),
        selectinload(Invoice.settlement)
        .selectinload(QuarterlySettlement.account_lines)
        .selectinload(SettlementAccountLine.account),
    )


@router.get("")
def list_invoices(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(_invoice_query().order_by(Invoice.created_at.desc())).unique().all()
    return [invoice_dict(item) for item in items]


@router.post("", status_code=201)
def create_invoice_draft(payload: InvoiceDraftCreate, db: Session = Depends(get_db)) -> dict:
    settlement = db.get(QuarterlySettlement, payload.settlement_id)
    if not settlement:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    if settlement.status != "FINALIZED":
        raise HTTPException(status_code=409, detail="只有Finalized Settlement可以生成Invoice")
    if settlement.service_fee_cents <= 0:
        raise HTTPException(status_code=400, detail="Service Fee为0时只生成结算单，不生成Invoice")
    client = db.get(Client, settlement.client_id)
    if not client or not settlement.company_id or not settlement.fc_id or not client.management_start_date:
        raise HTTPException(status_code=400, detail="Client必须补全Company、FC和Management Start Date")
    existing = db.scalar(
        select(Invoice).where(
            Invoice.settlement_id == settlement.id,
            Invoice.lifecycle_status.in_(["DRAFT", "ISSUING", "ISSUED"]),
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail="该Settlement已有有效Invoice")
    item = Invoice(
        settlement_id=settlement.id,
        company_id=settlement.company_id,
        fc_id=settlement.fc_id,
        amount_cents=settlement.service_fee_cents,
        language=payload.language,
        lifecycle_status="DRAFT",
    )
    db.add(item)
    db.commit()
    item = db.scalar(_invoice_query().where(Invoice.id == item.id))
    return invoice_dict(item)


def _next_number(db: Session, invoice: Invoice) -> str:
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
    sequence.last_number += 1
    client = invoice.settlement.client
    if not client.management_start_date:
        raise HTTPException(status_code=400, detail="Client缺少Management Start Date")
    yyyymm = client.management_start_date.strftime("%Y%m")
    return f"{invoice.company.code}-{invoice.fc.code}-{yyyymm}-{sequence.last_number:03d}"


@router.post("/{invoice_id}/issue")
def issue_invoice(
    invoice_id: int,
    payload: InvoiceIssueRequest,
    db: Session = Depends(get_db),
) -> dict:
    # Reserve the sequence in a short serialized transaction. Rendering the
    # two PDFs must never hold the SQLite writer lock.
    with _invoice_issue_lock:
        item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
        if not item:
            raise HTTPException(status_code=404, detail="Invoice不存在")
        if item.lifecycle_status != "DRAFT":
            raise HTTPException(status_code=409, detail="只有Draft Invoice可以Issued")
        if item.settlement.status != "FINALIZED":
            raise HTTPException(status_code=409, detail="Settlement不是Finalized状态")
        issue_date = payload.issue_date or date.today()
        due_date = payload.due_date or (issue_date + timedelta(days=item.company.payment_terms_days))
        if due_date < issue_date:
            raise HTTPException(status_code=400, detail="Payment Due Date不能早于Issue Date")
        item.invoice_number = _next_number(db, item)
        item.issue_date = issue_date
        item.due_date = due_date
        item.language = payload.language
        item.lifecycle_status = "ISSUING"
        db.commit()

    settings = get_settings()
    pdf_paths: dict[str, str] = {}
    temp_paths: dict[str, Path] = {}
    final_paths: dict[str, Path] = {}
    hashes: dict[str, str] = {}
    try:
        for language in ("zh", "en"):
            filename = f"{item.invoice_number}_{language}.pdf"
            output_path = settings.data_root / "output" / "pdf" / filename
            temp_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.tmp")
            generate_settlement_pdf(
                settlement=item.settlement,
                invoice=item,
                language=language,
                output_path=temp_path,
            )
            temp_paths[language] = temp_path
            final_paths[language] = output_path
            hashes[language] = file_sha256(temp_path)
        for language, temp_path in temp_paths.items():
            output_path = final_paths[language]
            temp_path.replace(output_path)
            pdf_paths[language] = str(output_path)
    except Exception as exc:
        db.rollback()
        for path in [*temp_paths.values(), *final_paths.values()]:
            path.unlink(missing_ok=True)
        with _invoice_issue_lock:
            failed = db.get(Invoice, invoice_id)
            if failed and failed.lifecycle_status == "ISSUING":
                failed.lifecycle_status = "DRAFT"
                failed.invoice_number = None
                failed.issue_date = None
                failed.due_date = None
                db.commit()
        raise HTTPException(status_code=500, detail="Invoice PDF生成失败，Invoice已恢复为Draft") from exc

    with _invoice_issue_lock:
        item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
        if not item or item.lifecycle_status != "ISSUING":
            for path in final_paths.values():
                path.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail="Invoice签发状态已变化，请刷新后重试")
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
        db.add(
            AuditEvent(
                action="INVOICE_ISSUED",
                entity_type="INVOICE",
                entity_id=item.id,
                details_json={"invoice_number": item.invoice_number},
            )
        )
        db.commit()
    db.expire_all()
    item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
    return invoice_dict(item)


@router.post("/{invoice_id}/void")
def void_invoice(invoice_id: int, payload: VoidRequest, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
    if not item:
        raise HTTPException(status_code=404, detail="Invoice不存在")
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
    db.commit()
    return invoice_dict(item)


@router.post("/{invoice_id}/payments", status_code=201)
def create_payment(invoice_id: int, payload: PaymentCreate, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
    if not item:
        raise HTTPException(status_code=404, detail="Invoice不存在")
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
    db.commit()
    db.expire_all()
    item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
    return invoice_dict(item)


@router.post("/{invoice_id}/pdf")
def download_invoice_pdf(
    invoice_id: int,
    language: str = Query(default="zh", pattern="^(zh|en)$"),
    db: Session = Depends(get_db),
) -> FileResponse:
    item = db.scalar(_invoice_query().where(Invoice.id == invoice_id))
    if not item:
        raise HTTPException(status_code=404, detail="Invoice不存在")
    if item.lifecycle_status not in {"ISSUED", "VOID"}:
        raise HTTPException(status_code=409, detail="只有曾经Issued的Invoice才有正式PDF")
    settings = get_settings()
    filename = f"{item.invoice_number}_{language}.pdf"
    paths = dict(item.pdf_paths_json or {})
    recorded_path = paths.get(language)
    if recorded_path:
        output_path = Path(recorded_path)
        if not output_path.exists() or not is_within(output_path, settings.data_root / "output" / "pdf"):
            raise HTTPException(status_code=404, detail="原始Invoice PDF缺失，请从备份恢复")
    else:
        # Compatibility for an Invoice issued by an earlier MVP build: create
        # the immutable archive once, then always return that exact file.
        output_path = settings.data_root / "output" / "pdf" / filename
        generate_settlement_pdf(
            settlement=item.settlement,
            invoice=item,
            language=language,
            output_path=output_path,
        )
        paths[language] = str(output_path)
        item.pdf_paths_json = paths
        db.add(
            ExportRecord(
                export_type="PDF_INVOICE",
                entity_type="INVOICE",
                entity_id=item.id,
                stored_path=str(output_path),
                sha256=file_sha256(output_path),
                language=language,
            )
        )
        db.commit()
    return FileResponse(output_path, media_type="application/pdf", filename=filename)
