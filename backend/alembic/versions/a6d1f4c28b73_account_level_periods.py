"""store independent periods for settlement account lines

Revision ID: a6d1f4c28b73
Revises: f2a8c7d41e90
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6d1f4c28b73"
down_revision: Union[str, Sequence[str], None] = "f2a8c7d41e90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_period_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_transactions_block_finalized_period")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_validate_finalize")


def _create_transaction_trigger(*, account_periods: bool) -> None:
    start_expression = "line.start_date" if account_periods else "settlement.start_date"
    closing_expression = "line.closing_date" if account_periods else "settlement.closing_date"
    op.execute(
        f"""
        CREATE TRIGGER trg_transactions_block_finalized_period
        BEFORE INSERT ON transactions
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE line.account_id = NEW.account_id
              AND settlement.status = 'FINALIZED'
              AND NEW.transaction_date >= {start_expression}
              AND NEW.transaction_date <= {closing_expression}
        )
        BEGIN
            SELECT RAISE(ABORT, 'transaction_in_finalized_period');
        END
        """
    )


def _create_finalize_trigger(*, account_periods: bool) -> None:
    incomplete_period = """
                    OR line.start_date IS NULL
                    OR line.closing_date IS NULL
                    OR line.days IS NULL
                    OR line.days != CAST(julianday(line.closing_date) - julianday(line.start_date) AS INTEGER) + 1
    """ if account_periods else ""
    snapshot_closing_date = "line.closing_date" if account_periods else "NEW.closing_date"
    transaction_start_date = "line.start_date" if account_periods else "NEW.start_date"
    transaction_closing_date = "line.closing_date" if account_periods else "NEW.closing_date"
    account_date_checks = """
                    OR line.start_date > line.closing_date
                    OR (account.start_date IS NOT NULL AND line.start_date < account.start_date)
                    OR (account.end_date IS NOT NULL AND line.closing_date > account.end_date)
                    OR (line.previous_line_id IS NULL AND beginning.as_of_date != line.start_date)
                    OR (line.previous_line_id IS NOT NULL
                        AND line.beginning_snapshot_id != prior_line.closing_snapshot_id)
    """ if account_periods else ""
    account_joins = """
                LEFT JOIN settlement_account_lines AS prior_line ON prior_line.id = line.previous_line_id
                LEFT JOIN sub_accounts AS account ON account.id = line.account_id
    """ if account_periods else ""
    op.execute(
        f"""
        CREATE TRIGGER trg_settlement_validate_finalize
        BEFORE UPDATE OF status ON quarterly_settlements
        WHEN NEW.status = 'FINALIZED' AND OLD.status != 'FINALIZED'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_has_later_dependency')
            WHERE NEW.calculation_mode != 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1 FROM quarterly_settlements AS later
                WHERE later.client_id = NEW.client_id
                  AND later.platform_id = NEW.platform_id
                  AND later.fee_plan_id = NEW.fee_plan_id
                  AND later.status != 'VOID'
                  AND (later.year * 4 + later.quarter) > (NEW.year * 4 + NEW.quarter)
            );
            SELECT RAISE(ABORT, 'settlement_previous_changed')
            WHERE NEW.calculation_mode != 'ACCOUNT_HWM' AND NEW.previous_settlement_id IS NOT (
                SELECT prior.id FROM quarterly_settlements AS prior
                WHERE prior.client_id = NEW.client_id
                  AND prior.platform_id = NEW.platform_id
                  AND prior.fee_plan_id = NEW.fee_plan_id
                  AND prior.status = 'FINALIZED'
                  AND (prior.year * 4 + prior.quarter) < (NEW.year * 4 + NEW.quarter)
                ORDER BY prior.year DESC, prior.quarter DESC LIMIT 1
            );
            SELECT RAISE(ABORT, 'settlement_previous_hwm_changed')
            WHERE NEW.calculation_mode != 'ACCOUNT_HWM'
              AND NEW.previous_settlement_id IS NOT NULL
              AND NEW.original_hwm_cents != (
                  SELECT prior.next_hwm_cents FROM quarterly_settlements AS prior
                  WHERE prior.id = NEW.previous_settlement_id
              );

            SELECT RAISE(ABORT, 'settlement_account_line_incomplete')
            WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1 FROM settlement_account_lines AS line
                WHERE line.settlement_id = NEW.id
                  AND (line.beginning_snapshot_id IS NULL
                    OR line.closing_snapshot_id IS NULL
                    OR line.original_hwm_cents IS NULL
                    OR line.next_hwm_cents IS NULL
                    OR line.service_fee_cents IS NULL
                    OR line.period_rate_ppm IS NULL
                    {incomplete_period})
            );
            SELECT RAISE(ABORT, 'settlement_account_has_later_dependency')
            WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1
                FROM settlement_account_lines AS current_line
                JOIN settlement_account_lines AS later_line ON later_line.account_id = current_line.account_id
                JOIN quarterly_settlements AS later ON later.id = later_line.settlement_id
                WHERE current_line.settlement_id = NEW.id
                  AND later.id != NEW.id
                  AND later.status != 'VOID'
                  AND (later.year * 4 + later.quarter) > (NEW.year * 4 + NEW.quarter)
            );
            SELECT RAISE(ABORT, 'settlement_account_previous_changed')
            WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1 FROM settlement_account_lines AS line
                WHERE line.settlement_id = NEW.id
                  AND line.previous_line_id IS NOT (
                      SELECT prior_line.id
                      FROM settlement_account_lines AS prior_line
                      JOIN quarterly_settlements AS prior ON prior.id = prior_line.settlement_id
                      WHERE prior_line.account_id = line.account_id
                        AND prior.calculation_mode = 'ACCOUNT_HWM'
                        AND prior.status = 'FINALIZED'
                        AND (prior.year * 4 + prior.quarter) < (NEW.year * 4 + NEW.quarter)
                      ORDER BY prior.year DESC, prior.quarter DESC LIMIT 1
                  )
            );
            SELECT RAISE(ABORT, 'settlement_account_previous_hwm_changed')
            WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1 FROM settlement_account_lines AS line
                JOIN settlement_account_lines AS prior_line ON prior_line.id = line.previous_line_id
                WHERE line.settlement_id = NEW.id
                  AND line.original_hwm_cents != prior_line.next_hwm_cents
            );
            SELECT RAISE(ABORT, 'settlement_snapshot_invalid')
            WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1 FROM settlement_account_lines AS line
                LEFT JOIN balance_snapshots AS beginning ON beginning.id = line.beginning_snapshot_id
                LEFT JOIN balance_snapshots AS closing ON closing.id = line.closing_snapshot_id
                {account_joins}
                WHERE line.settlement_id = NEW.id
                  AND (beginning.id IS NULL OR closing.id IS NULL
                    OR beginning.account_id != line.account_id
                    OR closing.account_id != line.account_id
                    OR closing.eligible_for_closing != 1
                    OR closing.as_of_date != {snapshot_closing_date}
                    {account_date_checks})
            );
            SELECT RAISE(ABORT, 'settlement_snapshot_evidence_missing')
            WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                JOIN balance_snapshots AS snapshot
                  ON snapshot.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
                WHERE line.settlement_id = NEW.id
                  AND snapshot.statement_import_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM attachments
                      WHERE attachments.entity_type = 'SNAPSHOT'
                        AND attachments.entity_id = snapshot.id
                  )
            );
            SELECT RAISE(ABORT, 'settlement_transaction_evidence_missing')
            WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                JOIN transactions AS transaction_record ON transaction_record.account_id = line.account_id
                WHERE line.settlement_id = NEW.id
                  AND transaction_record.transaction_date > {transaction_start_date}
                  AND transaction_record.transaction_date <= {transaction_closing_date}
                  AND transaction_record.attachment_id IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM attachments
                      WHERE attachments.entity_type = 'TRANSACTION'
                        AND attachments.entity_id = transaction_record.id
                  )
            );
        END
        """
    )


def upgrade() -> None:
    columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("settlement_account_lines")
    }
    if "start_date" not in columns:
        op.add_column("settlement_account_lines", sa.Column("start_date", sa.Date(), nullable=True))
    if "closing_date" not in columns:
        op.add_column("settlement_account_lines", sa.Column("closing_date", sa.Date(), nullable=True))
    if "days" not in columns:
        op.add_column("settlement_account_lines", sa.Column("days", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE settlement_account_lines
        SET start_date = (
                SELECT settlement.start_date FROM quarterly_settlements AS settlement
                WHERE settlement.id = settlement_account_lines.settlement_id
            ),
            closing_date = (
                SELECT settlement.closing_date FROM quarterly_settlements AS settlement
                WHERE settlement.id = settlement_account_lines.settlement_id
            ),
            days = (
                SELECT settlement.days FROM quarterly_settlements AS settlement
                WHERE settlement.id = settlement_account_lines.settlement_id
            )
        WHERE start_date IS NULL OR closing_date IS NULL OR days IS NULL
        """
    )
    missing = op.get_bind().exec_driver_sql(
        "SELECT COUNT(*) FROM settlement_account_lines WHERE start_date IS NULL OR closing_date IS NULL OR days IS NULL"
    ).scalar_one()
    if missing:
        raise RuntimeError("settlement_account_lines存在无法回填的账户期间，请先备份并人工检查")
    _drop_period_triggers()
    _create_transaction_trigger(account_periods=True)
    _create_finalize_trigger(account_periods=True)


def downgrade() -> None:
    _drop_period_triggers()
    with op.batch_alter_table("settlement_account_lines") as batch_op:
        batch_op.drop_column("days")
        batch_op.drop_column("closing_date")
        batch_op.drop_column("start_date")
    _create_transaction_trigger(account_periods=False)
    _create_finalize_trigger(account_periods=False)
