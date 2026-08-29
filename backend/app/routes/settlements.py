from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import (
    Attachment,
    AuditEvent,
    BalanceSnapshot,
    Client,
    FeePlan,
    InvoiceSource,
    Platform,
    QuarterlySettlement,
    SettlementAccountLine,
    SubAccount,
    TransactionRecord,
)
from ..money import to_cents
from ..schemas import SettlementAccountInput, SettlementCalculateRequest, VoidRequest
from ..serializers import settlement_dict
from ..services.calculation import (
    SettlementCalculation,
    aggregate_account_settlements,
    calculate_account_settlement,
    quarter_dates,
)


router = APIRouter(prefix="/api/settlements", tags=["settlements"])
ACCOUNT_HWM_MODE = "ACCOUNT_HWM"


def _commit_state_change(db: Session, detail: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=detail) from exc


def _loaded_query():
    return select(QuarterlySettlement).options(
        selectinload(QuarterlySettlement.client),
        selectinload(QuarterlySettlement.platform),
        selectinload(QuarterlySettlement.fee_plan),
        selectinload(QuarterlySettlement.company),
        selectinload(QuarterlySettlement.fc),
        selectinload(QuarterlySettlement.previous_settlement),
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
    return [settlement_dict(item, db=db) for item in db.scalars(query).unique().all()]


@router.get("/{settlement_id}")
def get_settlement(settlement_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    return settlement_dict(item, db=db)


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


def _previous_finalized_line(
    db: Session, *, account_id: int, year: int, quarter: int
) -> SettlementAccountLine | None:
    period_key = year * 4 + quarter
    return db.scalar(
        select(SettlementAccountLine)
        .join(QuarterlySettlement, QuarterlySettlement.id == SettlementAccountLine.settlement_id)
        .where(
            SettlementAccountLine.account_id == account_id,
            QuarterlySettlement.calculation_mode == ACCOUNT_HWM_MODE,
            QuarterlySettlement.status == "FINALIZED",
            (QuarterlySettlement.year * 4 + QuarterlySettlement.quarter) < period_key,
        )
        .order_by(QuarterlySettlement.year.desc(), QuarterlySettlement.quarter.desc())
        .limit(1)
    )


def _later_non_void(db: Session, item: QuarterlySettlement) -> QuarterlySettlement | None:
    period_key = item.year * 4 + item.quarter
    candidates = db.scalars(
        select(QuarterlySettlement)
        .where(
            QuarterlySettlement.client_id == item.client_id,
            QuarterlySettlement.platform_id == item.platform_id,
            QuarterlySettlement.fee_plan_id == item.fee_plan_id,
            QuarterlySettlement.status != "VOID",
        )
        .order_by(QuarterlySettlement.year, QuarterlySettlement.quarter)
    ).all()
    return next((candidate for candidate in candidates if candidate.year * 4 + candidate.quarter > period_key), None)


def _later_for_accounts(
    db: Session, *, account_ids: list[int], year: int, quarter: int, exclude_id: int | None = None
) -> QuarterlySettlement | None:
    if not account_ids:
        return None
    period_key = year * 4 + quarter
    query = (
        select(QuarterlySettlement)
        .join(SettlementAccountLine, SettlementAccountLine.settlement_id == QuarterlySettlement.id)
        .where(
            SettlementAccountLine.account_id.in_(account_ids),
            QuarterlySettlement.status != "VOID",
            (QuarterlySettlement.year * 4 + QuarterlySettlement.quarter) > period_key,
        )
        .order_by(QuarterlySettlement.year, QuarterlySettlement.quarter)
        .limit(1)
    )
    if exclude_id is not None:
        query = query.where(QuarterlySettlement.id != exclude_id)
    return db.scalar(query)


def _snapshot_has_evidence(db: Session, snapshot: BalanceSnapshot) -> bool:
    if snapshot.statement_import_id is not None:
        return True
    return db.scalar(
        select(Attachment.id).where(
            Attachment.entity_type == "SNAPSHOT", Attachment.entity_id == snapshot.id
        ).limit(1)
    ) is not None


def _transaction_has_evidence(db: Session, transaction: TransactionRecord) -> bool:
    if transaction.attachment_id is not None:
        return True
    return db.scalar(
        select(Attachment.id).where(
            Attachment.entity_type == "TRANSACTION", Attachment.entity_id == transaction.id
        ).limit(1)
    ) is not None


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
    if client.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="只有Active Client可以建立季度Settlement")
    if client.company_id and fee_plan.company_id != client.company_id:
        raise HTTPException(status_code=400, detail="Fee Plan与Client所属Company不一致")

    natural_start, natural_end = quarter_dates(payload.year, payload.quarter)
    start_date = payload.start_date or natural_start
    closing_date = payload.closing_date or natural_end
    if start_date < natural_start or start_date > natural_end:
        raise HTTPException(status_code=400, detail="Starting Date必须位于所选季度内")
    if closing_date < start_date or closing_date > natural_end:
        raise HTTPException(status_code=400, detail="Closing Date必须位于所选季度且不早于Starting Date")

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
    inactive_accounts = [
        account.account_number
        for account in accounts.values()
        if account.status != "ACTIVE"
    ]
    if inactive_accounts:
        raise HTTPException(
            status_code=409,
            detail=f"以下Sub Account不是Active，不能建立季度Settlement：{'、'.join(inactive_accounts)}",
        )
    missing_start_dates = [
        account.account_number
        for account in accounts.values()
        if account.start_date is None
    ]
    if missing_start_dates:
        raise HTTPException(
            status_code=409,
            detail=f"以下Sub Account缺少开始管理日期，不能建立季度Settlement：{'、'.join(missing_start_dates)}",
        )

    later = _later_for_accounts(
        db,
        account_ids=account_ids,
        year=payload.year,
        quarter=payload.quarter,
        exclude_id=existing.id if existing else None,
    )
    if later:
        raise HTTPException(
            status_code=409,
            detail=f"存在后续Settlement #{later.id}（{later.year} Q{later.quarter}），请先按时间倒序作废后再补算",
        )

    line_results: list[
        tuple[SettlementAccountInput, SettlementCalculation, int, int, int | None]
    ] = []
    for line in payload.account_lines:
        line_start_date = line.start_date or start_date
        line_closing_date = line.closing_date or closing_date
        if line_start_date < natural_start or line_start_date > natural_end:
            raise HTTPException(status_code=400, detail="账户Starting Date必须位于所选季度内")
        if line_closing_date < line_start_date or line_closing_date > natural_end:
            raise HTTPException(
                status_code=400, detail="账户Closing Date必须位于所选季度且不早于Starting Date"
            )
        account = accounts[line.account_id]
        if account.start_date and line_start_date < account.start_date:
            raise HTTPException(status_code=400, detail="账户Starting Date不能早于Sub Account开始管理日期")
        if account.end_date and line_closing_date > account.end_date:
            raise HTTPException(status_code=400, detail="账户Closing Date不能晚于Sub Account结束管理日期")

        closing_snapshot = db.get(BalanceSnapshot, line.closing_snapshot_id)
        if not closing_snapshot or closing_snapshot.account_id != line.account_id:
            raise HTTPException(status_code=400, detail="Closing Snapshot与Sub Account不匹配")
        if closing_snapshot.as_of_date != line_closing_date:
            raise HTTPException(status_code=400, detail="Closing Snapshot日期必须等于该账户Closing Date")
        if not closing_snapshot.eligible_for_closing:
            raise HTTPException(status_code=400, detail="该余额快照不是季末或退出日，不能作为Closing")

        previous_line = _previous_finalized_line(
            db, account_id=line.account_id, year=payload.year, quarter=payload.quarter
        )
        if previous_line:
            if previous_line.closing_snapshot_id is None or previous_line.next_hwm_cents is None:
                raise HTTPException(status_code=409, detail="前序账户结算缺少Closing Snapshot或Next HWM")
            if line.beginning_snapshot_id not in (None, previous_line.closing_snapshot_id):
                raise HTTPException(status_code=400, detail="Beginning必须自动关联前序Closing Snapshot")
            if line.original_hwm is not None and to_cents(line.original_hwm) != previous_line.next_hwm_cents:
                raise HTTPException(status_code=400, detail="Original HWM必须自动继承前序账户Next HWM")
            beginning_snapshot_id = previous_line.closing_snapshot_id
            beginning_cents = previous_line.closing_cents
            original_hwm_cents = previous_line.next_hwm_cents
        else:
            if line.beginning_snapshot_id is None:
                raise HTTPException(status_code=400, detail="首次账户结算必须选择明确的Beginning Snapshot")
            beginning_snapshot = db.get(BalanceSnapshot, line.beginning_snapshot_id)
            if not beginning_snapshot or beginning_snapshot.account_id != line.account_id:
                raise HTTPException(status_code=400, detail="Beginning Snapshot与Sub Account不匹配")
            if beginning_snapshot.as_of_date != line_start_date:
                raise HTTPException(status_code=400, detail="首次Beginning Snapshot日期必须等于该账户Starting Date")
            if line.original_hwm is None:
                raise HTTPException(status_code=400, detail="首次账户结算必须输入该Sub Account的Original HWM")
            beginning_snapshot_id = beginning_snapshot.id
            beginning_cents = beginning_snapshot.total_balance_cents
            original_hwm_cents = to_cents(line.original_hwm)

        contribution_cents = db.scalar(
            select(func.coalesce(func.sum(TransactionRecord.amount_cents), 0)).where(
                TransactionRecord.account_id == line.account_id,
                TransactionRecord.transaction_type == "CONTRIBUTION",
                TransactionRecord.transaction_date > line_start_date,
                TransactionRecord.transaction_date <= line_closing_date,
            )
        ) or 0
        withdrawal_cents = db.scalar(
            select(func.coalesce(func.sum(TransactionRecord.amount_cents), 0)).where(
                TransactionRecord.account_id == line.account_id,
                TransactionRecord.transaction_type == "WITHDRAWAL",
                TransactionRecord.transaction_date > line_start_date,
                TransactionRecord.transaction_date <= line_closing_date,
            )
        ) or 0
        try:
            result = calculate_account_settlement(
                start_date=line_start_date,
                closing_date=line_closing_date,
                beginning_cents=beginning_cents,
                closing_cents=closing_snapshot.total_balance_cents,
                contribution_cents=int(contribution_cents),
                withdrawal_cents=int(withdrawal_cents),
                original_hwm_cents=original_hwm_cents,
                fee_rate_bps=fee_plan.fee_rate_bps,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        line_results.append(
            (
                line,
                result,
                beginning_snapshot_id,
                closing_snapshot.id,
                previous_line.id if previous_line else None,
            )
        )

    aggregate_start_date = min(entry[1].start_date for entry in line_results)
    aggregate_closing_date = max(entry[1].closing_date for entry in line_results)
    aggregate = aggregate_account_settlements(
        start_date=aggregate_start_date,
        closing_date=aggregate_closing_date,
        calculations=[entry[1] for entry in line_results],
    )
    previous_group = _previous_finalized(
        db,
        client_id=payload.client_id,
        platform_id=payload.platform_id,
        fee_plan_id=payload.fee_plan_id,
        year=payload.year,
        quarter=payload.quarter,
    )

    if existing:
        item = existing
        item.account_lines.clear()
        db.flush()
    else:
        item = QuarterlySettlement(
            client_id=payload.client_id,
            platform_id=payload.platform_id,
            fee_plan_id=payload.fee_plan_id,
            year=payload.year,
            quarter=payload.quarter,
        )
        db.add(item)
    for key, value in aggregate.to_dict().items():
        setattr(item, key, value)
    item.calculation_mode = ACCOUNT_HWM_MODE
    item.previous_settlement_id = previous_group.id if previous_group else None
    item.status = "DRAFT"

    for input_line, result, beginning_snapshot_id, closing_snapshot_id, previous_line_id in line_results:
        item.account_lines.append(
            SettlementAccountLine(
                account_id=input_line.account_id,
                previous_line_id=previous_line_id,
                beginning_snapshot_id=beginning_snapshot_id,
                closing_snapshot_id=closing_snapshot_id,
                start_date=result.start_date,
                closing_date=result.closing_date,
                days=result.days,
                beginning_cents=result.beginning_cents,
                closing_cents=result.closing_cents,
                contribution_cents=result.contribution_cents,
                withdrawal_cents=result.withdrawal_cents,
                net_contribution_cents=result.net_contribution_cents,
                gain_loss_cents=result.gain_loss_cents,
                period_rate_ppm=result.period_rate_ppm,
                original_hwm_cents=result.original_hwm_cents,
                adjusted_hwm_cents=result.adjusted_hwm_cents,
                watermark_difference_cents=result.watermark_difference_cents,
                chargeable_above_hwm_cents=result.chargeable_above_hwm_cents,
                service_fee_cents=result.service_fee_cents,
                next_hwm_cents=result.next_hwm_cents,
                fee_rate_bps=result.fee_rate_bps,
                formula_version=result.formula_version,
                remark=input_line.remark,
            )
        )
    _commit_state_change(db, "Settlement账户HWM期间顺序或唯一性已被并发请求改变，请刷新后重试")
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == item.id))
    return settlement_dict(item, db=db)


