from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import (
    AuditEvent,
    BalanceSnapshot,
    InvoiceSource,
    QuarterlySettlement,
    SettlementAccountLine,
    TransactionRecord,
)
from ..money import to_cents
from ..schemas import SettlementCalculateRequest, VoidRequest
from ..serializers import settlement_dict
from ..services.evidence_integrity import (
    snapshot_evidence_problem,
    transaction_evidence_problem,
)
from ..services.settlement_current import (
    ACCOUNT_HWM_MODE,
    SettlementCurrentStateError,
    SettlementLineSpec,
    calculate_current_settlement,
    current_state_differences,
    later_non_void_for_accounts,
)


router = APIRouter(prefix="/api/settlements", tags=["settlements"])


def _commit_state_change(db: Session, detail: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=detail) from exc
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_busy(exc):
            raise HTTPException(status_code=409, detail="数据库正在处理另一笔财务写入，请稍后重试") from exc
        raise


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


def _loaded_query():
    return select(QuarterlySettlement).options(
        selectinload(QuarterlySettlement.client),
        selectinload(QuarterlySettlement.platform),
        selectinload(QuarterlySettlement.fee_plan),
        selectinload(QuarterlySettlement.company),
        selectinload(QuarterlySettlement.fc),
        selectinload(QuarterlySettlement.previous_settlement),
        selectinload(QuarterlySettlement.replaces_settlement),
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


@router.delete("/{settlement_id}")
def delete_draft_settlement(settlement_id: int, db: Session = Depends(get_db)) -> dict:
    item = db.get(QuarterlySettlement, settlement_id)
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    if item.status != "DRAFT":
        raise HTTPException(status_code=409, detail="只有Draft Settlement可以删除")

    audit_details = {
        "deleted_settlement_id": item.id,
        "client_id": item.client_id,
        "platform_id": item.platform_id,
        "fee_plan_id": item.fee_plan_id,
        "year": item.year,
        "quarter": item.quarter,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
    db.add(
        AuditEvent(
            action="SETTLEMENT_DRAFT_DELETED",
            entity_type="SETTLEMENT",
            # SQLite may reuse a deleted maximum ROWID.  Keeping the old id in
            # details avoids falsely attaching this audit event to a future row.
            entity_id=None,
            details_json=audit_details,
        )
    )
    db.delete(item)
    _commit_state_change(db, "Settlement状态已改变或已被下游资料引用，不能删除")
    return {"status": "deleted", "id": settlement_id}


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


@router.post("/calculate")
def calculate_or_update_settlement(
    payload: SettlementCalculateRequest,
    db: Session = Depends(get_db),
) -> dict:
    # Version allocation and active-row selection are one serialized decision.
    # Otherwise concurrent requests could both try to replace the same VOID row.
    _begin_immediate(db)
    natural_key = (
        QuarterlySettlement.client_id == payload.client_id,
        QuarterlySettlement.platform_id == payload.platform_id,
        QuarterlySettlement.fee_plan_id == payload.fee_plan_id,
        QuarterlySettlement.year == payload.year,
        QuarterlySettlement.quarter == payload.quarter,
    )
    existing = db.scalar(
        select(QuarterlySettlement).where(*natural_key, QuarterlySettlement.status != "VOID")
    )
    latest = db.scalar(
        select(QuarterlySettlement)
        .where(*natural_key)
        .order_by(QuarterlySettlement.version_no.desc(), QuarterlySettlement.id.desc())
        .limit(1)
    )
    if existing and existing.status != "DRAFT":
        raise HTTPException(status_code=409, detail="已Finalized的Settlement不能直接重算")

    line_specs = [
        SettlementLineSpec(
            account_id=line.account_id,
            start_date=line.start_date,
            closing_date=line.closing_date,
            beginning_snapshot_id=line.beginning_snapshot_id,
            closing_snapshot_id=line.closing_snapshot_id,
            original_hwm_cents=to_cents(line.original_hwm) if line.original_hwm is not None else None,
            remark=line.remark,
        )
        for line in payload.account_lines
    ]
    try:
        current = calculate_current_settlement(
            db,
            client_id=payload.client_id,
            platform_id=payload.platform_id,
            fee_plan_id=payload.fee_plan_id,
            year=payload.year,
            quarter=payload.quarter,
            default_start_date=payload.start_date,
            default_closing_date=payload.closing_date,
            line_specs=line_specs,
            exclude_settlement_id=existing.id if existing else None,
        )
    except SettlementCurrentStateError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

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
            version_no=(latest.version_no + 1) if latest else 1,
            replaces_settlement_id=latest.id if latest and latest.status == "VOID" else None,
        )
        db.add(item)
    for key, value in current.aggregate.to_dict().items():
        setattr(item, key, value)
    item.calculation_mode = ACCOUNT_HWM_MODE
    item.company_id = current.company_id
    item.fc_id = current.fc_id
    item.previous_settlement_id = current.previous_settlement_id
    item.status = "DRAFT"
    item.finalized_at = None
    item.void_reason = None

    for current_line in current.lines:
        result = current_line.calculation
        item.account_lines.append(
            SettlementAccountLine(
                account_id=current_line.spec.account_id,
                previous_line_id=current_line.previous_line_id,
                beginning_snapshot_id=current_line.beginning_snapshot_id,
                closing_snapshot_id=current_line.closing_snapshot_id,
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
                remark=current_line.spec.remark,
            )
        )
    _commit_state_change(db, "Settlement账户HWM期间顺序或唯一性已被并发请求改变，请刷新后重试")
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == item.id))
    return settlement_dict(item, db=db)


