from __future__ import annotations

import ipaddress
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session, selectinload

from ..config import LOOPBACK_HOSTS, get_settings
from ..database import get_db
from ..models import (
    Client,
    ExportRecord,
    FC,
    Invoice,
    Payment,
    QuarterlySettlement,
    SettlementAccountLine,
    SubAccount,
)
from ..money import money_string
from ..serializers import invoice_payment_status
from ..services.backup import create_backup, stage_restore
from ..services.excel_export import export_settlements_to_template, file_sha256
from ..services.pdf_invoice import generate_settlement_pdf
from ..services.storage import store_bytes
from ..services.shutdown import ShutdownCoordinator, get_shutdown_coordinator


router = APIRouter(prefix="/api", tags=["system"])


def _require_local_shutdown_request(request: Request) -> None:
    """Apply stricter loopback and browser-origin checks to process shutdown."""

    settings = get_settings()
    client_host = request.client.host.casefold() if request.client else ""
    if client_host == "testclient" and settings.testing:
        client_is_loopback = True
    else:
        try:
            client_is_loopback = ipaddress.ip_address(client_host.strip("[]")).is_loopback
        except ValueError:
            client_is_loopback = client_host in LOOPBACK_HOSTS
    if not settings.is_loopback or not client_is_loopback:
        raise HTTPException(status_code=403, detail="安全退出仅允许本机调用")

    if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
        raise HTTPException(status_code=403, detail="拒绝跨站安全退出请求")

    origin = request.headers.get("origin")
    if not origin:
        return
    try:
        parsed = urlsplit(origin)
        origin_port = parsed.port or (80 if parsed.scheme.casefold() == "http" else None)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="安全退出请求来源无效") from exc
    if (
        parsed.scheme.casefold() != "http"
        or (parsed.hostname or "").casefold() not in LOOPBACK_HOSTS
        or origin_port not in {settings.port, 5173}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise HTTPException(status_code=403, detail="拒绝跨站安全退出请求")


@router.post("/shutdown")
def shutdown_system(
    request: Request,
    background_tasks: BackgroundTasks,
    coordinator: ShutdownCoordinator = Depends(get_shutdown_coordinator),
) -> dict:
    _require_local_shutdown_request(request)
    scheduled = coordinator.request()
    if scheduled:
        # Starlette sends the complete response before running background
        # tasks, so the browser receives a success state before teardown.
        background_tasks.add_task(coordinator.execute)
    return {
        "status": "shutting_down",
        "accepted": True,
        "already_requested": not scheduled,
        "message": "系统正在安全退出，本地服务与Luna识别进程将停止。",
    }


def _settlement_export_query():
    return select(QuarterlySettlement).options(
        selectinload(QuarterlySettlement.client).selectinload(Client.company),
        selectinload(QuarterlySettlement.client).selectinload(Client.fc),
        selectinload(QuarterlySettlement.company),
        selectinload(QuarterlySettlement.fc),
        selectinload(QuarterlySettlement.platform),
        selectinload(QuarterlySettlement.fee_plan),
        selectinload(QuarterlySettlement.account_lines).selectinload(SettlementAccountLine.account),
    )


@router.get("/system-info")
def system_info() -> dict:
    settings = get_settings()
    return {
        "app_name": settings.app_name,
        "data_root": str(settings.data_root),
        "database_path": str(settings.database_path),
        "template_path": str(settings.template_path),
        "template_exists": settings.template_path.exists(),
        "local_only": settings.host == "127.0.0.1",
    }


@router.get("/dashboard")
def dashboard(
    year: int | None = None,
    quarter: int | None = None,
    db: Session = Depends(get_db),
) -> dict:
    year = year or date.today().year
    query = select(QuarterlySettlement).where(
        QuarterlySettlement.status == "FINALIZED",
        QuarterlySettlement.year == year,
    )
    if quarter:
        query = query.where(QuarterlySettlement.quarter == quarter)
    settlements = db.scalars(query).all()
    invoice_query = (
        select(Invoice)
        .options(selectinload(Invoice.payments))
        .join(QuarterlySettlement, QuarterlySettlement.id == Invoice.settlement_id)
        .where(
            Invoice.lifecycle_status == "ISSUED",
            QuarterlySettlement.year == year,
        )
    )
    if quarter:
        invoice_query = invoice_query.where(QuarterlySettlement.quarter == quarter)
    invoices = db.scalars(invoice_query).all()
    generated_fee = sum(item.service_fee_cents for item in settlements)
    paid = sum(sum(payment.amount_cents for payment in invoice.payments) for invoice in invoices)
    outstanding = sum(max(invoice.amount_cents - sum(p.amount_cents for p in invoice.payments), 0) for invoice in invoices)
    overdue = sum(1 for invoice in invoices if invoice_payment_status(invoice) == "OVERDUE")
    return {
        "active_clients": db.scalar(select(func.count()).select_from(Client).where(Client.status == "ACTIVE")) or 0,
        "active_accounts": db.scalar(
            select(func.count(SubAccount.id))
            .select_from(SubAccount)
            .join(Client, Client.id == SubAccount.client_id)
            .where(Client.status == "ACTIVE", SubAccount.status == "ACTIVE")
        ) or 0,
        "generated_service_fee": money_string(generated_fee),
        "paid_amount": money_string(paid),
        "outstanding_amount": money_string(outstanding),
        "overdue_invoices": overdue,
        "period": {"year": year, "quarter": quarter},
    }


@router.get("/reports/fc")
def fc_report(
    year: int | None = None,
    quarter: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    fcs = db.scalars(select(FC).order_by(FC.name)).all()
    result: list[dict] = []
    for fc in fcs:
        client_count = db.scalar(
            select(func.count(distinct(Client.id))).where(Client.fc_id == fc.id, Client.status == "ACTIVE")
        ) or 0
        settlement_query = (
            select(QuarterlySettlement)
            .where(QuarterlySettlement.fc_id == fc.id, QuarterlySettlement.status == "FINALIZED")
        )
        if year:
            settlement_query = settlement_query.where(QuarterlySettlement.year == year)
        if quarter:
            settlement_query = settlement_query.where(QuarterlySettlement.quarter == quarter)
        settlements = db.scalars(settlement_query).all()
        fee_generated = sum(item.service_fee_cents for item in settlements)
        invoice_query = (
            select(Invoice)
            .options(selectinload(Invoice.payments))
            .join(QuarterlySettlement, QuarterlySettlement.id == Invoice.settlement_id)
            .where(Invoice.fc_id == fc.id, Invoice.lifecycle_status == "ISSUED")
        )
        if year:
            invoice_query = invoice_query.where(QuarterlySettlement.year == year)
        if quarter:
            invoice_query = invoice_query.where(QuarterlySettlement.quarter == quarter)
        invoices = db.scalars(invoice_query).all()
        paid = sum(payment.amount_cents for invoice in invoices for payment in invoice.payments)
        invoiced = sum(invoice.amount_cents for invoice in invoices)
        result.append(
            {
                "fc_id": fc.id,
                "fc_name": fc.name,
                "fc_code": fc.code,
                "company_name": fc.company.name,
                "active_client_count": client_count,
                "service_fee_generated": money_string(fee_generated),
                "paid_amount": money_string(paid),
                "outstanding_amount": money_string(max(invoiced - paid, 0)),
            }
        )
    return result


@router.post("/exports/excel")
def export_excel(
    settlement_ids: str = Query(..., description="Comma-separated settlement IDs"),
    db: Session = Depends(get_db),
) -> FileResponse:
    try:
        ids = sorted({int(value) for value in settlement_ids.split(",") if value.strip()})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="settlement_ids格式不正确") from exc
    if not ids:
        raise HTTPException(status_code=400, detail="至少选择一个Settlement")
    settlements = db.scalars(
        _settlement_export_query()
        .where(QuarterlySettlement.id.in_(ids), QuarterlySettlement.status == "FINALIZED")
        .order_by(QuarterlySettlement.year, QuarterlySettlement.quarter, QuarterlySettlement.id)
    ).unique().all()
    if len(settlements) != len(ids):
        raise HTTPException(status_code=400, detail="只能导出存在且Finalized的Settlement")
    invoices = db.scalars(
        select(Invoice).where(Invoice.settlement_id.in_(ids), Invoice.lifecycle_status == "ISSUED")
    ).all()
    invoice_map = {invoice.settlement_id: invoice for invoice in invoices}
    settings = get_settings()
    filename = f"financial_settlements_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    output_path = settings.data_root / "output" / "excel" / filename
    try:
        export_settlements_to_template(
            settlements=settlements,
            template_path=settings.template_path,
            output_path=output_path,
            invoices_by_settlement=invoice_map,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    db.add(
        ExportRecord(
            export_type="EXCEL_INTERNAL",
            entity_type="SETTLEMENT_BATCH",
            entity_id=settlements[0].id,
            stored_path=str(output_path),
            sha256=file_sha256(output_path),
        )
    )
    db.commit()
    return FileResponse(
        output_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@router.post("/exports/pdf")
def export_settlement_pdf(
    settlement_id: int,
    language: str = Query(default="zh", pattern="^(zh|en)$"),
    db: Session = Depends(get_db),
) -> FileResponse:
    settlement = db.scalar(
        _settlement_export_query().where(
            QuarterlySettlement.id == settlement_id,
            QuarterlySettlement.status == "FINALIZED",
        )
    )
    if not settlement:
        raise HTTPException(status_code=404, detail="Finalized Settlement不存在")
    settings = get_settings()
    filename = f"settlement_{settlement.id}_{language}.pdf"
    output_path = settings.data_root / "output" / "pdf" / filename
    generate_settlement_pdf(settlement=settlement, output_path=output_path, language=language)
    db.add(
        ExportRecord(
            export_type="PDF_SETTLEMENT",
            entity_type="SETTLEMENT",
            entity_id=settlement.id,
            stored_path=str(output_path),
            sha256=file_sha256(output_path),
            language=language,
        )
    )
    db.commit()
    return FileResponse(output_path, media_type="application/pdf", filename=filename)


@router.post("/backups")
def make_backup() -> FileResponse:
    path = create_backup()
    return FileResponse(path, media_type="application/zip", filename=path.name)


@router.post("/backups/restore")
async def restore_backup(file: UploadFile = File(...)) -> dict:
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(status_code=415, detail="只支持系统生成的ZIP备份")
    data = await file.read(2 * 1024 * 1024 * 1024)
    settings = get_settings()
    backup_path, _ = store_bytes(
        data=data,
        original_name=file.filename or "restore.zip",
        directory=settings.data_root / "tmp",
        prefix="restore_",
    )
    try:
        marker = stage_restore(backup_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"staged": True, "restart_required": True, "marker": str(marker)}
