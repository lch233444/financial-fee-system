from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from ..money import fee_from_bps, rate_to_ppm


FORMULA_VERSION = "HWM-2.0-ACCOUNT"


@dataclass(frozen=True)
class SettlementCalculation:
    start_date: date
    closing_date: date
    days: int
    beginning_cents: int
    contribution_cents: int
    withdrawal_cents: int
    net_contribution_cents: int
    closing_cents: int
    gain_loss_cents: int
    period_rate_ppm: int | None
    original_hwm_cents: int
    adjusted_hwm_cents: int
    watermark_difference_cents: int
    chargeable_above_hwm_cents: int
    service_fee_cents: int
    next_hwm_cents: int
    fee_rate_bps: int
    formula_version: str = FORMULA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


def calculate_account_settlement(
    *,
    start_date: date,
    closing_date: date,
    beginning_cents: int,
    closing_cents: int,
    contribution_cents: int,
    withdrawal_cents: int,
    original_hwm_cents: int,
    fee_rate_bps: int,
) -> SettlementCalculation:
    if closing_date < start_date:
        raise ValueError("Closing Date不能早于Starting Date")
    if fee_rate_bps < 0 or fee_rate_bps > 10_000:
        raise ValueError("Fee Rate必须介于0%与100%之间")

    net_contribution_cents = contribution_cents - withdrawal_cents
    gain_loss_cents = closing_cents - beginning_cents - net_contribution_cents
    denominator_cents = beginning_cents + net_contribution_cents
    period_rate_ppm = rate_to_ppm(gain_loss_cents, denominator_cents)
    adjusted_hwm_cents = original_hwm_cents + net_contribution_cents
    watermark_difference_cents = closing_cents - adjusted_hwm_cents
    chargeable_above_hwm_cents = max(watermark_difference_cents, 0)
    service_fee_cents = fee_from_bps(chargeable_above_hwm_cents, fee_rate_bps)
    next_hwm_cents = max(adjusted_hwm_cents, closing_cents)

    return SettlementCalculation(
        start_date=start_date,
        closing_date=closing_date,
        days=(closing_date - start_date).days + 1,
        beginning_cents=beginning_cents,
        contribution_cents=contribution_cents,
        withdrawal_cents=withdrawal_cents,
        net_contribution_cents=net_contribution_cents,
        closing_cents=closing_cents,
        gain_loss_cents=gain_loss_cents,
        period_rate_ppm=period_rate_ppm,
        original_hwm_cents=original_hwm_cents,
        adjusted_hwm_cents=adjusted_hwm_cents,
        watermark_difference_cents=watermark_difference_cents,
        chargeable_above_hwm_cents=chargeable_above_hwm_cents,
        service_fee_cents=service_fee_cents,
        next_hwm_cents=next_hwm_cents,
        fee_rate_bps=fee_rate_bps,
    )


def aggregate_account_settlements(
    *, start_date: date, closing_date: date, calculations: list[SettlementCalculation]
) -> SettlementCalculation:
    if not calculations:
        raise ValueError("结算至少需要一个子账户")
    beginning_cents = sum(item.beginning_cents for item in calculations)
    contribution_cents = sum(item.contribution_cents for item in calculations)
    withdrawal_cents = sum(item.withdrawal_cents for item in calculations)
    net_contribution_cents = sum(item.net_contribution_cents for item in calculations)
    closing_cents = sum(item.closing_cents for item in calculations)
    gain_loss_cents = sum(item.gain_loss_cents for item in calculations)
    return SettlementCalculation(
        start_date=start_date,
        closing_date=closing_date,
        days=(closing_date - start_date).days + 1,
        beginning_cents=beginning_cents,
        contribution_cents=contribution_cents,
        withdrawal_cents=withdrawal_cents,
        net_contribution_cents=net_contribution_cents,
        closing_cents=closing_cents,
        gain_loss_cents=gain_loss_cents,
        period_rate_ppm=rate_to_ppm(gain_loss_cents, beginning_cents + net_contribution_cents),
        original_hwm_cents=sum(item.original_hwm_cents for item in calculations),
        adjusted_hwm_cents=sum(item.adjusted_hwm_cents for item in calculations),
        watermark_difference_cents=sum(item.watermark_difference_cents for item in calculations),
        chargeable_above_hwm_cents=sum(item.chargeable_above_hwm_cents for item in calculations),
        service_fee_cents=sum(item.service_fee_cents for item in calculations),
        next_hwm_cents=sum(item.next_hwm_cents for item in calculations),
        fee_rate_bps=calculations[0].fee_rate_bps,
    )


def quarter_dates(year: int, quarter: int) -> tuple[date, date]:
    if quarter == 1:
        return date(year, 1, 1), date(year, 3, 31)
    if quarter == 2:
        return date(year, 4, 1), date(year, 6, 30)
    if quarter == 3:
        return date(year, 7, 1), date(year, 9, 30)
    if quarter == 4:
        return date(year, 10, 1), date(year, 12, 31)
    raise ValueError("Quarter必须是1至4")


def is_quarter_end(value: date) -> bool:
    return value in {date(value.year, 3, 31), date(value.year, 6, 30), date(value.year, 9, 30), date(value.year, 12, 31)}
