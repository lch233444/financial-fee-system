"""freeze aggregated invoice sources and line items

Revision ID: c4b7f1d92e60
Revises: a6d1f4c28b73
Create Date: 2026-08-28
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4b7f1d92e60"
down_revision: Union[str, Sequence[str], None] = "a6d1f4c28b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INVOICE_PARENT_COLUMNS = {"client_id", "year", "quarter", "fee_plan_id"}
_INVOICE_TABLE_COLUMNS = {
    "invoice_sources": {
        "id",
        "invoice_id",
        "settlement_id",
        "locked_amount_cents",
        "active",
        "created_at",
        "updated_at",
    },
    "invoice_lines": {
        "id",
        "invoice_id",
        "source_id",
        "source_settlement_id",
        "source_account_line_id",
        "platform_id",
        "platform_name_snapshot",
        "account_number_snapshot",
        "start_date",
        "closing_date",
        "service_fee_cents",
        "display_order",
        "created_at",
        "updated_at",
    },
    "invoice_issue_attempts": {
        "id",
        "invoice_id",
        "invoice_number",
        "status",
        "started_at",
        "completed_at",
        "details",
    },
}


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _drop_invoice_triggers() -> None:
    for trigger_name in (
        "trg_invoice_validate_issue",
        "trg_invoice_source_insert_draft_only",
        "trg_invoice_source_update_draft_only",
        "trg_invoice_source_delete_draft_only",
        "trg_invoice_line_insert_draft_only",
        "trg_invoice_line_update_draft_only",
        "trg_invoice_line_delete_draft_only",
        "trg_invoice_sources_deactivate_on_void",
        "trg_invoice_financial_header_update_lock",
        "trg_invoice_insert_draft_only",
        "trg_invoice_lifecycle_transition",
        "trg_invoice_issue_metadata_guard",
        "trg_payment_validate_insert",
        "trg_invoice_block_void_with_payment",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _ensure_invoice_parent_columns() -> None:
    columns = _column_names("invoices")
    for column_name in sorted(_INVOICE_PARENT_COLUMNS - columns):
        op.add_column("invoices", sa.Column(column_name, sa.Integer(), nullable=True))

    op.execute(
        """
        UPDATE invoices
        SET client_id = COALESCE(
                client_id,
                (SELECT settlement.client_id
                 FROM quarterly_settlements AS settlement
                 WHERE settlement.id = invoices.settlement_id)
            ),
            year = COALESCE(
                year,
                (SELECT settlement.year
                 FROM quarterly_settlements AS settlement
                 WHERE settlement.id = invoices.settlement_id)
            ),
            quarter = COALESCE(
                quarter,
                (SELECT settlement.quarter
                 FROM quarterly_settlements AS settlement
                 WHERE settlement.id = invoices.settlement_id)
            ),
            fee_plan_id = COALESCE(
                fee_plan_id,
                (SELECT settlement.fee_plan_id
                 FROM quarterly_settlements AS settlement
                 WHERE settlement.id = invoices.settlement_id)
            )
        WHERE client_id IS NULL OR year IS NULL OR quarter IS NULL OR fee_plan_id IS NULL
        """
    )
    missing = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM invoices
        WHERE client_id IS NULL OR year IS NULL OR quarter IS NULL OR fee_plan_id IS NULL
        """
    ).scalar_one()
    if missing:
        raise RuntimeError("Invoice存在无法从兼容Settlement回填的季度归属，请先备份并人工检查")

    inspector = sa.inspect(op.get_bind())
    reflected_columns = {column["name"]: column for column in inspector.get_columns("invoices")}
    foreign_key_columns = {
        tuple(foreign_key.get("constrained_columns") or ())
        for foreign_key in inspector.get_foreign_keys("invoices")
    }
    needs_batch = any(reflected_columns[name].get("nullable", True) for name in _INVOICE_PARENT_COLUMNS)
    needs_client_fk = ("client_id",) not in foreign_key_columns
    needs_fee_plan_fk = ("fee_plan_id",) not in foreign_key_columns
    if needs_batch or needs_client_fk or needs_fee_plan_fk:
        with op.batch_alter_table("invoices") as batch_op:
            for column_name in sorted(_INVOICE_PARENT_COLUMNS):
                if reflected_columns[column_name].get("nullable", True):
                    batch_op.alter_column(
                        column_name,
                        existing_type=sa.Integer(),
                        nullable=False,
                    )
            if needs_client_fk:
                batch_op.create_foreign_key(
                    "fk_invoices_client",
                    "clients",
                    ["client_id"],
                    ["id"],
                    ondelete="RESTRICT",
                )
            if needs_fee_plan_fk:
                batch_op.create_foreign_key(
                    "fk_invoices_fee_plan",
                    "fee_plans",
                    ["fee_plan_id"],
                    ["id"],
                    ondelete="RESTRICT",
                )

    for column_name in sorted(_INVOICE_PARENT_COLUMNS):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_invoices_{column_name} "
            f"ON invoices ({column_name})"
        )