def _missing_evidence(db: Session, item: QuarterlySettlement) -> list[str]:
    from ..services.settlement_period import cash_flow_conditions

    missing: list[str] = []
    for line in item.account_lines:
        beginning = db.get(BalanceSnapshot, line.beginning_snapshot_id) if line.beginning_snapshot_id else None
        closing = db.get(BalanceSnapshot, line.closing_snapshot_id) if line.closing_snapshot_id else None
        label = line.account.account_number if line.account else f"#{line.account_id}"
        beginning_problem = "余额快照不存在" if not beginning else snapshot_evidence_problem(db, beginning)
        closing_problem = "余额快照不存在" if not closing else snapshot_evidence_problem(db, closing)
        if beginning_problem:
            missing.append(f"{label} Beginning Snapshot（{beginning_problem}）")
        if closing_problem:
            missing.append(f"{label} Closing Snapshot（{closing_problem}）")
        if beginning is None:
            continue
        transactions = db.scalars(
            select(TransactionRecord).where(
                *cash_flow_conditions(line.account_id, beginning.as_of_date, line.closing_date),
            )
        ).all()
        for transaction in transactions:
            problem = transaction_evidence_problem(db, transaction)
            if problem:
                missing.append(
                    f"{label} {transaction.transaction_date.isoformat()} "
                    f"{transaction.transaction_type}（{problem}）"
                )
    return missing


