from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .money import money_string
from .models import Attachment, BalanceSnapshot, Invoice, QuarterlySettlement


def _optional_money(value: int | None) -> str | None:
    return None if value is None else money_string(value)


def _snapshot_evidence_count(db: Session | None, snapshot_id: int | None) -> int | None:
    if db is None or snapshot_id is None:
        return None
    snapshot = db.get(BalanceSnapshot, snapshot_id)
    if not snapshot:
        return 0
    attachment_count = db.scalar(
        select(func.count(Attachment.id)).where(
            Attachment.entity_type == "SNAPSHOT", Attachment.entity_id == snapshot_id
        )
    ) or 0
    return int(attachment_count) + (1 if snapshot.statement_import_id is not None else 0)


def settlement_dict(item: QuarterlySettlement, *, db: Session | None = None) -> dict:
    return {
        "id": item.id,
        "client_id": item.client_id,
        "client_name": item.client.name if item.client else None,
        "company_id": item.company_id,
        "company_name": item.company.name if item.company else None,
        "fc_id": item.fc_id,
        "fc_name": item.fc.name if item.fc else None,
        "previous_settlement_id": item.previous_settlement_id,
        "platform_id": item.platform_id,
        "platform_name": item.platform.name if item.platform else None,
        "fee_plan_id": item.fee_plan_id,
        "fee_plan_name": item.fee_plan.name if item.fee_plan else None,
        "year": item.year,
        "quarter": item.quarter,
        "start_date": item.start_date.isoformat(),
        "closing_date": item.closing_date.isoformat(),
        "days": item.days,
        "beginning": money_string(item.beginning_cents),
        "contribution": money_string(item.contribution_cents),
        "withdrawal": money_string(item.withdrawal_cents),
        "net_contribution": money_string(item.net_contribution_cents),
        "closing": money_string(item.closing_cents),
        "gain_loss": money_string(item.gain_loss_cents),
        "period_rate": None if item.period_rate_ppm is None else item.period_rate_ppm / 1_000_000,
        "original_hwm": money_string(item.original_hwm_cents),
        "adjusted_hwm": money_string(item.adjusted_hwm_cents),
        "watermark_difference": money_string(item.watermark_difference_cents),
        "chargeable_above_hwm": money_string(item.chargeable_above_hwm_cents),
        "service_fee": money_string(item.service_fee_cents),
        "next_hwm": money_string(item.next_hwm_cents),
        "fee_rate": item.fee_rate_bps / 10_000,
        "formula_version": item.formula_version,
        "calculation_mode": item.calculation_mode,
        "status": item.status,
        "account_lines": [
            {
                "id": line.id,
                "account_id": line.account_id,
                "account_number": line.account.account_number if line.account else None,
                "previous_line_id": line.previous_line_id,
                "start_date": line.start_date.isoformat(),
                "closing_date": line.closing_date.isoformat(),
                "days": line.days,
                "beginning_snapshot_id": line.beginning_snapshot_id,
                "beginning": money_string(line.beginning_cents),
                "contribution": _optional_money(line.contribution_cents),
                "withdrawal": _optional_money(line.withdrawal_cents),
                "net_contribution": _optional_money(line.net_contribution_cents),
                "closing": money_string(line.closing_cents),
                "gain_loss": _optional_money(line.gain_loss_cents),
                "period_rate": None if line.period_rate_ppm is None else line.period_rate_ppm / 1_000_000,
                "original_hwm": _optional_money(line.original_hwm_cents),
                "adjusted_hwm": _optional_money(line.adjusted_hwm_cents),
                "watermark_difference": _optional_money(line.watermark_difference_cents),
                "chargeable_above_hwm": _optional_money(line.chargeable_above_hwm_cents),
                "service_fee": _optional_money(line.service_fee_cents),
                "next_hwm": _optional_money(line.next_hwm_cents),
                "closing_snapshot_id": line.closing_snapshot_id,
                "beginning_evidence_count": _snapshot_evidence_count(db, line.beginning_snapshot_id),
                "closing_evidence_count": _snapshot_evidence_count(db, line.closing_snapshot_id),
                "remark": line.remark,
            }
            for line in item.account_lines
        ],
    }


def invoice_payment_status(item: Invoice, today: date | None = None) -> str:
    today = today or date.today()
    paid_cents = sum(payment.amount_cents for payment in item.payments)
    if paid_cents >= item.amount_cents:
        return "PAID"
    if paid_cents > 0:
        return "PARTIALLY_PAID"
    if item.lifecycle_status == "ISSUED" and item.due_date and item.due_date < today:
        return "OVERDUE"
    return "UNPAID"


def invoice_dict(item: Invoice) -> dict:
    paid_cents = sum(payment.amount_cents for payment in item.payments)
    return {
        "id": item.id,
        "settlement_id": item.settlement_id,
        "invoice_number": item.invoice_number,
        "lifecycle_status": item.lifecycle_status,
        "payment_status": invoice_payment_status(item),
        "issue_date": item.issue_date.isoformat() if item.issue_date else None,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "amount": money_string(item.amount_cents),
        "paid_amount": money_string(paid_cents),
        "outstanding_amount": money_string(max(item.amount_cents - paid_cents, 0)),
        "language": item.language,
        "company_name": item.company.name if item.company else None,
        "fc_name": item.fc.name if item.fc else None,
        "client_name": item.settlement.client.name if item.settlement and item.settlement.client else None,
        "void_reason": item.void_reason,
        "payments": [
            {
                "id": payment.id,
                "payment_date": payment.payment_date.isoformat(),
                "amount": money_string(payment.amount_cents),
                "method": payment.method,
                "remark": payment.remark,
            }
            for payment in item.payments
        ],
    }