def _create_invoice_sources() -> None:
    op.create_table(
        "invoice_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("settlement_id", sa.Integer(), nullable=False),
        sa.Column("locked_amount_cents", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["settlement_id"], ["quarterly_settlements.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invoice_id", "settlement_id", name="uq_invoice_source_invoice_settlement"
        ),
    )
    op.create_index("ix_invoice_sources_invoice_id", "invoice_sources", ["invoice_id"])
    op.create_index("ix_invoice_sources_settlement_id", "invoice_sources", ["settlement_id"])
    op.create_index("ix_invoice_sources_active", "invoice_sources", ["active"])


def _create_invoice_lines() -> None:
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("source_settlement_id", sa.Integer(), nullable=False),
        sa.Column("source_account_line_id", sa.Integer(), nullable=True),
        sa.Column("platform_id", sa.Integer(), nullable=True),
        sa.Column("platform_name_snapshot", sa.String(length=200), nullable=False),
        sa.Column("account_number_snapshot", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("closing_date", sa.Date(), nullable=True),
        sa.Column("service_fee_cents", sa.Integer(), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["invoice_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_settlement_id"], ["quarterly_settlements.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_account_line_id"], ["settlement_account_lines.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_id", "source_account_line_id", name="uq_invoice_line_source_account"
        ),
    )
    for column_name in (
        "invoice_id",
        "source_id",
        "source_settlement_id",
        "source_account_line_id",
        "platform_id",
    ):
        op.create_index(f"ix_invoice_lines_{column_name}", "invoice_lines", [column_name])


def _create_invoice_issue_attempts() -> None:
    op.create_table(
        "invoice_issue_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'COMPLETED', 'FAILED', 'RECOVERED_TO_DRAFT')",
            name="ck_invoice_issue_attempt_status",
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invoice_issue_attempts_invoice_id", "invoice_issue_attempts", ["invoice_id"]
    )
    op.create_index(
        "ix_invoice_issue_attempts_invoice_number", "invoice_issue_attempts", ["invoice_number"]
    )
    op.create_index("ix_invoice_issue_attempts_status", "invoice_issue_attempts", ["status"])


def _ensure_invoice_tables() -> None:
    creators = {
        "invoice_sources": _create_invoice_sources,
        "invoice_lines": _create_invoice_lines,
        "invoice_issue_attempts": _create_invoice_issue_attempts,
    }
    tables = _table_names()
    for table_name, creator in creators.items():
        if table_name not in tables:
            creator()
            tables.add(table_name)
            continue
        columns = _column_names(table_name)
        if not _INVOICE_TABLE_COLUMNS[table_name].issubset(columns):
            missing = sorted(_INVOICE_TABLE_COLUMNS[table_name] - columns)
            raise RuntimeError(
                f"{table_name}存在不完整的0.2.9字段，请先备份并人工检查: {', '.join(missing)}"
            )

    for table_name, column_names in {
        "invoice_sources": ("invoice_id", "settlement_id", "active"),
        "invoice_lines": (
            "invoice_id",
            "source_id",
            "source_settlement_id",
            "source_account_line_id",
            "platform_id",
        ),
        "invoice_issue_attempts": ("invoice_id", "invoice_number", "status"),
    }.items():
        for column_name in column_names:
            op.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table_name}_{column_name} "
                f"ON {table_name} ({column_name})"
            )