@router.post("/{settlement_id}/finalize")
def finalize_settlement(settlement_id: int, db: Session = Depends(get_db)) -> dict:
    # This must remain the first database statement.  It serializes the full
    # read/recalculate/compare/finalize decision rather than only the final UPDATE.
    _begin_immediate(db)
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    if item.status != "DRAFT":
        raise HTTPException(status_code=409, detail="只有Draft Settlement可以Finalized")
    if not item.account_lines:
        raise HTTPException(status_code=400, detail="Settlement缺少账户明细")
    if item.calculation_mode != ACCOUNT_HWM_MODE:
        raise HTTPException(
            status_code=409,
            detail="旧版LEGACY Draft不能直接锁定，请先重新Calculate为当前Sub Account独立HWM口径",
        )

    replaced = item.replaces_settlement
    replacement_is_valid = (
        item.version_no == 1
        and item.replaces_settlement_id is None
    ) or (
        item.version_no > 1
        and replaced is not None
        and replaced.status == "VOID"
        and replaced.client_id == item.client_id
        and replaced.platform_id == item.platform_id
        and replaced.fee_plan_id == item.fee_plan_id
        and replaced.year == item.year
        and replaced.quarter == item.quarter
        and replaced.version_no + 1 == item.version_no
    )
    if not replacement_is_valid:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Settlement替代关系已失效，请删除该Draft并重新Calculate",
        )

    line_specs = [
        SettlementLineSpec(
            account_id=line.account_id,
            start_date=line.start_date,
            closing_date=line.closing_date,
            beginning_snapshot_id=line.beginning_snapshot_id,
            closing_snapshot_id=line.closing_snapshot_id,
            original_hwm_cents=line.original_hwm_cents,
            remark=line.remark,
        )
        for line in item.account_lines
    ]
    try:
        current = calculate_current_settlement(
            db,
            client_id=item.client_id,
            platform_id=item.platform_id,
            fee_plan_id=item.fee_plan_id,
            year=item.year,
            quarter=item.quarter,
            default_start_date=item.start_date,
            default_closing_date=item.closing_date,
            line_specs=line_specs,
            exclude_settlement_id=item.id,
        )
    except SettlementCurrentStateError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Draft所依据的当前资料已变化，请重新Calculate：{exc.detail}",
        ) from exc

    differences = current_state_differences(item, current)
    if differences:
        preview = "、".join(differences[:8])
        suffix = "等" if len(differences) > 8 else ""
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Draft与当前数据库重算结果不一致，请重新Calculate：{preview}{suffix}",
        )
    zero_denominator_lines = [
        line.account_number for line in current.lines if line.calculation.period_rate_ppm is None
    ]
    if zero_denominator_lines:
        raise HTTPException(
            status_code=400,
            detail=f"以下Sub Account的Period Rate分母为0，不能Finalized或出具账单：{'、'.join(zero_denominator_lines)}",
        )

    missing = _missing_evidence(db, item)
    if missing:
        preview = "、".join(missing[:5])
        suffix = "等" if len(missing) > 5 else ""
        raise HTTPException(
            status_code=400,
            detail=f"以下项目缺少凭证或凭证文件不完整，Draft可保存但不能Finalized：{preview}{suffix}",
        )

    item.status = "FINALIZED"
    item.finalized_at = datetime.now(timezone.utc)
    db.add(AuditEvent(action="SETTLEMENT_FINALIZED", entity_type="SETTLEMENT", entity_id=item.id))
    _commit_state_change(db, "Settlement账户HWM、凭证或下游状态已被并发请求改变，请重新Calculate")
    return settlement_dict(item, db=db)


@router.post("/{settlement_id}/void")
def void_settlement(settlement_id: int, payload: VoidRequest, db: Session = Depends(get_db)) -> dict:
    _begin_immediate(db)
    item = db.scalar(_loaded_query().where(QuarterlySettlement.id == settlement_id))
    if not item:
        raise HTTPException(status_code=404, detail="Settlement不存在")
    if item.status != "FINALIZED":
        if item.status == "VOID":
            raise HTTPException(status_code=409, detail="Settlement已经作废")
        raise HTTPException(status_code=409, detail="只有Finalized Settlement可以作废")
    active_invoice = db.scalar(
        select(InvoiceSource.invoice_id).where(
            InvoiceSource.settlement_id == item.id,
            InvoiceSource.active.is_(True),
        )
    )
    if active_invoice is not None:
        raise HTTPException(status_code=409, detail="请先作废关联的Draft或Issued Invoice")
    if item.calculation_mode == ACCOUNT_HWM_MODE:
        later = later_non_void_for_accounts(
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
