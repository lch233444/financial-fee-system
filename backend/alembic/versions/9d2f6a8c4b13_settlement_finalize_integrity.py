"""lock settlement finalization inputs and immutable history

Revision ID: 9d2f6a8c4b13
Revises: c4b7f1d92e60
Create Date: 2026-08-31
"""

from typing import Sequence, Union

from alembic import op


revision: str = "9d2f6a8c4b13"
down_revision: Union[str, Sequence[str], None] = "c4b7f1d92e60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TRIGGERS = (
    "trg_transactions_block_finalized_period",
    "trg_transactions_update_block_frozen_period",
    "trg_transactions_delete_block_frozen_period",
    "trg_snapshot_update_block_frozen_reference",
    "trg_snapshot_delete_block_frozen_reference",
    "trg_attachment_update_block_finalized_evidence",
    "trg_attachment_delete_block_finalized_evidence",
    "trg_statement_import_update_block_finalized_evidence",
    "trg_statement_import_delete_block_finalized_evidence",
    "trg_settlement_block_out_of_order_insert",
    "trg_settlement_account_line_order",
    "trg_settlement_account_line_update_order",
    "trg_settlement_validate_finalize",
    "trg_settlement_validate_void",
    "trg_settlement_insert_draft_only",
    "trg_settlement_lifecycle_transition",
    "trg_settlement_delete_non_draft",
    "trg_settlement_parent_financial_lock",
    "trg_settlement_line_insert_draft_only",
    "trg_settlement_line_update_draft_only",
    "trg_settlement_line_delete_draft_only",
)


def _drop_triggers() -> None:
    for trigger_name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _validate_foreign_keys() -> None:
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        affected_tables = ", ".join(sorted({str(row[0]) for row in violations}))
        raise RuntimeError(
            "数据库存在外键完整性问题（"
            f"{len(violations)}项，涉及{affected_tables}）；"
            "已停止迁移，请先使用已验证备份并人工检查"
        )


