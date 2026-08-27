from __future__ import annotations

from datetime import date

import pytest

from app.money import fee_from_bps
from app.services.calculation import aggregate_account_settlements, calculate_account_settlement


def _calculate(**overrides):
    values = {
        "start_date": date(2025, 1, 1),
        "closing_date": date(2025, 3, 31),
        "beginning_cents": 38_183_381,
        "closing_cents": 46_843_374,
        "contribution_cents": 3_237_584,
        "withdrawal_cents": 0,
        "original_hwm_cents": 43_069_758,
        "fee_rate_bps": 2000,
    }
    values.update(overrides)
    return calculate_account_settlement(**values)


def test_excel_example_one_matches_template() -> None:
    result = _calculate()
    assert result.days == 90
    assert result.net_contribution_cents == 3_237_584
    assert result.gain_loss_cents == 5_422_409
    assert result.period_rate_ppm == 130_910
    assert result.adjusted_hwm_cents == 46_307_342
    assert result.watermark_difference_cents == 536_032
    assert result.service_fee_cents == 107_206
    assert result.next_hwm_cents == 46_843_374


def test_excel_example_two_matches_template() -> None:
    result = _calculate(
        beginning_cents=100_000_000,
        closing_cents=111_000_000,
        contribution_cents=10_000_000,
        original_hwm_cents=100_000_000,
    )
    assert result.gain_loss_cents == 1_000_000
    assert result.period_rate_ppm == 9_091
    assert result.adjusted_hwm_cents == 110_000_000
    assert result.service_fee_cents == 200_000
    assert result.next_hwm_cents == 111_000_000


def test_profitable_account_fee_is_not_offset_by_losing_account() -> None:
    profitable = _calculate(
        start_date=date(2026, 4, 1),
        closing_date=date(2026, 6, 30),
        beginning_cents=100_000,
        closing_cents=110_000,
        contribution_cents=0,
        original_hwm_cents=100_000,
    )
    losing = _calculate(
        start_date=date(2026, 4, 1),
        closing_date=date(2026, 6, 30),
        beginning_cents=100_000,
        closing_cents=80_000,
        contribution_cents=0,
        original_hwm_cents=100_000,
    )
    result = aggregate_account_settlements(
        start_date=date(2026, 4, 1), closing_date=date(2026, 6, 30), calculations=[profitable, losing]
    )
    assert result.watermark_difference_cents == -10_000
    assert result.chargeable_above_hwm_cents == 10_000
    assert result.service_fee_cents == 2_000
    assert result.next_hwm_cents == 210_000


def test_zero_denominator_rate_is_none() -> None:
    result = _calculate(
        start_date=date(2026, 1, 1),
        closing_date=date(2026, 3, 31),
        beginning_cents=0,
        closing_cents=100,
        contribution_cents=0,
        original_hwm_cents=0,
    )
    assert result.period_rate_ppm is None


def test_invalid_dates_rejected() -> None:
    with pytest.raises(ValueError):
        _calculate(start_date=date(2026, 3, 31), closing_date=date(2026, 1, 1))


def test_service_fee_uses_round_half_up_at_half_cent() -> None:
    assert fee_from_bps(chargeable_cents=1, fee_rate_bps=5000) == 1