def _backfill_invoice_ledger() -> None:
    parent_mismatch = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM invoices AS invoice
        JOIN quarterly_settlements AS settlement ON settlement.id = invoice.settlement_id
        WHERE invoice.client_id IS NOT settlement.client_id
           OR invoice.year IS NOT settlement.year
           OR invoice.quarter IS NOT settlement.quarter
           OR invoice.fee_plan_id IS NOT settlement.fee_plan_id
           OR invoice.company_id IS NOT settlement.company_id
           OR invoice.fc_id IS NOT settlement.fc_id
           OR invoice.amount_cents IS NOT settlement.service_fee_cents
        """
    ).scalar_one()
    if parent_mismatch:
        raise RuntimeError("历史Invoice与兼容Settlement冻结字段或金额不一致，请先备份并人工检查")

    op.execute(
        """
        INSERT INTO invoice_sources (
            invoice_id, settlement_id, locked_amount_cents, active, created_at, updated_at
        )
        SELECT invoice.id,
               invoice.settlement_id,
               settlement.service_fee_cents,
               CASE WHEN invoice.lifecycle_status = 'VOID' THEN 0 ELSE 1 END,
               invoice.created_at,
               invoice.updated_at
        FROM invoices AS invoice
        JOIN quarterly_settlements AS settlement ON settlement.id = invoice.settlement_id
        WHERE NOT EXISTS (
            SELECT 1 FROM invoice_sources AS source WHERE source.invoice_id = invoice.id
        )
        """
    )
    op.execute(
        """
        UPDATE invoice_sources
        SET active = CASE
                WHEN (SELECT invoice.lifecycle_status FROM invoices AS invoice
                      WHERE invoice.id = invoice_sources.invoice_id) = 'VOID'
                THEN 0 ELSE 1 END,
            updated_at = COALESCE(updated_at, CURRENT_TIMESTAMP)
        """
    )

    invalid_account_lines = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM invoice_sources AS source
        JOIN quarterly_settlements AS settlement ON settlement.id = source.settlement_id
        LEFT JOIN settlement_account_lines AS line ON line.settlement_id = settlement.id
        LEFT JOIN sub_accounts AS account ON account.id = line.account_id
        LEFT JOIN platforms AS platform ON platform.id = settlement.platform_id
        WHERE settlement.calculation_mode = 'ACCOUNT_HWM'
          AND (line.id IS NULL OR line.service_fee_cents IS NULL
               OR account.id IS NULL OR platform.id IS NULL)
        """
    ).scalar_one()
    if invalid_account_lines:
        raise RuntimeError("ACCOUNT_HWM Invoice缺少可冻结的账户收费明细，请先备份并人工检查")

    op.execute(
        """
        INSERT INTO invoice_lines (
            invoice_id, source_id, source_settlement_id, source_account_line_id,
            platform_id, platform_name_snapshot, account_number_snapshot,
            start_date, closing_date, service_fee_cents, display_order,
            created_at, updated_at
        )
        SELECT source.invoice_id,
               source.id,
               source.settlement_id,
               account_line.id,
               settlement.platform_id,
               platform.name,
               account.account_number,
               account_line.start_date,
               account_line.closing_date,
               account_line.service_fee_cents,
               (
                   SELECT COUNT(*)
                   FROM settlement_account_lines AS earlier_line
                   WHERE earlier_line.settlement_id = account_line.settlement_id
                     AND earlier_line.id < account_line.id
               ),
               source.created_at,
               source.updated_at
        FROM invoice_sources AS source
        JOIN quarterly_settlements AS settlement ON settlement.id = source.settlement_id
        JOIN settlement_account_lines AS account_line
          ON account_line.settlement_id = settlement.id
        JOIN sub_accounts AS account ON account.id = account_line.account_id
        JOIN platforms AS platform ON platform.id = settlement.platform_id
        WHERE settlement.calculation_mode = 'ACCOUNT_HWM'
          AND NOT EXISTS (
              SELECT 1
              FROM invoice_lines AS existing_line
              WHERE existing_line.source_id = source.id
                AND existing_line.source_account_line_id = account_line.id
          )
        """
    )
    op.execute(
        """
        INSERT INTO invoice_lines (
            invoice_id, source_id, source_settlement_id, source_account_line_id,
            platform_id, platform_name_snapshot, account_number_snapshot,
            start_date, closing_date, service_fee_cents, display_order,
            created_at, updated_at
        )
        SELECT source.invoice_id,
               source.id,
               source.settlement_id,
               NULL,
               settlement.platform_id,
               platform.name,
               'LEGACY_GROUP_HWM',
               settlement.start_date,
               settlement.closing_date,
               settlement.service_fee_cents,
               0,
               source.created_at,
               source.updated_at
        FROM invoice_sources AS source
        JOIN quarterly_settlements AS settlement ON settlement.id = source.settlement_id
        JOIN platforms AS platform ON platform.id = settlement.platform_id
        WHERE settlement.calculation_mode = 'LEGACY_GROUP_HWM'
          AND NOT EXISTS (
              SELECT 1
              FROM invoice_lines AS existing_line
              WHERE existing_line.source_id = source.id
                AND existing_line.source_account_line_id IS NULL
          )
        """
    )

    op.execute(
        """
        INSERT INTO invoice_issue_attempts (
            invoice_id, invoice_number, status, started_at, completed_at, details
        )
        SELECT invoice.id,
               invoice.invoice_number,
               CASE WHEN invoice.lifecycle_status = 'ISSUING' THEN 'RESERVED' ELSE 'COMPLETED' END,
               COALESCE(invoice.issued_at, invoice.updated_at, invoice.created_at),
               CASE WHEN invoice.lifecycle_status = 'ISSUING' THEN NULL
                    ELSE COALESCE(invoice.issued_at, invoice.updated_at) END,
               'Migrated from 0.2.8 invoice lifecycle state'
        FROM invoices AS invoice
        WHERE invoice.invoice_number IS NOT NULL
          AND invoice.lifecycle_status IN ('ISSUING', 'ISSUED', 'VOID')
          AND NOT EXISTS (
              SELECT 1
              FROM invoice_issue_attempts AS attempt
              WHERE attempt.invoice_id = invoice.id
                AND attempt.invoice_number = invoice.invoice_number
          )
        """
    )