def _validate_existing_history() -> None:
    connection = op.get_bind()
    invalid_statuses = connection.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM quarterly_settlements
        WHERE status NOT IN ('DRAFT', 'FINALIZED', 'VOID')
           OR (status = 'DRAFT' AND (finalized_at IS NOT NULL OR void_reason IS NOT NULL))
           OR (status = 'FINALIZED' AND (finalized_at IS NULL OR void_reason IS NOT NULL))
           OR (status = 'VOID' AND (void_reason IS NULL OR trim(void_reason) = ''))
        """
    ).scalar_one()
    if invalid_statuses:
        raise RuntimeError(
            "数据库存在无法识别的Settlement状态或状态时间/作废理由不一致；"
            "已停止迁移，请先备份并人工检查"
        )

    duplicate_account_periods = connection.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM (
            SELECT line.account_id, settlement.year, settlement.quarter
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE settlement.status != 'VOID'
            GROUP BY line.account_id, settlement.year, settlement.quarter
            HAVING COUNT(DISTINCT settlement.id) > 1
        ) AS duplicate_period
        """
    ).scalar_one()
    if duplicate_account_periods:
        raise RuntimeError(
            "数据库存在同一Sub Account同一季度的多份非VOID Settlement；"
            "已停止迁移，请先备份并人工检查"
        )

    invalid_history = connection.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM quarterly_settlements AS settlement
        WHERE settlement.status = 'FINALIZED'
          AND settlement.calculation_mode = 'ACCOUNT_HWM'
          AND (
            NOT EXISTS (
                SELECT 1 FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.start_date IS NOT (
                SELECT MIN(line.start_date) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.closing_date IS NOT (
                SELECT MAX(line.closing_date) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.beginning_cents IS NOT (
                SELECT SUM(line.beginning_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.contribution_cents IS NOT (
                SELECT SUM(line.contribution_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.withdrawal_cents IS NOT (
                SELECT SUM(line.withdrawal_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.net_contribution_cents IS NOT (
                SELECT SUM(line.net_contribution_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.closing_cents IS NOT (
                SELECT SUM(line.closing_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.gain_loss_cents IS NOT (
                SELECT SUM(line.gain_loss_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.original_hwm_cents IS NOT (
                SELECT SUM(line.original_hwm_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.adjusted_hwm_cents IS NOT (
                SELECT SUM(line.adjusted_hwm_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.watermark_difference_cents IS NOT (
                SELECT SUM(line.watermark_difference_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.chargeable_above_hwm_cents IS NOT (
                SELECT SUM(line.chargeable_above_hwm_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.service_fee_cents IS NOT (
                SELECT SUM(line.service_fee_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.next_hwm_cents IS NOT (
                SELECT SUM(line.next_hwm_cents) FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
            )
            OR settlement.formula_version != 'HWM-2.0-ACCOUNT'
            OR CASE
                WHEN settlement.beginning_cents NOT BETWEEN -9000000000000 AND 9000000000000
                  OR settlement.net_contribution_cents NOT BETWEEN -9000000000000 AND 9000000000000
                  OR settlement.gain_loss_cents NOT BETWEEN -9000000000000 AND 9000000000000
                  THEN 1
                WHEN settlement.beginning_cents + settlement.net_contribution_cents = 0
                  THEN 1
                WHEN settlement.period_rate_ppm IS NOT (
                    CASE
                      WHEN (settlement.gain_loss_cents < 0)
                           != (settlement.beginning_cents
                               + settlement.net_contribution_cents < 0)
                      THEN -1 ELSE 1
                    END
                    * CAST((
                        abs(settlement.gain_loss_cents) * 1000000
                        + CAST(abs(
                            settlement.beginning_cents
                            + settlement.net_contribution_cents
                          ) / 2 AS INTEGER)
                      ) / abs(
                        settlement.beginning_cents
                        + settlement.net_contribution_cents
                      ) AS INTEGER)
                ) THEN 1
                ELSE 0
              END = 1
            OR EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                LEFT JOIN balance_snapshots AS beginning ON beginning.id = line.beginning_snapshot_id
                LEFT JOIN balance_snapshots AS closing ON closing.id = line.closing_snapshot_id
                LEFT JOIN settlement_account_lines AS previous_line
                  ON previous_line.id = line.previous_line_id
                WHERE line.settlement_id = settlement.id
                  AND (
                    line.start_date IS NULL
                    OR line.closing_date IS NULL
                    OR line.days IS NULL
                    OR line.start_date > line.closing_date
                    OR line.days != CAST(
                        julianday(line.closing_date) - julianday(line.start_date) AS INTEGER
                    ) + 1
                    OR line.beginning_cents IS NULL
                    OR line.closing_cents IS NULL
                    OR line.contribution_cents IS NULL
                    OR line.withdrawal_cents IS NULL
                    OR line.net_contribution_cents IS NULL
                    OR line.gain_loss_cents IS NULL
                    OR line.period_rate_ppm IS NULL
                    OR line.original_hwm_cents IS NULL
                    OR line.adjusted_hwm_cents IS NULL
                    OR line.watermark_difference_cents IS NULL
                    OR line.chargeable_above_hwm_cents IS NULL
                    OR line.service_fee_cents IS NULL
                    OR line.next_hwm_cents IS NULL
                    OR line.fee_rate_bps IS NULL
                    OR line.formula_version IS NULL
                    OR line.fee_rate_bps != settlement.fee_rate_bps
                    OR line.formula_version != settlement.formula_version
                    OR line.formula_version != 'HWM-2.0-ACCOUNT'
                    OR line.fee_rate_bps < 0
                    OR line.fee_rate_bps > 10000
                    OR line.net_contribution_cents
                       != line.contribution_cents - line.withdrawal_cents
                    OR line.gain_loss_cents
                       != line.closing_cents - line.beginning_cents
                          - line.net_contribution_cents
                    OR line.adjusted_hwm_cents
                       != line.original_hwm_cents + line.net_contribution_cents
                    OR line.watermark_difference_cents
                       != line.closing_cents - line.adjusted_hwm_cents
                    OR line.chargeable_above_hwm_cents
                       != CASE
                            WHEN line.watermark_difference_cents > 0
                            THEN line.watermark_difference_cents
                            ELSE 0
                          END
                    OR line.service_fee_cents
                       != CAST(line.chargeable_above_hwm_cents / 10000 AS INTEGER)
                          * line.fee_rate_bps
                          + CAST((
                              (line.chargeable_above_hwm_cents % 10000)
                              * line.fee_rate_bps + 5000
                            ) / 10000 AS INTEGER)
                    OR line.next_hwm_cents
                       != CASE
                            WHEN line.adjusted_hwm_cents > line.closing_cents
                            THEN line.adjusted_hwm_cents
                            ELSE line.closing_cents
                          END
                    OR CASE
                        WHEN line.beginning_cents NOT BETWEEN -9000000000000 AND 9000000000000
                          OR line.net_contribution_cents NOT BETWEEN -9000000000000 AND 9000000000000
                          OR line.gain_loss_cents NOT BETWEEN -9000000000000 AND 9000000000000
                          THEN 1
                        WHEN line.beginning_cents + line.net_contribution_cents = 0
                          THEN 1
                        WHEN line.period_rate_ppm IS NOT (
                            CASE
                              WHEN (line.gain_loss_cents < 0)
                                   != (line.beginning_cents
                                       + line.net_contribution_cents < 0)
                              THEN -1 ELSE 1
                            END
                            * CAST((
                                abs(line.gain_loss_cents) * 1000000
                                + CAST(abs(
                                    line.beginning_cents + line.net_contribution_cents
                                  ) / 2 AS INTEGER)
                              ) / abs(
                                line.beginning_cents + line.net_contribution_cents
                              ) AS INTEGER)
                        ) THEN 1
                        ELSE 0
                      END = 1
                    OR beginning.id IS NULL
                    OR closing.id IS NULL
                    OR beginning.account_id != line.account_id
                    OR closing.account_id != line.account_id
                    OR beginning.total_balance_cents != line.beginning_cents
                    OR closing.total_balance_cents != line.closing_cents
                    OR closing.as_of_date != line.closing_date
                    OR closing.eligible_for_closing != 1
                    OR line.previous_line_id IS NOT (
                        SELECT candidate_line.id
                        FROM settlement_account_lines AS candidate_line
                        JOIN quarterly_settlements AS candidate
                          ON candidate.id = candidate_line.settlement_id
                        WHERE candidate_line.account_id = line.account_id
                          AND candidate.calculation_mode = 'ACCOUNT_HWM'
                          AND candidate.status = 'FINALIZED'
                          AND (candidate.year * 4 + candidate.quarter)
                              < (settlement.year * 4 + settlement.quarter)
                        ORDER BY candidate.year DESC, candidate.quarter DESC
                        LIMIT 1
                    )
                    OR (line.previous_line_id IS NULL
                        AND beginning.as_of_date != line.start_date)
                    OR (line.previous_line_id IS NOT NULL AND (
                        line.beginning_snapshot_id != previous_line.closing_snapshot_id
                        OR line.original_hwm_cents != previous_line.next_hwm_cents
                    ))
                )
            )
            OR EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                WHERE line.settlement_id = settlement.id
                  AND (
                    line.contribution_cents IS NOT COALESCE((
                        SELECT SUM(transaction_record.amount_cents)
                        FROM transactions AS transaction_record
                        WHERE transaction_record.account_id = line.account_id
                          AND transaction_record.transaction_type = 'CONTRIBUTION'
                          AND transaction_record.transaction_date > line.start_date
                          AND transaction_record.transaction_date <= line.closing_date
                    ), 0)
                    OR line.withdrawal_cents IS NOT COALESCE((
                        SELECT SUM(transaction_record.amount_cents)
                        FROM transactions AS transaction_record
                        WHERE transaction_record.account_id = line.account_id
                          AND transaction_record.transaction_type = 'WITHDRAWAL'
                          AND transaction_record.transaction_date > line.start_date
                          AND transaction_record.transaction_date <= line.closing_date
                    ), 0)
                  )
            )
          )
        """
    ).scalar_one()
    if invalid_history:
        raise RuntimeError(
            "数据库存在与账户明细、Snapshot或Transaction不一致的Finalized Settlement；"
            "已停止迁移，请先备份并人工检查"
        )

    invalid_evidence_associations = connection.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM settlement_account_lines AS line
        JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
        WHERE settlement.status = 'FINALIZED'
          AND settlement.calculation_mode = 'ACCOUNT_HWM'
          AND (
            EXISTS (
                SELECT 1
                FROM balance_snapshots AS snapshot
                WHERE snapshot.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
                  AND (
                    (snapshot.statement_import_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1
                        FROM statement_imports AS statement
                        WHERE statement.id = snapshot.statement_import_id
                          AND statement.status = 'CONFIRMED'
                          AND statement.confirmed_account_id = snapshot.account_id
                          AND statement.confirmed_snapshot_id = snapshot.id
                    ))
                    OR
                    (snapshot.statement_import_id IS NULL AND NOT EXISTS (
                        SELECT 1
                        FROM attachments AS attachment
                        WHERE attachment.entity_type = 'SNAPSHOT'
                          AND attachment.entity_id = snapshot.id
                    ))
                  )
            )
            OR EXISTS (
                SELECT 1
                FROM transactions AS transaction_record
                WHERE transaction_record.account_id = line.account_id
                  AND transaction_record.transaction_date > line.start_date
                  AND transaction_record.transaction_date <= line.closing_date
                  AND NOT EXISTS (
                    SELECT 1
                    FROM attachments AS attachment
                    WHERE attachment.entity_type = 'TRANSACTION'
                      AND attachment.entity_id = transaction_record.id
                  )
            )
          )
        """
    ).scalar_one()
    if invalid_evidence_associations:
        raise RuntimeError(
            "数据库存在缺失或错绑凭证关联的Finalized Settlement；"
            "已停止迁移，请先备份并人工检查"
        )


def _create_transaction_and_snapshot_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_transactions_block_finalized_period
        BEFORE INSERT ON transactions
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE line.account_id = NEW.account_id
              AND settlement.status = 'FINALIZED'
              AND NEW.transaction_date >= line.start_date
              AND NEW.transaction_date <= line.closing_date
        )
        BEGIN
            SELECT RAISE(ABORT, 'transaction_in_finalized_period');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_transactions_update_block_frozen_period
        BEFORE UPDATE ON transactions
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE settlement.status = 'FINALIZED'
              AND (
                (line.account_id = OLD.account_id
                 AND OLD.transaction_date >= line.start_date
                 AND OLD.transaction_date <= line.closing_date)
                OR
                (line.account_id = NEW.account_id
                 AND NEW.transaction_date >= line.start_date
                 AND NEW.transaction_date <= line.closing_date)
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'transaction_update_in_frozen_settlement_period');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_transactions_delete_block_frozen_period
        BEFORE DELETE ON transactions
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE line.account_id = OLD.account_id
              AND settlement.status = 'FINALIZED'
              AND OLD.transaction_date >= line.start_date
              AND OLD.transaction_date <= line.closing_date
        )
        BEGIN
            SELECT RAISE(ABORT, 'transaction_delete_in_frozen_settlement_period');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_snapshot_update_block_frozen_reference
        BEFORE UPDATE ON balance_snapshots
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE settlement.status = 'FINALIZED'
              AND OLD.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
        )
        BEGIN
            SELECT RAISE(ABORT, 'snapshot_used_by_frozen_settlement');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_snapshot_delete_block_frozen_reference
        BEFORE DELETE ON balance_snapshots
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE settlement.status = 'FINALIZED'
              AND OLD.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
        )
        BEGIN
            SELECT RAISE(ABORT, 'snapshot_used_by_frozen_settlement');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_attachment_update_block_finalized_evidence
        BEFORE UPDATE ON attachments
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE settlement.status = 'FINALIZED'
              AND (
                (OLD.entity_type = 'SNAPSHOT'
                 AND OLD.entity_id IN (line.beginning_snapshot_id, line.closing_snapshot_id))
                OR
                (NEW.entity_type = 'SNAPSHOT'
                 AND NEW.entity_id IN (line.beginning_snapshot_id, line.closing_snapshot_id))
                OR
                (OLD.entity_type = 'TRANSACTION' AND EXISTS (
                    SELECT 1 FROM transactions AS transaction_record
                    WHERE transaction_record.id = OLD.entity_id
                      AND transaction_record.account_id = line.account_id
                      AND transaction_record.transaction_date > line.start_date
                      AND transaction_record.transaction_date <= line.closing_date
                ))
                OR
                (NEW.entity_type = 'TRANSACTION' AND EXISTS (
                    SELECT 1 FROM transactions AS transaction_record
                    WHERE transaction_record.id = NEW.entity_id
                      AND transaction_record.account_id = line.account_id
                      AND transaction_record.transaction_date > line.start_date
                      AND transaction_record.transaction_date <= line.closing_date
                ))
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'attachment_used_by_finalized_settlement');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_attachment_delete_block_finalized_evidence
        BEFORE DELETE ON attachments
        WHEN EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE settlement.status = 'FINALIZED'
              AND (
                (OLD.entity_type = 'SNAPSHOT'
                 AND OLD.entity_id IN (line.beginning_snapshot_id, line.closing_snapshot_id))
                OR
                (OLD.entity_type = 'TRANSACTION' AND EXISTS (
                    SELECT 1 FROM transactions AS transaction_record
                    WHERE transaction_record.id = OLD.entity_id
                      AND transaction_record.account_id = line.account_id
                      AND transaction_record.transaction_date > line.start_date
                      AND transaction_record.transaction_date <= line.closing_date
                ))
              )
        )
        BEGIN
            SELECT RAISE(ABORT, 'attachment_used_by_finalized_settlement');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_statement_import_update_block_finalized_evidence
        BEFORE UPDATE ON statement_imports
        WHEN EXISTS (
            SELECT 1
            FROM balance_snapshots AS snapshot
            JOIN settlement_account_lines AS line
              ON snapshot.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE snapshot.statement_import_id = OLD.id
              AND settlement.status = 'FINALIZED'
        )
        BEGIN
            SELECT RAISE(ABORT, 'statement_import_used_by_finalized_settlement');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_statement_import_delete_block_finalized_evidence
        BEFORE DELETE ON statement_imports
        WHEN EXISTS (
            SELECT 1
            FROM balance_snapshots AS snapshot
            JOIN settlement_account_lines AS line
              ON snapshot.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE snapshot.statement_import_id = OLD.id
              AND settlement.status = 'FINALIZED'
        )
        BEGIN
            SELECT RAISE(ABORT, 'statement_import_used_by_finalized_settlement');
        END
        """
    )


def _create_settlement_state_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_settlement_insert_draft_only
        BEFORE INSERT ON quarterly_settlements
        WHEN NEW.status != 'DRAFT'
          OR NEW.finalized_at IS NOT NULL
          OR NEW.void_reason IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'settlement_insert_must_be_draft');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_lifecycle_transition
        BEFORE UPDATE OF status, finalized_at, void_reason ON quarterly_settlements
        WHEN (
            NEW.status != OLD.status
            AND NOT (
              (OLD.status = 'DRAFT' AND NEW.status = 'FINALIZED')
              OR (OLD.status = 'FINALIZED' AND NEW.status = 'VOID')
            )
          )
          OR (NEW.status = 'DRAFT'
              AND (NEW.finalized_at IS NOT NULL OR NEW.void_reason IS NOT NULL))
          OR (NEW.status = 'FINALIZED' AND NEW.void_reason IS NOT NULL)
        BEGIN
            SELECT RAISE(ABORT, 'settlement_lifecycle_transition_invalid');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_delete_non_draft
        BEFORE DELETE ON quarterly_settlements
        WHEN OLD.status IN ('FINALIZED', 'VOID')
        BEGIN
            SELECT RAISE(ABORT, 'settlement_history_cannot_be_deleted');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_parent_financial_lock
        BEFORE UPDATE ON quarterly_settlements
        WHEN OLD.status IN ('FINALIZED', 'VOID')
          AND (
            NEW.id IS NOT OLD.id
            OR NEW.client_id IS NOT OLD.client_id
            OR NEW.platform_id IS NOT OLD.platform_id
            OR NEW.fee_plan_id IS NOT OLD.fee_plan_id
            OR NEW.company_id IS NOT OLD.company_id
            OR NEW.fc_id IS NOT OLD.fc_id
            OR NEW.previous_settlement_id IS NOT OLD.previous_settlement_id
            OR NEW.year IS NOT OLD.year
            OR NEW.quarter IS NOT OLD.quarter
            OR NEW.start_date IS NOT OLD.start_date
            OR NEW.closing_date IS NOT OLD.closing_date
            OR NEW.days IS NOT OLD.days
            OR NEW.beginning_cents IS NOT OLD.beginning_cents
            OR NEW.contribution_cents IS NOT OLD.contribution_cents
            OR NEW.withdrawal_cents IS NOT OLD.withdrawal_cents
            OR NEW.net_contribution_cents IS NOT OLD.net_contribution_cents
            OR NEW.closing_cents IS NOT OLD.closing_cents
            OR NEW.gain_loss_cents IS NOT OLD.gain_loss_cents
            OR NEW.period_rate_ppm IS NOT OLD.period_rate_ppm
            OR NEW.original_hwm_cents IS NOT OLD.original_hwm_cents
            OR NEW.adjusted_hwm_cents IS NOT OLD.adjusted_hwm_cents
            OR NEW.watermark_difference_cents IS NOT OLD.watermark_difference_cents
            OR NEW.chargeable_above_hwm_cents IS NOT OLD.chargeable_above_hwm_cents
            OR NEW.service_fee_cents IS NOT OLD.service_fee_cents
            OR NEW.next_hwm_cents IS NOT OLD.next_hwm_cents
            OR NEW.fee_rate_bps IS NOT OLD.fee_rate_bps
            OR NEW.formula_version IS NOT OLD.formula_version
            OR NEW.calculation_mode IS NOT OLD.calculation_mode
            OR NEW.finalized_at IS NOT OLD.finalized_at
            OR NEW.created_at IS NOT OLD.created_at
            OR (
              NEW.void_reason IS NOT OLD.void_reason
              AND NOT (
                OLD.status = 'FINALIZED'
                AND NEW.status = 'VOID'
                AND NEW.void_reason IS NOT NULL
                AND trim(NEW.void_reason) != ''
              )
            )
          )
        BEGIN
            SELECT RAISE(ABORT, 'settlement_financial_history_immutable');
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


def _create_settlement_line_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_settlement_line_insert_draft_only
        BEFORE INSERT ON settlement_account_lines
        WHEN COALESCE((
            SELECT settlement.status FROM quarterly_settlements AS settlement
            WHERE settlement.id = NEW.settlement_id
        ), '') != 'DRAFT'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_line_parent_must_be_draft');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_line_update_draft_only
        BEFORE UPDATE ON settlement_account_lines
        WHEN COALESCE((
            SELECT settlement.status FROM quarterly_settlements AS settlement
            WHERE settlement.id = OLD.settlement_id
        ), '') != 'DRAFT'
          OR COALESCE((
            SELECT settlement.status FROM quarterly_settlements AS settlement
            WHERE settlement.id = NEW.settlement_id
        ), '') != 'DRAFT'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_line_history_immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_line_delete_draft_only
        BEFORE DELETE ON settlement_account_lines
        WHEN EXISTS (
            SELECT 1 FROM quarterly_settlements AS settlement
            WHERE settlement.id = OLD.settlement_id
              AND settlement.status != 'DRAFT'
        )
        BEGIN
            SELECT RAISE(ABORT, 'settlement_line_history_immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_account_line_order
        BEFORE INSERT ON settlement_account_lines
        WHEN (SELECT calculation_mode FROM quarterly_settlements WHERE id = NEW.settlement_id) = 'ACCOUNT_HWM'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_account_ownership_invalid')
            WHERE NOT EXISTS (
                SELECT 1
                FROM quarterly_settlements AS settlement
                JOIN sub_accounts AS account ON account.id = NEW.account_id
                WHERE settlement.id = NEW.settlement_id
                  AND account.client_id = settlement.client_id
                  AND account.platform_id = settlement.platform_id
                  AND account.fee_plan_id = settlement.fee_plan_id
            );
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
            SELECT RAISE(ABORT, 'settlement_account_period_duplicate')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS other_line
                JOIN quarterly_settlements AS other ON other.id = other_line.settlement_id
                JOIN quarterly_settlements AS current ON current.id = NEW.settlement_id
                WHERE other_line.account_id = NEW.account_id
                  AND other.id != current.id
                  AND other.status != 'VOID'
                  AND other.year = current.year
                  AND other.quarter = current.quarter
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_account_line_update_order
        BEFORE UPDATE OF settlement_id, account_id ON settlement_account_lines
        WHEN (SELECT calculation_mode FROM quarterly_settlements WHERE id = NEW.settlement_id) = 'ACCOUNT_HWM'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_account_ownership_invalid')
            WHERE NOT EXISTS (
                SELECT 1
                FROM quarterly_settlements AS settlement
                JOIN sub_accounts AS account ON account.id = NEW.account_id
                WHERE settlement.id = NEW.settlement_id
                  AND account.client_id = settlement.client_id
                  AND account.platform_id = settlement.platform_id
                  AND account.fee_plan_id = settlement.fee_plan_id
            );
            SELECT RAISE(ABORT, 'settlement_account_out_of_order')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS later_line
                JOIN quarterly_settlements AS later ON later.id = later_line.settlement_id
                JOIN quarterly_settlements AS current ON current.id = NEW.settlement_id
                WHERE later_line.account_id = NEW.account_id
                  AND later_line.id != OLD.id
                  AND later.id != current.id
                  AND later.status != 'VOID'
                  AND (later.year * 4 + later.quarter) > (current.year * 4 + current.quarter)
            );
            SELECT RAISE(ABORT, 'settlement_account_period_duplicate')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS other_line
                JOIN quarterly_settlements AS other ON other.id = other_line.settlement_id
                JOIN quarterly_settlements AS current ON current.id = NEW.settlement_id
                WHERE other_line.account_id = NEW.account_id
                  AND other_line.id != OLD.id
                  AND other.id != current.id
                  AND other.status != 'VOID'
                  AND other.year = current.year
                  AND other.quarter = current.quarter
            );
        END
        """
    )


def _create_finalize_trigger() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_settlement_validate_finalize
        BEFORE UPDATE OF status ON quarterly_settlements
        WHEN NEW.status = 'FINALIZED' AND OLD.status != 'FINALIZED'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_finalize_metadata_invalid')
            WHERE NEW.finalized_at IS NULL OR NEW.void_reason IS NOT NULL;

            SELECT RAISE(ABORT, 'legacy_draft_requires_recalculation')
            WHERE NEW.calculation_mode != 'ACCOUNT_HWM';

            SELECT RAISE(ABORT, 'settlement_formula_version_invalid')
            WHERE NEW.formula_version != 'HWM-2.0-ACCOUNT';

            SELECT RAISE(ABORT, 'settlement_account_period_duplicate')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS current_line
                JOIN settlement_account_lines AS other_line
                  ON other_line.account_id = current_line.account_id
                JOIN quarterly_settlements AS other ON other.id = other_line.settlement_id
                WHERE current_line.settlement_id = NEW.id
                  AND other.id != NEW.id
                  AND other.status != 'VOID'
                  AND other.year = NEW.year
                  AND other.quarter = NEW.quarter
            );

            SELECT RAISE(ABORT, 'settlement_owner_or_plan_invalid')
            WHERE NOT EXISTS (
                SELECT 1
                FROM clients AS client
                JOIN fcs AS fc ON fc.id = client.fc_id
                JOIN fee_plans AS plan ON plan.id = NEW.fee_plan_id
                WHERE client.id = NEW.client_id
                  AND client.status = 'ACTIVE'
                  AND NEW.company_id = client.company_id
                  AND NEW.fc_id = client.fc_id
                  AND fc.company_id = client.company_id
                  AND plan.company_id = client.company_id
                  AND NEW.fee_rate_bps = plan.fee_rate_bps
            );

            SELECT RAISE(ABORT, 'settlement_account_line_incomplete')
            WHERE NOT EXISTS (
                SELECT 1 FROM settlement_account_lines AS line
                WHERE line.settlement_id = NEW.id
            ) OR EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                LEFT JOIN sub_accounts AS account ON account.id = line.account_id
                LEFT JOIN balance_snapshots AS beginning ON beginning.id = line.beginning_snapshot_id
                LEFT JOIN balance_snapshots AS closing ON closing.id = line.closing_snapshot_id
                LEFT JOIN settlement_account_lines AS prior_line ON prior_line.id = line.previous_line_id
                WHERE line.settlement_id = NEW.id
                  AND (
                    line.start_date IS NULL
                    OR line.closing_date IS NULL
                    OR line.days IS NULL
                    OR line.start_date > line.closing_date
                    OR line.days != CAST(
                        julianday(line.closing_date) - julianday(line.start_date) AS INTEGER
                    ) + 1
                    OR line.beginning_cents IS NULL
                    OR line.closing_cents IS NULL
                    OR line.contribution_cents IS NULL
                    OR line.withdrawal_cents IS NULL
                    OR line.net_contribution_cents IS NULL
                    OR line.gain_loss_cents IS NULL
                    OR line.period_rate_ppm IS NULL
                    OR line.original_hwm_cents IS NULL
                    OR line.adjusted_hwm_cents IS NULL
                    OR line.watermark_difference_cents IS NULL
                    OR line.chargeable_above_hwm_cents IS NULL
                    OR line.service_fee_cents IS NULL
                    OR line.next_hwm_cents IS NULL
                    OR line.fee_rate_bps IS NULL
                    OR line.formula_version IS NULL
                    OR line.fee_rate_bps < 0
                    OR line.fee_rate_bps > 10000
                    OR line.net_contribution_cents
                       != line.contribution_cents - line.withdrawal_cents
                    OR line.gain_loss_cents
                       != line.closing_cents - line.beginning_cents
                          - line.net_contribution_cents
                    OR line.adjusted_hwm_cents
                       != line.original_hwm_cents + line.net_contribution_cents
                    OR line.watermark_difference_cents
                       != line.closing_cents - line.adjusted_hwm_cents
                    OR line.chargeable_above_hwm_cents
                       != CASE
                            WHEN line.watermark_difference_cents > 0
                            THEN line.watermark_difference_cents
                            ELSE 0
                          END
                    OR line.service_fee_cents
                       != CAST(line.chargeable_above_hwm_cents / 10000 AS INTEGER)
                          * line.fee_rate_bps
                          + CAST((
                              (line.chargeable_above_hwm_cents % 10000)
                              * line.fee_rate_bps + 5000
                            ) / 10000 AS INTEGER)
                    OR line.next_hwm_cents
                       != CASE
                            WHEN line.adjusted_hwm_cents > line.closing_cents
                            THEN line.adjusted_hwm_cents
                            ELSE line.closing_cents
                          END
                    OR line.formula_version != 'HWM-2.0-ACCOUNT'
                    OR CASE
                        WHEN line.beginning_cents NOT BETWEEN -9000000000000 AND 9000000000000
                          OR line.net_contribution_cents NOT BETWEEN -9000000000000 AND 9000000000000
                          OR line.gain_loss_cents NOT BETWEEN -9000000000000 AND 9000000000000
                          THEN 1
                        WHEN line.beginning_cents + line.net_contribution_cents = 0
                          THEN 1
                        WHEN line.period_rate_ppm IS NOT (
                            CASE
                              WHEN (line.gain_loss_cents < 0)
                                   != (line.beginning_cents
                                       + line.net_contribution_cents < 0)
                              THEN -1 ELSE 1
                            END
                            * CAST((
                                abs(line.gain_loss_cents) * 1000000
                                + CAST(abs(
                                    line.beginning_cents + line.net_contribution_cents
                                  ) / 2 AS INTEGER)
                              ) / abs(
                                line.beginning_cents + line.net_contribution_cents
                              ) AS INTEGER)
                        ) THEN 1
                        ELSE 0
                      END = 1
                    OR account.id IS NULL
                    OR account.status != 'ACTIVE'
                    OR account.start_date IS NULL
                    OR account.client_id != NEW.client_id
                    OR account.platform_id != NEW.platform_id
                    OR account.fee_plan_id != NEW.fee_plan_id
                    OR line.start_date < account.start_date
                    OR (account.end_date IS NOT NULL AND line.closing_date > account.end_date)
                    OR beginning.id IS NULL
                    OR closing.id IS NULL
                    OR beginning.account_id != line.account_id
                    OR closing.account_id != line.account_id
                    OR beginning.total_balance_cents != line.beginning_cents
                    OR closing.total_balance_cents != line.closing_cents
                    OR closing.as_of_date != line.closing_date
                    OR closing.eligible_for_closing != 1
                    OR line.fee_rate_bps != NEW.fee_rate_bps
                    OR line.formula_version != NEW.formula_version
                    OR (line.previous_line_id IS NULL AND beginning.as_of_date != line.start_date)
                    OR (line.previous_line_id IS NOT NULL
                        AND line.beginning_snapshot_id != prior_line.closing_snapshot_id)
                  )
            );

            SELECT RAISE(ABORT, 'settlement_parent_aggregate_mismatch')
            WHERE NEW.start_date IS NOT (
                    SELECT MIN(line.start_date) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.closing_date IS NOT (
                    SELECT MAX(line.closing_date) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.days != CAST(julianday(NEW.closing_date) - julianday(NEW.start_date) AS INTEGER) + 1
               OR NEW.beginning_cents IS NOT (
                    SELECT SUM(line.beginning_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.contribution_cents IS NOT (
                    SELECT SUM(line.contribution_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.withdrawal_cents IS NOT (
                    SELECT SUM(line.withdrawal_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.net_contribution_cents IS NOT (
                    SELECT SUM(line.net_contribution_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.closing_cents IS NOT (
                    SELECT SUM(line.closing_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.gain_loss_cents IS NOT (
                    SELECT SUM(line.gain_loss_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.original_hwm_cents IS NOT (
                    SELECT SUM(line.original_hwm_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.adjusted_hwm_cents IS NOT (
                    SELECT SUM(line.adjusted_hwm_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.watermark_difference_cents IS NOT (
                    SELECT SUM(line.watermark_difference_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.chargeable_above_hwm_cents IS NOT (
                    SELECT SUM(line.chargeable_above_hwm_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.service_fee_cents IS NOT (
                    SELECT SUM(line.service_fee_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR NEW.next_hwm_cents IS NOT (
                    SELECT SUM(line.next_hwm_cents) FROM settlement_account_lines AS line
                    WHERE line.settlement_id = NEW.id
                )
               OR CASE
                    WHEN NEW.beginning_cents NOT BETWEEN -9000000000000 AND 9000000000000
                      OR NEW.net_contribution_cents NOT BETWEEN -9000000000000 AND 9000000000000
                      OR NEW.gain_loss_cents NOT BETWEEN -9000000000000 AND 9000000000000
                      THEN 1
                    WHEN NEW.beginning_cents + NEW.net_contribution_cents = 0
                      THEN 1
                    WHEN NEW.period_rate_ppm IS NOT (
                        CASE
                          WHEN (NEW.gain_loss_cents < 0)
                               != (NEW.beginning_cents + NEW.net_contribution_cents < 0)
                          THEN -1 ELSE 1
                        END
                        * CAST((
                            abs(NEW.gain_loss_cents) * 1000000
                            + CAST(abs(
                                NEW.beginning_cents + NEW.net_contribution_cents
                              ) / 2 AS INTEGER)
                          ) / abs(
                            NEW.beginning_cents + NEW.net_contribution_cents
                          ) AS INTEGER)
                    ) THEN 1
                    ELSE 0
                  END = 1;

            SELECT RAISE(ABORT, 'settlement_transaction_totals_changed')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                WHERE line.settlement_id = NEW.id
                  AND (
                    line.contribution_cents IS NOT COALESCE((
                        SELECT SUM(transaction_record.amount_cents)
                        FROM transactions AS transaction_record
                        WHERE transaction_record.account_id = line.account_id
                          AND transaction_record.transaction_type = 'CONTRIBUTION'
                          AND transaction_record.transaction_date > line.start_date
                          AND transaction_record.transaction_date <= line.closing_date
                    ), 0)
                    OR line.withdrawal_cents IS NOT COALESCE((
                        SELECT SUM(transaction_record.amount_cents)
                        FROM transactions AS transaction_record
                        WHERE transaction_record.account_id = line.account_id
                          AND transaction_record.transaction_type = 'WITHDRAWAL'
                          AND transaction_record.transaction_date > line.start_date
                          AND transaction_record.transaction_date <= line.closing_date
                    ), 0)
                  )
            );

            SELECT RAISE(ABORT, 'settlement_account_has_later_dependency')
            WHERE EXISTS (
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
            WHERE EXISTS (
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
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                JOIN settlement_account_lines AS prior_line ON prior_line.id = line.previous_line_id
                WHERE line.settlement_id = NEW.id
                  AND line.original_hwm_cents != prior_line.next_hwm_cents
            );

            SELECT RAISE(ABORT, 'settlement_snapshot_evidence_missing')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                JOIN balance_snapshots AS snapshot
                  ON snapshot.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
                WHERE line.settlement_id = NEW.id
                  AND (
                    (snapshot.statement_import_id IS NOT NULL AND NOT EXISTS (
                        SELECT 1 FROM statement_imports AS statement
                        WHERE statement.id = snapshot.statement_import_id
                          AND statement.status = 'CONFIRMED'
                          AND statement.confirmed_account_id = snapshot.account_id
                          AND statement.confirmed_snapshot_id = snapshot.id
                    ))
                    OR
                    (snapshot.statement_import_id IS NULL AND NOT EXISTS (
                        SELECT 1 FROM attachments AS attachment
                        WHERE attachment.entity_type = 'SNAPSHOT'
                          AND attachment.entity_id = snapshot.id
                    ))
                  )
            );

            SELECT RAISE(ABORT, 'settlement_transaction_evidence_missing')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                JOIN transactions AS transaction_record ON transaction_record.account_id = line.account_id
                WHERE line.settlement_id = NEW.id
                  AND transaction_record.transaction_date > line.start_date
                  AND transaction_record.transaction_date <= line.closing_date
                  AND NOT EXISTS (
                    SELECT 1 FROM attachments AS attachment
                    WHERE attachment.entity_type = 'TRANSACTION'
                      AND attachment.entity_id = transaction_record.id
                  )
            );
        END
        """
    )


def _create_void_trigger() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_settlement_validate_void
        BEFORE UPDATE OF status ON quarterly_settlements
        WHEN NEW.status = 'VOID' AND OLD.status != 'VOID'
        BEGIN
            SELECT RAISE(ABORT, 'settlement_void_reason_required')
            WHERE NEW.void_reason IS NULL OR trim(NEW.void_reason) = '';

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
                SELECT 1
                FROM invoice_sources AS source
                JOIN invoices AS invoice ON invoice.id = source.invoice_id
                WHERE source.settlement_id = NEW.id
                  AND source.active = 1
                  AND invoice.lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
            );
        END
        """
    )


def upgrade() -> None:
    _validate_foreign_keys()
    _validate_existing_history()
    _drop_triggers()
    _create_transaction_and_snapshot_triggers()
    _create_settlement_state_triggers()
    _create_settlement_line_triggers()
    _create_finalize_trigger()
    _create_void_trigger()


def downgrade() -> None:
    raise RuntimeError(
        "该财务完整性迁移不允许原地降级；请使用升级前已验证的完整备份回滚"
    )
