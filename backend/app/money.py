from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


CENT = Decimal("0.01")


def to_cents(value: Decimal | str | int | float) -> int:
    amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    return int(amount * 100)


def from_cents(value: int) -> Decimal:
    return (Decimal(value) / Decimal(100)).quantize(CENT)


def money_string(value: int) -> str:
    return f"{from_cents(value):.2f}"


def rate_to_ppm(numerator_cents: int, denominator_cents: int) -> int | None:
    if denominator_cents == 0:
        return None
    value = (Decimal(numerator_cents) / Decimal(denominator_cents)) * Decimal(1_000_000)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fee_from_bps(chargeable_cents: int, fee_rate_bps: int) -> int:
    value = Decimal(chargeable_cents) * Decimal(fee_rate_bps) / Decimal(10_000)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

