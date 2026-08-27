"""add account-level HWM and evidence gates

Revision ID: f2a8c7d41e90
Revises: e91f7c6a2b40
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a8c7d41e90"
down_revision: Union[str, Sequence[str], None] = "e91f7c6a2b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_settlement_triggers() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_validate_void")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_validate_finalize")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_block_out_of_order_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_account_line_order")


def upgrade() -> None:
    # SQLite batch migrations temporarily replace settlement_account_lines;
    # triggers that reference it must be removed before the replacement.
    op.execute("DROP TRIGGER IF EXISTS trg_transactions_block_finalized_period")
    _drop_settlement_triggers()
    settlement_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("quarterly_settlements")
    }
    if "calculation_mode" not in settlement_columns:
        with op.batch_alter_table("quarterly_settlements") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "calculation_mode",
                    sa.String(length=30),
                    nullable=False,
                    server_default="LEGACY_GROUP_HWM",
                )
            )
            batch_op.create_index(
                "ix_quarterly_settlements_calculation_mode", ["calculation_mode"], unique=False
            )

    line_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("settlement_account_lines")
    }
    expected = {
        "previous_line_id",
        "beginning_snapshot_id",
        "contribution_cents",
        "withdrawal_cents",
        "net_contribution_cents",
        "gain_loss_cents",
        "period_rate_ppm",
        "original_hwm_cents",
        "adjusted_hwm_cents",
        "watermark_difference_cents",
        "chargeable_above_hwm_cents",
        "service_fee_cents",
        "next_hwm_cents",
        "fee_rate_bps",
        "formula_version",
    }
    if line_columns.isdisjoint(expected):
        with op.batch_alter_table("settlement_account_lines") as batch_op:
            batch_op.add_column(sa.Column("previous_line_id", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("beginning_snapshot_id", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("contribution_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("withdrawal_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("net_contribution_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("gain_loss_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("period_rate_ppm", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("original_hwm_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("adjusted_hwm_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("watermark_difference_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("chargeable_above_hwm_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("service_fee_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("next_hwm_cents", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("fee_rate_bps", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("formula_version", sa.String(length=30), nullable=True))
            batch_op.create_foreign_key(
                "fk_settlement_line_previous",
                "settlement_account_lines",
                ["previous_line_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_foreign_key(
                "fk_settlement_line_beginning_snapshot",
                "balance_snapshots",
                ["beginning_snapshot_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_index(
                "ix_settlement_account_lines_previous_line_id", ["previous_line_id"], unique=False
            )
            batch_op.create_index(
                "ix_settlement_account_lines_beginning_snapshot_id",
                ["beginning_snapshot_id"],
                unique=False,
            )
            batch_op.create_index(
                "ix_settlement_account_lines_closing_snapshot_id",
                ["closing_snapshot_id"],
                unique=False,
            )
    elif not expected.issubset(line_columns):
        raise RuntimeError("settlement_account_lines存在不完整的账户HWM字段，请先备份并人工检查")

    op.execute(
        "UPDATE quarterly_settlements SET calculation_mode = 'LEGACY_GROUP_HWM' "
        "WHERE calculation_mode IS NULL OR calculation_mode = ''"
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_transactions_block_finalized_period
        BEFORE INSERT ON transactions
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE line.account_id = NEW.account_id
              AND settlement.status = 'FINALIZED'
              AND NEW.transaction_date >= settlement.start_date
              AND NEW.transaction_date <= settlement.closing_date
        )
        BEGIN
            SELECT RAISE(ABORT, 'transaction_in_finalized_period');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_block_out_of_order_insert
        BEFORE INSERT ON quarterly_settlements
        WHEN NEW.calculation_mode != 'ACCOUNT_HWM' AND EXISTS (
            SELECT 1 FROM quarterly_settlements AS later
            WHERE later.client_id = NEW.client_id
              AND later.platform_id = NEW.platform_id
              AND later.fee_plan_id = NEW.fee_plan_id
              AND later.status != 'VOID'
              AND (later.year * 4 + later.quarter) > (NEW.year * 4 + NEW.quarter)
        )
        BEGIN
            SELECT RAISE(ABORT, 'settlement_out_of_order_insert');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_account_line_order
        BEFORE INSERT ON settlement_account_lines
        WHEN (SELECT calculation_mode FROM quarterly_settlements WHERE id = NEW.settlement_id) = 'ACCOUNT_HWM'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_account_out_of_order')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS later_line
                JOIN quarterly_settlements AS later ON later.id = later_line.settlement_id
                JOIN quarterly_settlements AS current ON current.id = NEW.settlement_id
                WHERE later_line.account_id = NEW.account_id
                  AND later.id != current.id
                  AND later.status != 'VOID'
                  AND (later.year * 4 + later.quarter) > (current.year * 4 + current.quarter)
            );
        END
        """
    )
    op.execute(
        """
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
                    OR line.service_fee_cents IS NULL)
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
                WHERE line.settlement_id = NEW.id
                  AND (beginning.id IS NULL OR closing.id IS NULL
                    OR beginning.account_id != line.account_id
                    OR closing.account_id != line.account_id
                    OR closing.eligible_for_closing != 1
                    OR closing.as_of_date != NEW.closing_date)
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
                  AND transaction_record.transaction_date > NEW.start_date
                  AND transaction_record.transaction_date <= NEW.closing_date
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
    op.execute(
        """
        CREATE TRIGGER trg_settlement_validate_void
        BEFORE UPDATE OF status ON quarterly_settlements
        WHEN NEW.status = 'VOID' AND OLD.status != 'VOID'
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
            SELECT RAISE(ABORT, 'settlement_has_active_invoice')
            WHERE EXISTS (
                SELECT 1 FROM invoices
                WHERE invoices.settlement_id = NEW.id
                  AND invoices.lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
            );
        END
        """
    )


def downgrade() -> None:
    _drop_settlement_triggers()
    with op.batch_alter_table("settlement_account_lines") as batch_op:
        batch_op.drop_index("ix_settlement_account_lines_closing_snapshot_id")
        batch_op.drop_index("ix_settlement_account_lines_beginning_snapshot_id")
        batch_op.drop_index("ix_settlement_account_lines_previous_line_id")
        batch_op.drop_constraint("fk_settlement_line_beginning_snapshot", type_="foreignkey")
        batch_op.drop_constraint("fk_settlement_line_previous", type_="foreignkey")
        for name in [
            "formula_version", "fee_rate_bps", "next_hwm_cents", "service_fee_cents",
            "chargeable_above_hwm_cents", "watermark_difference_cents", "adjusted_hwm_cents",
            "original_hwm_cents", "period_rate_ppm", "gain_loss_cents", "net_contribution_cents",
            "withdrawal_cents", "contribution_cents", "beginning_snapshot_id", "previous_line_id",
        ]:
            batch_op.drop_column(name)
    with op.batch_alter_table("quarterly_settlements") as batch_op:
        batch_op.drop_index("ix_quarterly_settlements_calculation_mode")
        batch_op.drop_column("calculation_mode")
