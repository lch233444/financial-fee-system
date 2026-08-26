from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import (
    AuditEvent,
    BalanceSnapshot,
    Client,
    FeePlan,
    Invoice,
    Platform,
    QuarterlySettlement,
    SettlementAccountLine,
    SubAccount,
    TransactionRecord,
)
from ..money import to_cents
from ..schemas import SettlementCalculateRequest, VoidRequest
from ..serializers import settlement_dict
from ..services.calculation import AccountPeriodInput, calculate_settlement, quarter_dates


router = APIRouter(prefix="/api/settlements", tags=["settlements"])


def _loaded_query():
    return select(QuarterlySettlement).options(
        selectinload(QuarterlySettlement.client),
        selectinload(QuarterlySettlement.platform),
        selectinload(QuarterlySettlement.fee_plan),
        selectinload(QuarterlySettlement.account_lines).selectinload(SettlementAccountLine.account),
    )


@router.get("")
def list_settlements(
    year: int | None = None,
    quarter: int | None = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    query = _loaded_query().order_by(QuarterlySettlement.year.desc(), QuarterlySettlement.quarter.desc())
    if year:
        query = query.where(QuarterlySettlement.year == year)
    if quarter:
        query = query.where(QuarterlySettlement.quarter == quarter)
    return [settlement_dict(item) for item in db.scalars(query).unique().all()]


@router.get("/{settlement_id}")
def get_settlement(settlement_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    return settlement_dict(item)


def _previous_finalized(
    db: Session,
    *,
    client_id: int,
    platform_id: int,
    fee_plan_id: int,
    year: int,
    quarter: int,
) -> QuarterlySettlement | None:
    period_key = year * 4 + quarter
    candidates = db.scalars(
        select(QuarterlySettlement)
        .where(
            QuarterlySettlement.client_id == client_id,
            QuarterlySettlement.platform_id == platform_id,
            QuarterlySettlement.fee_plan_id == fee_plan_id,
            QuarterlySettlement.status == "FINALIZED",
        )
        .order_by(QuarterlySettlement.year.desc(), QuarterlySettlement.quarter.desc())
    ).all()
    return next((item for item in candidates if item.year * 4 + item.quarter < period_key), None)


@router.post("/calculate")
def calculate_or_update_settlement(
    payload: SettlementCalculateRequest,
    db: Session = Depends(get_db),
) -> dict:
    client = db.get(Client, payload.client_id)
    platform = db.get(Platform, payload.platform_id)
    fee_plan = db.get(FeePlan, payload.fee_plan_id)
    if not client or not platform or not fee_plan:
        raise HTTPException(status_code=404, detail="Client、Platform或Fee Plan不存在")
    if client.company_id and fee_plan.company_id != client.company_id:
        raise HTTPException(status_code=400, detail="Fee Plan与Client所属Company不一致")

    natural_start, natural_end = quarter_dates(payload.year, payload.quarter)
    start_date = payload.start_date or natural_start
    closing_date = payload.closing_date or natural_end
    if start_date < natural_start or start_date > natural_end:
        raise HTTPException(status_code=400, detail="Starting Date必须位于所选季度内")
    if closing_date < start_date or closing_date > natural_end:
        raise HTTPException(status_code=400, detail="Closing Date必须位于所选季度且不早于Starting Date")

    account_ids = [line.account_id for line in payload.account_lines]
    if len(account_ids) != len(set(account_ids)):
        raise HTTPException(status_code=400, detail="同一Sub Account不能重复加入结算")
    accounts = {item.id: item for item in db.scalars(select(SubAccount).where(SubAccount.id.in_(account_ids))).all()}
    if len(accounts) != len(account_ids):
        raise HTTPException(status_code=404, detail="部分Sub Account不存在")
    for account in accounts.values():
        if (
            account.client_id != payload.client_id
            or account.platform_id != payload.platform_id
            or account.fee_plan_id != payload.fee_plan_id
        ):
            raise HTTPException(status_code=400, detail="所有Sub Account必须属于同一Client + Platform + Fee Plan")

    for line in payload.account_lines:
        if line.closing_snapshot_id:
            snapshot = db.get(BalanceSnapshot, line.closing_snapshot_id)
            if not snapshot or snapshot.account_id != line.account_id:
                raise HTTPException(status_code=400, detail="Closing Snapshot与Sub Account不匹配")
            if snapshot.as_of_date != closing_date:
                raise HTTPException(status_code=400, detail="Closing Snapshot日期必须等于Closing Date")
            if not snapshot.eligible_for_closing:
                raise HTTPException(status_code=400, detail="该余额快照不是季末或退出日，不能作为Closing")
            if to_cents(line.closing) != snapshot.total_balance_cents:
                raise HTTPException(status_code=400, detail="Closing金额与所选余额快照不一致")

    contribution_cents = db.scalar(
        select(func.coalesce(func.sum(TransactionRecord.amount_cents), 0)).where(
            TransactionRecord.account_id.in_(account_ids),
            TransactionRecord.transaction_type == "CONTRIBUTION",
            TransactionRecord.transaction_date > start_date,
            TransactionRecord.transaction_date <= closing_date,
        )
    ) or 0
    withdrawal_cents = db.scalar(
        select(func.coalesce(func.sum(TransactionRecord.amount_cents), 0)).where(
            TransactionRecord.account_id.in_(account_ids),
            TransactionRecord.transaction_type == "WITHDRAWAL",
            TransactionRecord.transaction_date > start_date,
            TransactionRecord.transaction_date <= closing_date,
        )
    ) or 0

    previous = _previous_finalized(
        db,
        client_id=payload.client_id,
        platform_id=payload.platform_id,
        fee_plan_id=payload.fee_plan_id,
        year=payload.year,
        quarter=payload.quarter,
    )
    if previous:
        original_hwm_cents = previous.next_hwm_cents
    elif payload.original_hwm is not None:
        original_hwm_cents = to_cents(payload.original_hwm)
    else:
        raise HTTPException(status_code=400, detail="首次结算必须由财务输入Original HWM")

    account_inputs = [
        AccountPeriodInput(
            account_id=line.account_id,
            beginning_cents=to_cents(line.beginning),
            closing_cents=to_cents(line.closing),
            closing_snapshot_id=line.closing_snapshot_id,
            remark=line.remark,
        )
        for line in payload.account_lines
    ]
    try:
        result = calculate_settlement(
            start_date=start_date,
            closing_date=closing_date,
            account_lines=account_inputs,
            contribution_cents=int(contribution_cents),
            withdrawal_cents=int(withdrawal_cents),
            original_hwm_cents=original_hwm_cents,
            fee_rate_bps=fee_plan.fee_rate_bps,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing = db.scalar(
        select(QuarterlySettlement).where(
            QuarterlySettlement.client_id == payload.client_id,
            QuarterlySettlement.platform_id == payload.platform_id,
            QuarterlySettlement.fee_plan_id == payload.fee_plan_id,
            QuarterlySettlement.year == payload.year,
            QuarterlySettlement.quarter == payload.quarter,
        )
    )
    if existing and existing.status != "DRAFT":
        raise HTTPException(status_code=409, detail="已Finalized或Void的Settlement不能直接重算")
    if existing:
        item = existing
        item.account_lines.clear()
    else:
        item = QuarterlySettlement(
            client_id=payload.client_id,
            platform_id=payload.platform_id,
            fee_plan_id=payload.fee_plan_id,
            year=payload.year,
            quarter=payload.quarter,
        )
        db.add(item)

    for key, value in result.to_dict().items():
        setattr(item, key, value)
    item.status = "DRAFT"
    for line in account_inputs:
        item.account_lines.append(
            SettlementAccountLine(
                account_id=line.account_id,
                beginning_cents=line.beginning_cents,
                closing_cents=line.closing_cents,
                closing_snapshot_id=line.closing_snapshot_id,
                remark=line.remark,
            )
        )
    db.commit()
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == item.id))
    return settlement_dict(item)


@router.post("/{settlement_id}/finalize")
def finalize_settlement(settlement_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    if item.status != "DRAFT":
        raise HTTPException(status_code=409, detail="只有Draft Settlement可以Finalized")
    if item.period_rate_ppm is None:
        raise HTTPException(status_code=400, detail="Period Rate分母为0，不能Finalized或出具账单")
    if not item.account_lines:
        raise HTTPException(status_code=400, detail="Settlement缺少账户明细")
    item.status = "FINALIZED"
    item.finalized_at = datetime.now(timezone.utc)
    db.add(AuditEvent(action="SETTLEMENT_FINALIZED", entity_type="SETTLEMENT", entity_id=item.id))
    db.commit()
    return settlement_dict(item)


@router.post("/{settlement_id}/void")
def void_settlement(settlement_id: int, payload: VoidRequest, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    issued_invoice_exists = db.scalar(
        select(Invoice.id).where(
            Invoice.settlement_id == item.id,
            Invoice.lifecycle_status == "ISSUED",
        )
    ) is not None
    if issued_invoice_exists:
        raise HTTPException(status_code=409, detail="请先作废关联的Issued Invoice")
    item.status = "VOID"
    item.void_reason = payload.reason
    db.add(
        AuditEvent(
            action="SETTLEMENT_VOIDED",
            entity_type="SETTLEMENT",
            entity_id=item.id,
            details_json={"reason": payload.reason},
        )
    )
    db.commit()
    return settlement_dict(item)