def _validate_backfill() -> None:
    invalid_parent_count = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM invoices
        WHERE client_id IS NULL OR year IS NULL OR quarter IS NULL OR fee_plan_id IS NULL
        """
    ).scalar_one()
    if invalid_parent_count:
        raise RuntimeError("Invoice季度归属回填不完整，请先备份并人工检查")

    source_or_line_mismatch = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM invoices AS invoice
        WHERE NOT EXISTS (
                SELECT 1 FROM invoice_sources AS source
                WHERE source.invoice_id = invoice.id
                  AND source.settlement_id = invoice.settlement_id
            )
           OR invoice.amount_cents != COALESCE((
                SELECT SUM(source.locked_amount_cents)
                FROM invoice_sources AS source
                WHERE source.invoice_id = invoice.id
            ), 0)
           OR invoice.amount_cents != COALESCE((
                SELECT SUM(line.service_fee_cents)
                FROM invoice_lines AS line
                WHERE line.invoice_id = invoice.id
            ), 0)
        """
    ).scalar_one()
    if source_or_line_mismatch:
        raise RuntimeError("Invoice来源或冻结明细金额无法与原Invoice对平，请先备份并人工检查")

    line_relationship_mismatch = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM invoice_lines AS line
        LEFT JOIN invoice_sources AS source ON source.id = line.source_id
        WHERE source.id IS NULL
           OR line.invoice_id != source.invoice_id
           OR line.source_settlement_id != source.settlement_id
           OR (line.source_account_line_id IS NOT NULL AND NOT EXISTS (
                SELECT 1
                FROM settlement_account_lines AS account_line
                WHERE account_line.id = line.source_account_line_id
                  AND account_line.settlement_id = source.settlement_id
                  AND account_line.service_fee_cents = line.service_fee_cents
           ))
        """
    ).scalar_one()
    if line_relationship_mismatch:
        raise RuntimeError("Invoice冻结明细与来源Settlement不一致，请先备份并人工检查")

    source_amount_mismatch = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM invoice_sources AS source
        WHERE source.locked_amount_cents != COALESCE((
            SELECT SUM(line.service_fee_cents)
            FROM invoice_lines AS line
            WHERE line.source_id = source.id
        ), 0)
        """
    ).scalar_one()
    if source_amount_mismatch:
        raise RuntimeError("Invoice来源金额与冻结行合计不一致，请先备份并人工检查")

    duplicate_active_source = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*) FROM (
            SELECT settlement_id
            FROM invoice_sources
            WHERE active = 1
            GROUP BY settlement_id
            HAVING COUNT(*) > 1
        )
        """
    ).scalar_one()
    if duplicate_active_source:
        raise RuntimeError("同一Settlement存在多条活动Invoice来源，请先备份并人工检查")

    duplicate_active_invoice = op.get_bind().exec_driver_sql(
        """
        SELECT COUNT(*) FROM (
            SELECT client_id, year, quarter, fee_plan_id
            FROM invoices
            WHERE lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
            GROUP BY client_id, year, quarter, fee_plan_id
            HAVING COUNT(*) > 1
        )
        """
    ).scalar_one()
    if duplicate_active_invoice:
        raise RuntimeError("同一客户季度收费计划存在多张活动Invoice，请先备份并人工检查")


def _create_partial_unique_indexes() -> None:
    op.execute("DROP INDEX IF EXISTS uq_invoice_sources_active_settlement")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invoice_sources_active_settlement
        ON invoice_sources (settlement_id)
        WHERE active = 1
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_invoices_active_client_period_plan")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_invoices_active_client_period_plan
        ON invoices (client_id, year, quarter, fee_plan_id)
        WHERE lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
        """
    )