def _missing_evidence(db: Session, item: QuarterlySettlement) -> list[str]:
    missing: list[str] = []
    for line in item.account_lines:
        beginning = db.get(BalanceSnapshot, line.beginning_snapshot_id) if line.beginning_snapshot_id else None
        closing = db.get(BalanceSnapshot, line.closing_snapshot_id) if line.closing_snapshot_id else None
        label = line.account.account_number if line.account else f"#{line.account_id}"
        if not beginning or not _snapshot_has_evidence(db, beginning):
            missing.append(f"{label} Beginning Snapshot")
        if not closing or not _snapshot_has_evidence(db, closing):
            missing.append(f"{label} Closing Snapshot")
        transactions = db.scalars(
            select(TransactionRecord).where(
                TransactionRecord.account_id == line.account_id,
                TransactionRecord.transaction_date > line.start_date,
                TransactionRecord.transaction_date <= line.closing_date,
            )
        ).all()
        for transaction in transactions:
            if not _transaction_has_evidence(db, transaction):
                missing.append(f"{label} {transaction.transaction_date.isoformat()} {transaction.transaction_type}")
    return missing


@router.post("/{settlement_id}/finalize")
def finalize_settlement(settlement_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    if item.status != "DRAFT":
        raise HTTPException(status_code=409, detail="只有Draft Settlement可以Finalized")
    if not item.account_lines:
        raise HTTPException(status_code=400, detail="Settlement缺少账户明细")
    if item.calculation_mode == ACCOUNT_HWM_MODE:
        zero_denominator_lines = [
            line.account.account_number if line.account else f"#{line.account_id}"
            for line in item.account_lines
            if line.period_rate_ppm is None
        ]
        if zero_denominator_lines:
            raise HTTPException(
                status_code=400,
                detail=f"以下Sub Account的Period Rate分母为0，不能Finalized或出具账单：{'、'.join(zero_denominator_lines)}",
            )
    elif item.period_rate_ppm is None:
        raise HTTPException(status_code=400, detail="Period Rate分母为0，不能Finalized或出具账单")
    client = item.client
    if not client or client.status != "ACTIVE":
        raise HTTPException(status_code=409, detail="Client不是Active，不能Finalized Settlement")
    inactive_accounts = [
        line.account.account_number if line.account else f"#{line.account_id}"
        for line in item.account_lines
        if not line.account or line.account.status != "ACTIVE"
    ]
    if inactive_accounts:
        raise HTTPException(
            status_code=409,
            detail=f"以下Sub Account不是Active，不能Finalized Settlement：{'、'.join(inactive_accounts)}",
        )
    missing_start_dates = [
        line.account.account_number if line.account else f"#{line.account_id}"
        for line in item.account_lines
        if not line.account or line.account.start_date is None
    ]
    if missing_start_dates:
        raise HTTPException(
            status_code=409,
            detail=f"以下Sub Account缺少开始管理日期，不能Finalized Settlement：{'、'.join(missing_start_dates)}",
        )
    if not client.company_id or not client.fc_id:
        raise HTTPException(status_code=400, detail="Client必须补全Company和FC后才能Finalized")
    if not item.fee_plan or item.fee_plan.company_id != client.company_id:
        raise HTTPException(status_code=409, detail="Client Company已变化且与Fee Plan不一致，请重新Calculate")
    if not client.fc or client.fc.company_id != client.company_id:
        raise HTTPException(status_code=409, detail="Client FC不属于当前Company，请修正归属后重新Calculate")

    if item.calculation_mode == ACCOUNT_HWM_MODE:
        for line in item.account_lines:
            current_previous = _previous_finalized_line(
                db, account_id=line.account_id, year=item.year, quarter=item.quarter
            )
            current_previous_id = current_previous.id if current_previous else None
            if current_previous_id != line.previous_line_id:
                raise HTTPException(status_code=409, detail="账户前序Finalized Settlement已变化，请重新Calculate")
            if current_previous and current_previous.next_hwm_cents != line.original_hwm_cents:
                raise HTTPException(status_code=409, detail="账户Original HWM与当前前序结算不一致，请重新Calculate")
        later = _later_for_accounts(
            db,
            account_ids=[line.account_id for line in item.account_lines],
            year=item.year,
            quarter=item.quarter,
            exclude_id=item.id,
        )
        if later:
            raise HTTPException(status_code=409, detail=f"存在后续Settlement #{later.id}，不能倒序Finalized")
        missing = _missing_evidence(db, item)
        if missing:
            preview = "、".join(missing[:5])
            suffix = "等" if len(missing) > 5 else ""
            raise HTTPException(status_code=400, detail=f"以下项目缺少凭证，Draft可保存但不能Finalized：{preview}{suffix}")
    else:
        current_previous = _previous_finalized(
            db,
            client_id=item.client_id,
            platform_id=item.platform_id,
            fee_plan_id=item.fee_plan_id,
            year=item.year,
            quarter=item.quarter,
        )
        current_previous_id = current_previous.id if current_previous else None
        if current_previous_id != item.previous_settlement_id:
            raise HTTPException(status_code=409, detail="前序Finalized Settlement已变化，请重新Calculate后再锁定")
        if current_previous and current_previous.next_hwm_cents != item.original_hwm_cents:
            raise HTTPException(status_code=409, detail="Original HWM与当前前序Settlement不一致，请重新Calculate")
        later = _later_non_void(db, item)
        if later:
            raise HTTPException(status_code=409, detail=f"存在后续Settlement #{later.id}，不能倒序Finalized")

    item.company_id = client.company_id
    item.fc_id = client.fc_id
    item.status = "FINALIZED"
    item.finalized_at = datetime.now(timezone.utc)
    db.add(AuditEvent(action="SETTLEMENT_FINALIZED", entity_type="SETTLEMENT", entity_id=item.id))
    _commit_state_change(db, "Settlement账户HWM、凭证或下游状态已被并发请求改变，请重新Calculate")
    return settlement_dict(item, db=db)


@router.post("/{settlement_id}/void")
def void_settlement(settlement_id: int, payload: VoidRequest, db: Session = Depends(get_db)) -> dict:
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    if item.status == "VOID":
        raise HTTPException(status_code=409, detail="Settlement已经作废")
    active_invoice = db.scalar(
        select(InvoiceSource.invoice_id).where(
            InvoiceSource.settlement_id == item.id,
            InvoiceSource.active.is_(True),
        )
    )
    if active_invoice is not None:
        raise HTTPException(status_code=409, detail="请先作废关联的Draft或Issued Invoice")
    if item.calculation_mode == ACCOUNT_HWM_MODE:
        later = _later_for_accounts(
            db,
            account_ids=[line.account_id for line in item.account_lines],
            year=item.year,
            quarter=item.quarter,
            exclude_id=item.id,
        )
    else:
        later = _later_non_void(db, item)
    if later:
        raise HTTPException(status_code=409, detail=f"Settlement #{later.id} 依赖当前HWM，请先按时间倒序作废后续结算")
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
    _commit_state_change(db, "Settlement存在下游依赖或有效Invoice，不能作废")
    return settlement_dict(item, db=db)
