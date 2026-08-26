from __future__ import annotations

from datetime import date

import pytest

from app.services.calculation import AccountPeriodInput, calculate_settlement
from app.money import fee_from_bps


def test_excel_example_one_matches_template() -> None:
    result = calculate_settlement(
        start_date=date(2025, 1, 1),
        closing_date=date(2025, 3, 31),
        account_lines=[AccountPeriodInput(account_id=1, beginning_cents=38_183_381, closing_cents=46_843_374)],
        contribution_cents=3_237_584,
        withdrawal_cents=0,
        original_hwm_cents=43_069_758,
        fee_rate_bps=2000,
    )
    assert result.days == 90
    assert result.net_contribution_cents == 3_237_584
    assert result.gain_loss_cents == 5_422_409
    assert result.period_rate_ppm == 130_910
    assert result.adjusted_hwm_cents == 46_307_342
    assert result.watermark_difference_cents == 536_032
    assert result.service_fee_cents == 107_206
    assert result.next_hwm_cents == 46_843_374


def test_excel_example_two_matches_template() -> None:
    result = calculate_settlement(
        start_date=date(2025, 1, 1),
        closing_date=date(2025, 3, 31),
        account_lines=[AccountPeriodInput(account_id=1, beginning_cents=100_000_000, closing_cents=111_000_000)],
        contribution_cents=10_000_000,
        withdrawal_cents=0,
        original_hwm_cents=100_000_000,
        fee_rate_bps=2000,
    )
    assert result.gain_loss_cents == 1_000_000
    assert result.period_rate_ppm == 9_091
    assert result.adjusted_hwm_cents == 110_000_000
    assert result.service_fee_cents == 200_000
    assert result.next_hwm_cents == 111_000_000


def test_multi_account_loss_and_zero_fee() -> None:
    result = calculate_settlement(
        start_date=date(2026, 4, 1),
        closing_date=date(2026, 6, 30),
        account_lines=[
            AccountPeriodInput(account_id=1, beginning_cents=50_000_000, closing_cents=40_000_000),
            AccountPeriodInput(account_id=2, beginning_cents=30_000_000, closing_cents=25_000_000),
        ],
        contribution_cents=5_000_000,
        withdrawal_cents=1_000_000,
        original_hwm_cents=80_000_000,
        fee_rate_bps=2000,
    )
    assert result.beginning_cents == 80_000_000
    assert result.net_contribution_cents == 4_000_000
    assert result.closing_cents == 65_000_000
    assert result.watermark_difference_cents == -19_000_000
    assert result.chargeable_above_hwm_cents == 0
    assert result.service_fee_cents == 0
    assert result.next_hwm_cents == 84_000_000


def test_zero_denominator_rate_is_none() -> None:
    result = calculate_settlement(
        start_date=date(2026, 1, 1),
        closing_date=date(2026, 3, 31),
        account_lines=[AccountPeriodInput(account_id=1, beginning_cents=0, closing_cents=100)],
        contribution_cents=0,
        withdrawal_cents=0,
        original_hwm_cents=0,
        fee_rate_bps=2000,
    )
    assert result.period_rate_ppm is None


def test_invalid_dates_rejected() -> None:
    with pytest.raises(ValueError):
        calculate_settlement(
            start_date=date(2026, 3, 31),
            closing_date=date(2026, 1, 1),
            account_lines=[AccountPeriodInput(account_id=1, beginning_cents=0, closing_cents=0)],
            contribution_cents=0,
            withdrawal_cents=0,
            original_hwm_cents=0,
            fee_rate_bps=2000,
        )


def test_service_fee_uses_round_half_up_at_half_cent() -> None:
    assert fee_from_bps(chargeable_cents=1, fee_rate_bps=5000) == 1
