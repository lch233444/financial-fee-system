"""lock settlement chain and historical ownership

Revision ID: e91f7c6a2b40
Revises: d8f42c0b7a11
Create Date: 2026-08-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e91f7c6a2b40"
down_revision: Union[str, Sequence[str], None] = "d8f42c0b7a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    settlement_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("quarterly_settlements")
    }
    chain_columns = {"company_id", "fc_id", "previous_settlement_id"}
    if settlement_columns.isdisjoint(chain_columns):
        with op.batch_alter_table("quarterly_settlements") as batch_op:
            batch_op.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("fc_id", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("previous_settlement_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_settlement_company", "companies", ["company_id"], ["id"], ondelete="RESTRICT"
            )
            batch_op.create_foreign_key("fk_settlement_fc", "fcs", ["fc_id"], ["id"], ondelete="RESTRICT")
            batch_op.create_foreign_key(
                "fk_settlement_previous",
                "quarterly_settlements",
                ["previous_settlement_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.create_index("ix_quarterly_settlements_company_id", ["company_id"], unique=False)
            batch_op.create_index("ix_quarterly_settlements_fc_id", ["fc_id"], unique=False)
            batch_op.create_index(
                "ix_quarterly_settlements_previous_settlement_id", ["previous_settlement_id"], unique=False
            )
    elif not chain_columns.issubset(settlement_columns):
        raise RuntimeError("quarterly_settlements存在不完整的0.2.3链字段，请先备份并人工检查")

    # Freeze the owner visible at migration time for existing history.
    op.execute(
        """
        UPDATE quarterly_settlements
        SET company_id = (
                SELECT clients.company_id FROM clients WHERE clients.id = quarterly_settlements.client_id
            ),
            fc_id = (
                SELECT clients.fc_id FROM clients WHERE clients.id = quarterly_settlements.client_id
            )
        """
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
        CREATE TRIGGER IF NOT EXISTS trg_settlement_block_out_of_order_insert
        BEFORE INSERT ON quarterly_settlements
        WHEN EXISTS (
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
        CREATE TRIGGER IF NOT EXISTS trg_settlement_validate_finalize
        BEFORE UPDATE OF status ON quarterly_settlements
        WHEN NEW.status = 'FINALIZED' AND OLD.status != 'FINALIZED'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_has_later_dependency')
            WHERE EXISTS (
                SELECT 1 FROM quarterly_settlements AS later
                WHERE later.client_id = NEW.client_id
                  AND later.platform_id = NEW.platform_id
                  AND later.fee_plan_id = NEW.fee_plan_id
                  AND later.status != 'VOID'
                  AND (later.year * 4 + later.quarter) > (NEW.year * 4 + NEW.quarter)
            );
            SELECT RAISE(ABORT, 'settlement_previous_changed')
            WHERE NEW.previous_settlement_id IS NOT (
                SELECT prior.id
                FROM quarterly_settlements AS prior
                WHERE prior.client_id = NEW.client_id
                  AND prior.platform_id = NEW.platform_id
                  AND prior.fee_plan_id = NEW.fee_plan_id
                  AND prior.status = 'FINALIZED'
                  AND (prior.year * 4 + prior.quarter) < (NEW.year * 4 + NEW.quarter)
                ORDER BY prior.year DESC, prior.quarter DESC
                LIMIT 1
            );
            SELECT RAISE(ABORT, 'settlement_previous_hwm_changed')
            WHERE NEW.previous_settlement_id IS NOT NULL
              AND NEW.original_hwm_cents != (
                  SELECT prior.next_hwm_cents
                  FROM quarterly_settlements AS prior
                  WHERE prior.id = NEW.previous_settlement_id
              );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER IF NOT EXISTS trg_settlement_validate_void
        BEFORE UPDATE OF status ON quarterly_settlements
        WHEN NEW.status = 'VOID' AND OLD.status != 'VOID'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_has_later_dependency')
            WHERE EXISTS (
                SELECT 1 FROM quarterly_settlements AS later
                WHERE later.client_id = NEW.client_id
                  AND later.platform_id = NEW.platform_id
                  AND later.fee_plan_id = NEW.fee_plan_id
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
    # Persist the nearest earlier finalized HWM source for existing chains.
    op.execute(
        """
        UPDATE quarterly_settlements
        SET previous_settlement_id = (
            SELECT prior.id
            FROM quarterly_settlements AS prior
            WHERE prior.client_id = quarterly_settlements.client_id
              AND prior.platform_id = quarterly_settlements.platform_id
              AND prior.fee_plan_id = quarterly_settlements.fee_plan_id
              AND prior.status = 'FINALIZED'
              AND (prior.year * 4 + prior.quarter) <
                  (quarterly_settlements.year * 4 + quarterly_settlements.quarter)
            ORDER BY prior.year DESC, prior.quarter DESC
            LIMIT 1
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_validate_void")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_validate_finalize")
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_block_out_of_order_insert")
    op.execute("DROP TRIGGER IF EXISTS trg_transactions_block_finalized_period")
    with op.batch_alter_table("quarterly_settlements") as batch_op:
        batch_op.drop_index("ix_quarterly_settlements_previous_settlement_id")
        batch_op.drop_index("ix_quarterly_settlements_fc_id")
        batch_op.drop_index("ix_quarterly_settlements_company_id")
        batch_op.drop_constraint("fk_settlement_previous", type_="foreignkey")
        batch_op.drop_constraint("fk_settlement_fc", type_="foreignkey")
        batch_op.drop_constraint("fk_settlement_company", type_="foreignkey")
        batch_op.drop_column("previous_settlement_id")
        batch_op.drop_column("fc_id")
        batch_op.drop_column("company_id")
