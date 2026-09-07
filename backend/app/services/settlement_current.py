from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    BalanceSnapshot,
    Client,
    FC,
    FeePlan,
    Platform,
    QuarterlySettlement,
    SettlementAccountLine,
    SubAccount,
    TransactionRecord,
)
from .calculation import (
    SettlementCalculation,
    aggregate_account_settlements,
    calculate_account_settlement,
    quarter_dates,
)
from .settlement_period import cash_flow_conditions


ACCOUNT_HWM_MODE = "ACCOUNT_HWM"
RATE_SQL_SAFE_CENTS = 9_000_000_000_000


class SettlementCurrentStateError(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _valid_initial_beginning_date(
    *, snapshot_date: date, line_start_date: date, natural_quarter_start: date
) -> bool:
    """Accept an opening balance dated at the boundary it actually represents.

    Existing mid-quarter onboarding uses a same-day snapshot whose cash flows
    are already included in Beginning.  A normal calendar quarter instead
    opens from the previous quarter-end snapshot, so the first in-period cash
    flow is on the following day.
    """

    return snapshot_date == line_start_date or (
        line_start_date == natural_quarter_start
        and snapshot_date == line_start_date - timedelta(days=1)
    )


def _require_sql_safe_rate_inputs(
    *, beginning_cents: int, net_contribution_cents: int, gain_loss_cents: int, label: str
) -> None:
    if any(
        abs(value) > RATE_SQL_SAFE_CENTS
        for value in (beginning_cents, net_contribution_cents, gain_loss_cents)
    ):
        raise SettlementCurrentStateError(
            400,
            f"{label}的Beginning、Net Contribution或Gain/Loss超过"
            "SQLite精确Period Rate校验上限（HKD 90,000,000,000）",
        )


@dataclass(frozen=True)
class SettlementLineSpec:
    account_id: int
    start_date: date | None
    closing_date: date | None
    beginning_snapshot_id: int | None
    closing_snapshot_id: int | None
    original_hwm_cents: int | None
    remark: str | None = None


@dataclass(frozen=True)
class CurrentSettlementLine:
    spec: SettlementLineSpec
    account_number: str
    calculation: SettlementCalculation
    beginning_snapshot_id: int
    closing_snapshot_id: int
    previous_line_id: int | None


@dataclass(frozen=True)
class CurrentSettlement:
    client: Client
    platform: Platform
    fee_plan: FeePlan
    company_id: int
    fc_id: int
    previous_settlement_id: int | None
    aggregate: SettlementCalculation
    lines: tuple[CurrentSettlementLine, ...]


def previous_finalized_settlement(
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


def previous_finalized_line(
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


def later_non_void_for_accounts(
    db: Session,
    *,
    account_ids: list[int],
    year: int,
    quarter: int,
    exclude_id: int | None = None,
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


def same_period_non_void_for_accounts(
    db: Session,
    *,
    account_ids: list[int],
    year: int,
    quarter: int,
    exclude_id: int | None = None,
) -> QuarterlySettlement | None:
    if not account_ids:
        return None
    query = (
        select(QuarterlySettlement)
        .join(SettlementAccountLine, SettlementAccountLine.settlement_id == QuarterlySettlement.id)
        .where(
            SettlementAccountLine.account_id.in_(account_ids),
            QuarterlySettlement.year == year,
            QuarterlySettlement.quarter == quarter,
            QuarterlySettlement.status != "VOID",
        )
        .order_by(QuarterlySettlement.id)
        .limit(1)
    )
    if exclude_id is not None:
        query = query.where(QuarterlySettlement.id != exclude_id)
    return db.scalar(query)


def calculate_current_settlement(
    db: Session,
    *,
    client_id: int,
    platform_id: int,
    fee_plan_id: int,
    year: int,
    quarter: int,
    default_start_date: date | None,
    default_closing_date: date | None,
    line_specs: list[SettlementLineSpec],
    exclude_settlement_id: int | None = None,
) -> CurrentSettlement:
    """Calculate a settlement only from current, normalized database state.

    Both Calculate and Finalize call this function.  It deliberately owns all
    database-derived inputs (account ownership, snapshots, transactions, fee
    rate and prior HWM) so Finalize cannot trust stale values saved in a Draft.
    """

    client = db.get(Client, client_id)
    platform = db.get(Platform, platform_id)
    fee_plan = db.get(FeePlan, fee_plan_id)
    if not client or not platform or not fee_plan:
        raise SettlementCurrentStateError(404, "Client、Platform或Fee Plan不存在")
    if client.status != "ACTIVE":
        raise SettlementCurrentStateError(409, "只有Active Client可以建立季度Settlement")
    if not client.company_id or not client.fc_id:
        raise SettlementCurrentStateError(409, "Client必须补全Company和FC后再Calculate")
    if fee_plan.company_id != client.company_id:
        raise SettlementCurrentStateError(
            409, "Fee Plan不一致：与Client所属Company不一致，请修正后重新Calculate"
        )
    fc = db.get(FC, client.fc_id)
    if not fc or fc.company_id != client.company_id:
        raise SettlementCurrentStateError(409, "Client FC不属于当前Company，请修正归属后重新Calculate")

    natural_start, natural_end = quarter_dates(year, quarter)
    start_date = default_start_date or natural_start
    closing_date = default_closing_date or natural_end
    if start_date < natural_start or start_date > natural_end:
        raise SettlementCurrentStateError(400, "Starting Date必须位于所选季度内")
    if closing_date < start_date or closing_date > natural_end:
        raise SettlementCurrentStateError(400, "Closing Date必须位于所选季度且不早于Starting Date")

    account_ids = [line.account_id for line in line_specs]
    if not account_ids:
        raise SettlementCurrentStateError(400, "结算至少需要一个Sub Account")
    if len(account_ids) != len(set(account_ids)):
        raise SettlementCurrentStateError(400, "同一Sub Account不能重复加入结算")
    accounts = {
        item.id: item
        for item in db.scalars(select(SubAccount).where(SubAccount.id.in_(account_ids))).all()
    }
    if len(accounts) != len(account_ids):
        raise SettlementCurrentStateError(404, "部分Sub Account不存在")
    for account in accounts.values():
        if (
            account.client_id != client_id
            or account.platform_id != platform_id
            or account.fee_plan_id != fee_plan_id
        ):
            raise SettlementCurrentStateError(
                409, "Sub Account当前归属与Draft的Client、Platform或Fee Plan不一致，请重新Calculate"
            )
        if account.status != "ACTIVE":
            raise SettlementCurrentStateError(
                409, f"Sub Account {account.account_number}不是Active，不能建立季度Settlement"
            )
        if account.start_date is None:
            raise SettlementCurrentStateError(
                409, f"Sub Account {account.account_number}缺少开始管理日期，不能建立季度Settlement"
            )

    duplicate_period = same_period_non_void_for_accounts(
        db,
        account_ids=account_ids,
        year=year,
        quarter=quarter,
        exclude_id=exclude_settlement_id,
    )
    if duplicate_period:
        raise SettlementCurrentStateError(
            409,
            f"同一Sub Account在{year} Q{quarter}已存在Settlement #{duplicate_period.id}，"
            "即使更换Platform或Fee Plan也不能重复结算",
        )

    later = later_non_void_for_accounts(
        db,
        account_ids=account_ids,
        year=year,
        quarter=quarter,
        exclude_id=exclude_settlement_id,
    )
    if later:
        raise SettlementCurrentStateError(
            409,
            f"存在后续Settlement #{later.id}（{later.year} Q{later.quarter}），请先按时间倒序作废后再补算",
        )

    current_lines: list[CurrentSettlementLine] = []
    for spec in line_specs:
        line_start_date = spec.start_date or start_date
        line_closing_date = spec.closing_date or closing_date
        if line_start_date < natural_start or line_start_date > natural_end:
            raise SettlementCurrentStateError(400, "账户Starting Date必须位于所选季度内")
        if line_closing_date < line_start_date or line_closing_date > natural_end:
            raise SettlementCurrentStateError(
                400, "账户Closing Date必须位于所选季度且不早于Starting Date"
            )

        account = accounts[spec.account_id]
        if line_start_date < account.start_date:
            raise SettlementCurrentStateError(400, "账户Starting Date不能早于Sub Account开始管理日期")
        if account.end_date and line_closing_date > account.end_date:
            raise SettlementCurrentStateError(400, "账户Closing Date不能晚于Sub Account结束管理日期")

        if spec.closing_snapshot_id is None:
            raise SettlementCurrentStateError(400, "Settlement缺少Closing Snapshot")
        closing_snapshot = db.get(BalanceSnapshot, spec.closing_snapshot_id)
        if not closing_snapshot or closing_snapshot.account_id != spec.account_id:
            raise SettlementCurrentStateError(400, "Closing Snapshot与Sub Account不匹配")
        if closing_snapshot.as_of_date != line_closing_date:
            raise SettlementCurrentStateError(400, "Closing Snapshot日期必须等于该账户Closing Date")
        if not closing_snapshot.eligible_for_closing:
            raise SettlementCurrentStateError(400, "该余额快照不是季末或退出日，不能作为Closing")

        previous_line = previous_finalized_line(
            db, account_id=spec.account_id, year=year, quarter=quarter
        )
        if previous_line:
            previous = db.get(QuarterlySettlement, previous_line.settlement_id)
            if previous.year * 4 + previous.quarter != year * 4 + quarter - 1:
                missing_year = previous.year + (previous.quarter == 4)
                missing_quarter = previous.quarter % 4 + 1
                raise SettlementCurrentStateError(
                    409,
                    f"Sub Account {account.account_number}必须先补齐并Finalize "
                    f"{missing_year} Q{missing_quarter}，不能跳季结算",
                )
            if previous_line.closing_snapshot_id is None or previous_line.next_hwm_cents is None:
                raise SettlementCurrentStateError(409, "前序账户结算缺少Closing Snapshot或Next HWM")
            if spec.beginning_snapshot_id not in (None, previous_line.closing_snapshot_id):
                raise SettlementCurrentStateError(409, "Beginning与当前前序Closing Snapshot不一致，请重新Calculate")
            if (
                spec.original_hwm_cents is not None
                and spec.original_hwm_cents != previous_line.next_hwm_cents
            ):
                raise SettlementCurrentStateError(409, "Original HWM与当前前序账户Next HWM不一致，请重新Calculate")
            beginning_snapshot = db.get(BalanceSnapshot, previous_line.closing_snapshot_id)
            if (
                not beginning_snapshot
                or beginning_snapshot.account_id != spec.account_id
                or beginning_snapshot.total_balance_cents != previous_line.closing_cents
            ):
                raise SettlementCurrentStateError(409, "前序Closing Snapshot已变化，请先检查历史结算")
            beginning_snapshot_id = previous_line.closing_snapshot_id
            beginning_cents = previous_line.closing_cents
            original_hwm_cents = previous_line.next_hwm_cents
        else:
            if spec.beginning_snapshot_id is None:
                raise SettlementCurrentStateError(400, "首次账户结算必须选择明确的Beginning Snapshot")
            beginning_snapshot = db.get(BalanceSnapshot, spec.beginning_snapshot_id)
            if not beginning_snapshot or beginning_snapshot.account_id != spec.account_id:
                raise SettlementCurrentStateError(400, "Beginning Snapshot与Sub Account不匹配")
            if not _valid_initial_beginning_date(
                snapshot_date=beginning_snapshot.as_of_date,
                line_start_date=line_start_date,
                natural_quarter_start=natural_start,
            ):
                raise SettlementCurrentStateError(
                    400,
                    "首次Beginning Snapshot必须等于Starting Date；季度首日也可选择上一季末快照",
                )
            if spec.original_hwm_cents is None:
                raise SettlementCurrentStateError(400, "首次账户结算必须输入该Sub Account的Original HWM")
            beginning_snapshot_id = beginning_snapshot.id
            beginning_cents = beginning_snapshot.total_balance_cents
            original_hwm_cents = spec.original_hwm_cents

        contribution_cents = db.scalar(
            select(func.coalesce(func.sum(TransactionRecord.amount_cents), 0)).where(
                *cash_flow_conditions(spec.account_id, beginning_snapshot.as_of_date, line_closing_date),
                TransactionRecord.transaction_type == "CONTRIBUTION",
            )
        ) or 0
        withdrawal_cents = db.scalar(
            select(func.coalesce(func.sum(TransactionRecord.amount_cents), 0)).where(
                *cash_flow_conditions(spec.account_id, beginning_snapshot.as_of_date, line_closing_date),
                TransactionRecord.transaction_type == "WITHDRAWAL",
            )
        ) or 0
        try:
            calculation = calculate_account_settlement(
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
            raise SettlementCurrentStateError(400, str(exc)) from exc
        _require_sql_safe_rate_inputs(
            beginning_cents=calculation.beginning_cents,
            net_contribution_cents=calculation.net_contribution_cents,
            gain_loss_cents=calculation.gain_loss_cents,
            label=f"Sub Account {account.account_number}",
        )
        current_lines.append(
            CurrentSettlementLine(
                spec=spec,
                account_number=account.account_number,
                calculation=calculation,
                beginning_snapshot_id=beginning_snapshot_id,
                closing_snapshot_id=closing_snapshot.id,
                previous_line_id=previous_line.id if previous_line else None,
            )
        )

    aggregate = aggregate_account_settlements(
        start_date=min(line.calculation.start_date for line in current_lines),
        closing_date=max(line.calculation.closing_date for line in current_lines),
        calculations=[line.calculation for line in current_lines],
    )
    _require_sql_safe_rate_inputs(
        beginning_cents=aggregate.beginning_cents,
        net_contribution_cents=aggregate.net_contribution_cents,
        gain_loss_cents=aggregate.gain_loss_cents,
        label="Settlement合计",
    )
    previous_group = previous_finalized_settlement(
        db,
        client_id=client_id,
        platform_id=platform_id,
        fee_plan_id=fee_plan_id,
        year=year,
        quarter=quarter,
    )
    return CurrentSettlement(
        client=client,
        platform=platform,
        fee_plan=fee_plan,
        company_id=client.company_id,
        fc_id=client.fc_id,
        previous_settlement_id=previous_group.id if previous_group else None,
        aggregate=aggregate,
        lines=tuple(current_lines),
    )


def current_state_differences(
    item: QuarterlySettlement, current: CurrentSettlement
) -> list[str]:
    differences: list[str] = []
    expected_parent = {
        **current.aggregate.to_dict(),
        "calculation_mode": ACCOUNT_HWM_MODE,
        "company_id": current.company_id,
        "fc_id": current.fc_id,
        "previous_settlement_id": current.previous_settlement_id,
        "finalized_at": None,
        "void_reason": None,
    }
    for field_name, expected in expected_parent.items():
        if getattr(item, field_name) != expected:
            differences.append(f"Settlement.{field_name}")

    persisted_by_account = {line.account_id: line for line in item.account_lines}
    expected_account_ids = {line.spec.account_id for line in current.lines}
    if set(persisted_by_account) != expected_account_ids:
        differences.append("Settlement.account_lines")
        return differences

    for current_line in current.lines:
        persisted = persisted_by_account[current_line.spec.account_id]
        expected_line = {
            **current_line.calculation.to_dict(),
            "previous_line_id": current_line.previous_line_id,
            "beginning_snapshot_id": current_line.beginning_snapshot_id,
            "closing_snapshot_id": current_line.closing_snapshot_id,
        }
        for field_name, expected in expected_line.items():
            if getattr(persisted, field_name) != expected:
                differences.append(f"Sub Account {current_line.account_number}.{field_name}")
    return differences