def _create_invoice_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_invoice_source_insert_draft_only
        BEFORE INSERT ON invoice_sources
        WHEN COALESCE((
            SELECT lifecycle_status FROM invoices WHERE id = NEW.invoice_id
        ), '') != 'DRAFT'
        BEGIN
            SELECT RAISE(ABORT, 'invoice_sources_locked');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_source_update_draft_only
        BEFORE UPDATE ON invoice_sources
        WHEN NOT (
              NEW.invoice_id = OLD.invoice_id
              AND (SELECT lifecycle_status FROM invoices WHERE id = OLD.invoice_id) = 'DRAFT'
              AND (SELECT lifecycle_status FROM invoices WHERE id = NEW.invoice_id) = 'DRAFT'
          )
          AND NOT (
              (SELECT lifecycle_status FROM invoices WHERE id = OLD.invoice_id) = 'VOID'
              AND OLD.active = 1
              AND NEW.active = 0
              AND NEW.invoice_id = OLD.invoice_id
              AND NEW.settlement_id = OLD.settlement_id
              AND NEW.locked_amount_cents = OLD.locked_amount_cents
              AND NEW.created_at IS OLD.created_at
          )
        BEGIN
            SELECT RAISE(ABORT, 'invoice_sources_locked');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_source_delete_draft_only
        BEFORE DELETE ON invoice_sources
        WHEN COALESCE((
            SELECT lifecycle_status FROM invoices WHERE id = OLD.invoice_id
        ), '') != 'DRAFT'
        BEGIN
            SELECT RAISE(ABORT, 'invoice_sources_locked');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_line_insert_draft_only
        BEFORE INSERT ON invoice_lines
        WHEN COALESCE((SELECT lifecycle_status FROM invoices WHERE id = NEW.invoice_id), '') != 'DRAFT'
          OR NOT EXISTS (
              SELECT 1 FROM invoice_sources AS source
              WHERE source.id = NEW.source_id
                AND source.invoice_id = NEW.invoice_id
          )
        BEGIN
            SELECT RAISE(ABORT, 'invoice_line_source_mismatch');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_line_update_draft_only
        BEFORE UPDATE ON invoice_lines
        WHEN NOT (
            NEW.invoice_id = OLD.invoice_id
            AND (SELECT lifecycle_status FROM invoices WHERE id = OLD.invoice_id) = 'DRAFT'
            AND (SELECT lifecycle_status FROM invoices WHERE id = NEW.invoice_id) = 'DRAFT'
            AND EXISTS (
                SELECT 1 FROM invoice_sources AS source
                WHERE source.id = NEW.source_id
                  AND source.invoice_id = NEW.invoice_id
            )
        )
        BEGIN
            SELECT RAISE(ABORT, 'invoice_lines_locked');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_line_delete_draft_only
        BEFORE DELETE ON invoice_lines
        WHEN COALESCE((
            SELECT lifecycle_status FROM invoices WHERE id = OLD.invoice_id
        ), '') != 'DRAFT'
        BEGIN
            SELECT RAISE(ABORT, 'invoice_lines_locked');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_validate_issue
        BEFORE UPDATE OF lifecycle_status ON invoices
        WHEN NEW.lifecycle_status IN ('ISSUING', 'ISSUED')
          AND OLD.lifecycle_status != NEW.lifecycle_status
        BEGIN
            SELECT RAISE(ABORT, 'invoice_amount_invalid')
            WHERE NEW.amount_cents <= 0;

            SELECT RAISE(ABORT, 'invoice_source_missing')
            WHERE NOT EXISTS (
                SELECT 1 FROM invoice_sources AS source
                WHERE source.invoice_id = NEW.id AND source.active = 1
            );

            SELECT RAISE(ABORT, 'invoice_source_inactive')
            WHERE EXISTS (
                SELECT 1 FROM invoice_sources AS source
                WHERE source.invoice_id = NEW.id AND source.active != 1
            );

            SELECT RAISE(ABORT, 'invoice_anchor_source_missing')
            WHERE NOT EXISTS (
                SELECT 1 FROM invoice_sources AS source
                WHERE source.invoice_id = NEW.id
                  AND source.settlement_id = NEW.settlement_id
                  AND source.active = 1
            );

            SELECT RAISE(ABORT, 'invoice_source_mismatch')
            WHERE EXISTS (
                SELECT 1
                FROM invoice_sources AS source
                JOIN quarterly_settlements AS settlement ON settlement.id = source.settlement_id
                WHERE source.invoice_id = NEW.id
                  AND (settlement.status IS NOT 'FINALIZED'
                    OR settlement.client_id IS NOT NEW.client_id
                    OR settlement.year IS NOT NEW.year
                    OR settlement.quarter IS NOT NEW.quarter
                    OR settlement.fee_plan_id IS NOT NEW.fee_plan_id
                    OR settlement.company_id IS NOT NEW.company_id
                    OR settlement.fc_id IS NOT NEW.fc_id
                    OR settlement.service_fee_cents IS NOT source.locked_amount_cents)
            );

            SELECT RAISE(ABORT, 'invoice_source_set_incomplete')
            WHERE EXISTS (
                SELECT 1
                FROM quarterly_settlements AS settlement
                WHERE settlement.client_id = NEW.client_id
                  AND settlement.year = NEW.year
                  AND settlement.quarter = NEW.quarter
                  AND settlement.fee_plan_id = NEW.fee_plan_id
                  AND settlement.status = 'FINALIZED'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM invoice_sources AS source
                      WHERE source.invoice_id = NEW.id
                        AND source.settlement_id = settlement.id
                        AND source.active = 1
                  )
            );

            SELECT RAISE(ABORT, 'invoice_source_amount_mismatch')
            WHERE NEW.amount_cents != COALESCE((
                SELECT SUM(source.locked_amount_cents)
                FROM invoice_sources AS source
                WHERE source.invoice_id = NEW.id AND source.active = 1
            ), 0);

            SELECT RAISE(ABORT, 'invoice_line_missing')
            WHERE NOT EXISTS (
                SELECT 1 FROM invoice_lines AS line WHERE line.invoice_id = NEW.id
            );

            SELECT RAISE(ABORT, 'invoice_source_line_missing')
            WHERE EXISTS (
                SELECT 1
                FROM invoice_sources AS source
                WHERE source.invoice_id = NEW.id
                  AND source.active = 1
                  AND NOT EXISTS (
                      SELECT 1 FROM invoice_lines AS line WHERE line.source_id = source.id
                  )
            );

            SELECT RAISE(ABORT, 'invoice_line_source_mismatch')
            WHERE EXISTS (
                SELECT 1
                FROM invoice_lines AS line
                LEFT JOIN invoice_sources AS source ON source.id = line.source_id
                WHERE line.invoice_id = NEW.id
                  AND (source.id IS NULL
                    OR source.invoice_id != NEW.id
                    OR source.active != 1
                    OR line.source_settlement_id != source.settlement_id
                    OR (line.source_account_line_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1
                        FROM settlement_account_lines AS account_line
                        WHERE account_line.id = line.source_account_line_id
                          AND account_line.settlement_id = source.settlement_id
                          AND account_line.service_fee_cents = line.service_fee_cents
                    )))
            );

            SELECT RAISE(ABORT, 'invoice_line_amount_mismatch')
            WHERE NEW.amount_cents != COALESCE((
                SELECT SUM(line.service_fee_cents)
                FROM invoice_lines AS line
                WHERE line.invoice_id = NEW.id
            ), 0);

            SELECT RAISE(ABORT, 'invoice_source_line_amount_mismatch')
            WHERE EXISTS (
                SELECT 1
                FROM invoice_sources AS source
                WHERE source.invoice_id = NEW.id
                  AND source.active = 1
                  AND source.locked_amount_cents != COALESCE((
                      SELECT SUM(line.service_fee_cents)
                      FROM invoice_lines AS line
                      WHERE line.source_id = source.id
                  ), 0)
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_financial_header_update_lock
        BEFORE UPDATE OF settlement_id, client_id, year, quarter, fee_plan_id,
                         company_id, fc_id, amount_cents
        ON invoices
        WHEN (OLD.lifecycle_status != 'DRAFT' OR NEW.lifecycle_status != 'DRAFT')
          AND (NEW.settlement_id IS NOT OLD.settlement_id
            OR NEW.client_id IS NOT OLD.client_id
            OR NEW.year IS NOT OLD.year
            OR NEW.quarter IS NOT OLD.quarter
            OR NEW.fee_plan_id IS NOT OLD.fee_plan_id
            OR NEW.company_id IS NOT OLD.company_id
            OR NEW.fc_id IS NOT OLD.fc_id
            OR NEW.amount_cents IS NOT OLD.amount_cents)
        BEGIN
            SELECT RAISE(ABORT, 'invoice_financial_header_locked');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_insert_draft_only
        BEFORE INSERT ON invoices
        WHEN NEW.lifecycle_status != 'DRAFT'
          OR NEW.invoice_number IS NOT NULL
          OR NEW.issue_date IS NOT NULL
          OR NEW.due_date IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'invoice_initial_state_invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_lifecycle_transition
        BEFORE UPDATE OF lifecycle_status ON invoices
        WHEN NEW.lifecycle_status IS NOT OLD.lifecycle_status
          AND NOT (
              (OLD.lifecycle_status = 'DRAFT' AND NEW.lifecycle_status IN ('ISSUING', 'VOID'))
              OR (OLD.lifecycle_status = 'ISSUING' AND NEW.lifecycle_status IN ('ISSUED', 'DRAFT'))
              OR (OLD.lifecycle_status = 'ISSUED' AND NEW.lifecycle_status = 'VOID')
          )
        BEGIN
            SELECT RAISE(ABORT, 'invoice_lifecycle_transition_invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_issue_metadata_guard
        BEFORE UPDATE OF lifecycle_status, invoice_number, issue_date, due_date ON invoices
        WHEN (
            NEW.lifecycle_status = 'DRAFT'
            AND (NEW.invoice_number IS NOT NULL
              OR NEW.issue_date IS NOT NULL
              OR NEW.due_date IS NOT NULL)
        ) OR (
            OLD.lifecycle_status = 'DRAFT'
            AND NEW.lifecycle_status = 'ISSUING'
            AND (NEW.invoice_number IS NULL
              OR NEW.issue_date IS NULL
              OR NEW.due_date IS NULL)
        ) OR (
            NOT (OLD.lifecycle_status = 'DRAFT' AND NEW.lifecycle_status = 'ISSUING')
            AND NOT (OLD.lifecycle_status = 'ISSUING' AND NEW.lifecycle_status = 'DRAFT')
            AND (NEW.invoice_number IS NOT OLD.invoice_number
              OR NEW.issue_date IS NOT OLD.issue_date
              OR NEW.due_date IS NOT OLD.due_date)
        )
        BEGIN
            SELECT RAISE(ABORT, 'invoice_issue_metadata_invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_validate_insert
        BEFORE INSERT ON payments
        BEGIN
            SELECT RAISE(ABORT, 'payment_invoice_not_issued')
            WHERE COALESCE((
                SELECT lifecycle_status FROM invoices WHERE id = NEW.invoice_id
            ), '') != 'ISSUED';

            SELECT RAISE(ABORT, 'payment_amount_invalid')
            WHERE NEW.amount_cents <= 0;

            SELECT RAISE(ABORT, 'payment_amount_exceeds_invoice')
            WHERE COALESCE((
                    SELECT SUM(payment.amount_cents)
                    FROM payments AS payment
                    WHERE payment.invoice_id = NEW.invoice_id
                ), 0) + NEW.amount_cents > COALESCE((
                    SELECT invoice.amount_cents
                    FROM invoices AS invoice
                    WHERE invoice.id = NEW.invoice_id
                ), -1);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_block_void_with_payment
        BEFORE UPDATE OF lifecycle_status ON invoices
        WHEN NEW.lifecycle_status = 'VOID'
          AND OLD.lifecycle_status != 'VOID'
          AND EXISTS (
              SELECT 1 FROM payments AS payment WHERE payment.invoice_id = OLD.id
          )
        BEGIN
            SELECT RAISE(ABORT, 'invoice_has_payments');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_sources_deactivate_on_void
        AFTER UPDATE OF lifecycle_status ON invoices
        WHEN NEW.lifecycle_status = 'VOID' AND OLD.lifecycle_status != 'VOID'
        BEGIN
            UPDATE invoice_sources
            SET active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE invoice_id = NEW.id AND active = 1;
        END
        """
    )


def _create_settlement_void_trigger(*, aggregated_sources: bool) -> None:
    invoice_dependency = (
        """
                SELECT 1
                FROM invoice_sources AS source
                JOIN invoices AS invoice ON invoice.id = source.invoice_id
                WHERE source.settlement_id = NEW.id
                  AND source.active = 1
                  AND invoice.lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
        """
        if aggregated_sources
        else
        """
                SELECT 1 FROM invoices AS invoice
                WHERE invoice.settlement_id = NEW.id
                  AND invoice.lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
        """
    )
    op.execute(
        f"""
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
                JOIN settlement_account_lines AS later_line
                  ON later_line.account_id = current_line.account_id
                JOIN quarterly_settlements AS later ON later.id = later_line.settlement_id
                WHERE current_line.settlement_id = NEW.id
                  AND later.id != NEW.id
                  AND later.status != 'VOID'
                  AND (later.year * 4 + later.quarter) > (NEW.year * 4 + NEW.quarter)
            );
            SELECT RAISE(ABORT, 'settlement_has_active_invoice')
            WHERE EXISTS ({invoice_dependency});
        END
        """
    )


def upgrade() -> None:
    # Batch-changing invoices would otherwise invalidate the old trigger that
    # references the compatibility settlement_id anchor.
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_validate_void")
    _drop_invoice_triggers()
    _ensure_invoice_parent_columns()
    _ensure_invoice_tables()
    _backfill_invoice_ledger()
    _validate_backfill()
    _create_partial_unique_indexes()
    _create_invoice_triggers()
    _create_settlement_void_trigger(aggregated_sources=True)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_settlement_validate_void")
    _drop_invoice_triggers()
    op.execute("DROP INDEX IF EXISTS uq_invoice_sources_active_settlement")
    op.execute("DROP INDEX IF EXISTS uq_invoices_active_client_period_plan")
    op.drop_table("invoice_issue_attempts")
    op.drop_table("invoice_lines")
    op.drop_table("invoice_sources")
    with op.batch_alter_table("invoices") as batch_op:
        for column_name in sorted(_INVOICE_PARENT_COLUMNS):
            batch_op.drop_index(f"ix_invoices_{column_name}")
        batch_op.drop_column("fee_plan_id")
        batch_op.drop_column("quarter")
        batch_op.drop_column("year")
        batch_op.drop_column("client_id")
    _create_settlement_void_trigger(aggregated_sources=False)
