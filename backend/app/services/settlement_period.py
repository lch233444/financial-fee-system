from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import BalanceSnapshot, QuarterlySettlement, SettlementAccountLine, TransactionRecord


def cash_flow_conditions(account_id: int, opening_date: date, closing_date: date):
    """The opening snapshot already contains all cash effective on its own date."""
    return (
        TransactionRecord.account_id == account_id,
        TransactionRecord.transaction_date > opening_date,
        TransactionRecord.transaction_date <= closing_date,
    )


def transaction_locked_settlement_id(db: Session, *, account_id: int, transaction_date: date) -> int | None:
    """Protect used cash, including historical skipped quarters, without weakening old locks."""
    return db.scalar(
        select(QuarterlySettlement.id)
        .join(SettlementAccountLine, SettlementAccountLine.settlement_id == QuarterlySettlement.id)
        .outerjoin(BalanceSnapshot, BalanceSnapshot.id == SettlementAccountLine.beginning_snapshot_id)
        .where(
            SettlementAccountLine.account_id == account_id,
            QuarterlySettlement.status == "FINALIZED",
            or_(SettlementAccountLine.start_date <= transaction_date, BalanceSnapshot.as_of_date < transaction_date),
            SettlementAccountLine.closing_date >= transaction_date,
        )
        .order_by(QuarterlySettlement.year, QuarterlySettlement.quarter, QuarterlySettlement.id)
        .limit(1)
    )
