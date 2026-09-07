"""Protect actual cash periods, require consecutive quarters, and allow late platforms.

Revision ID: e8b2c6d91a04
Revises: d4f8a1c73b29
"""

from alembic import op

revision = "e8b2c6d91a04"
down_revision = "d4f8a1c73b29"
branch_labels = None
depends_on = None

CONSECUTIVE_QUARTER = """
            SELECT RAISE(ABORT, 'settlement_missing_previous_quarter')
            WHERE EXISTS (
                SELECT 1
                FROM settlement_account_lines AS line
                JOIN settlement_account_lines AS prior_line ON prior_line.account_id = line.account_id
                JOIN quarterly_settlements AS prior ON prior.id = prior_line.settlement_id
                WHERE line.settlement_id = NEW.id
                  AND prior.status = 'FINALIZED'
                  AND prior.calculation_mode = 'ACCOUNT_HWM'
                  AND prior.year * 4 + prior.quarter < NEW.year * 4 + NEW.quarter
                  AND NOT EXISTS (
                      SELECT 1 FROM settlement_account_lines AS immediate_line
                      JOIN quarterly_settlements AS immediate ON immediate.id = immediate_line.settlement_id
                      WHERE immediate_line.account_id = line.account_id
                        AND immediate.status = 'FINALIZED'
                        AND immediate.calculation_mode = 'ACCOUNT_HWM'
                        AND immediate.year * 4 + immediate.quarter = NEW.year * 4 + NEW.quarter - 1
                  )
            );

"""

LINEAGE_CTE = """
                      WITH RECURSIVE lineage(replacement_source_id, settlement_id) AS (
                          SELECT replacement_source.id, settlement.replaces_settlement_id
                          FROM invoice_sources AS replacement_source
                          JOIN quarterly_settlements AS settlement ON settlement.id = replacement_source.settlement_id
                          WHERE replacement_source.invoice_id = NEW.replacement_invoice_id
                            AND replacement_source.active = 1
                            AND settlement.replaces_settlement_id IS NOT NULL
                          UNION
                          SELECT lineage.replacement_source_id, settlement.replaces_settlement_id
                          FROM lineage
                          JOIN quarterly_settlements AS settlement ON settlement.id = lineage.settlement_id
                          WHERE settlement.replaces_settlement_id IS NOT NULL
                      )
"""

CORRECTION_LINEAGE = """
            SELECT RAISE(ABORT, 'invoice_correction_late_source_group_invalid')
            WHERE NEW.replacement_invoice_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM invoice_sources AS source
                  JOIN quarterly_settlements AS settlement ON settlement.id = source.settlement_id
                  JOIN invoices AS original ON original.id = NEW.original_invoice_id
                  WHERE source.invoice_id = NEW.replacement_invoice_id AND source.active = 1
                    AND (settlement.status != 'FINALIZED'
                      OR settlement.client_id != original.client_id
                      OR settlement.year != original.year OR settlement.quarter != original.quarter
                      OR settlement.fee_plan_id != original.fee_plan_id)
              );

            SELECT RAISE(ABORT, 'invoice_correction_source_lineage_invalid')
            WHERE NEW.replacement_invoice_id IS NOT NULL
              AND (
                  NOT EXISTS (SELECT 1 FROM invoice_sources WHERE invoice_id = NEW.original_invoice_id)
                  OR EXISTS (
""" + LINEAGE_CTE + """
                      SELECT 1 FROM invoice_sources AS replacement_source
                      JOIN quarterly_settlements AS replacement_settlement
                        ON replacement_settlement.id = replacement_source.settlement_id
                      LEFT JOIN lineage ON lineage.replacement_source_id = replacement_source.id
                      LEFT JOIN invoice_sources AS original_source
                        ON original_source.invoice_id = NEW.original_invoice_id
                       AND original_source.settlement_id = lineage.settlement_id
                      WHERE replacement_source.invoice_id = NEW.replacement_invoice_id
                        AND replacement_source.active = 1
                      GROUP BY replacement_source.id
                      HAVING COUNT(DISTINCT original_source.id) > 1
                         OR (COUNT(DISTINCT original_source.id) = 0
                             AND replacement_settlement.platform_id IN (
                                 SELECT original_settlement.platform_id
                                 FROM invoice_sources AS source
                                 JOIN quarterly_settlements AS original_settlement ON original_settlement.id = source.settlement_id
                                 WHERE source.invoice_id = NEW.original_invoice_id
                             ))
                  )
                  OR EXISTS (
""" + LINEAGE_CTE + """
                      SELECT 1 FROM invoice_sources AS original_source
                      LEFT JOIN lineage ON lineage.settlement_id = original_source.settlement_id
                      WHERE original_source.invoice_id = NEW.original_invoice_id
                      GROUP BY original_source.id
                      HAVING COUNT(DISTINCT lineage.replacement_source_id) != 1
                  )
              );

"""


def upgrade() -> None:
    from app.services.backup import LATEST_HEAD_TRIGGER_NAMES
    from app.services.settlement_boundary_contract import settlement_boundary_trigger_sql_is_current
    from app.services.workflow_guard_contract import WORKFLOW_TRIGGER_NAMES, workflow_trigger_sql_is_current

    connection = op.get_bind()
    # sqlite3 does not begin a transaction for DDL by itself. Keep all replacements atomic.
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    if version != down_revision:
        raise RuntimeError("财务流程保护迁移要求精确的0.2.20数据库结构")
    previous = {str(row[0]): str(row[1] or "") for row in connection.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
    if set(previous) != LATEST_HEAD_TRIGGER_NAMES or not settlement_boundary_trigger_sql_is_current(previous):
        raise RuntimeError("财务流程保护迁移缺少完整前序Trigger；未修改业务记录")
    updates = {name: previous[name] for name in WORKFLOW_TRIGGER_NAMES}
    for name, prefixes in {
        "trg_transactions_block_finalized_period": ("NEW",),
        "trg_transactions_update_block_frozen_period": ("OLD", "NEW"),
        "trg_transactions_delete_block_frozen_period": ("OLD",),
    }.items():
        for prefix in prefixes:
            old = f"{prefix}.transaction_date >= line.start_date"
            if updates[name].count(old) != 1:
                raise RuntimeError("财务流程保护迁移检测到非预期资金锁定条件")
            new = f"""({old} OR {prefix}.transaction_date > (
                  SELECT opening_snapshot.as_of_date FROM balance_snapshots AS opening_snapshot
                  WHERE opening_snapshot.id = line.beginning_snapshot_id
              ))"""
            updates[name] = updates[name].replace(old, new)
    name = "trg_settlement_validate_finalize"
    anchor = "SELECT RAISE(ABORT, 'settlement_owner_or_plan_invalid')"
    if updates[name].count(anchor) != 1:
        raise RuntimeError("财务流程保护迁移检测到非预期Finalize规则")
    updates[name] = updates[name].replace(anchor, CONSECUTIVE_QUARTER + anchor)
    name = "trg_invoice_correction_validate_update"
    start = updates[name].find("SELECT RAISE(ABORT, 'invoice_correction_source_lineage_invalid')")
    end = updates[name].find("SELECT RAISE(ABORT, 'invoice_correction_transition_invalid')")
    if start < 0 or end <= start or "HAVING COUNT(DISTINCT original_source.id) != 1" not in updates[name][start:end]:
        raise RuntimeError("财务流程保护迁移检测到非预期Invoice替代链规则")
    updates[name] = updates[name][:start] + CORRECTION_LINEAGE + updates[name][end:]
    if not workflow_trigger_sql_is_current(updates):
        raise RuntimeError("财务流程保护迁移生成的Trigger契约不完整")
    for name, sql in updates.items():
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.exec_driver_sql(sql)
    actual = {str(row[0]): str(row[1] or "") for row in connection.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
    if set(actual) != set(previous) or not workflow_trigger_sql_is_current(actual):
        raise RuntimeError("财务流程保护迁移后复验失败")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("财务流程保护迁移后外键校验失败")


def downgrade() -> None:
    raise RuntimeError("财务流程保护不可原地降级；请恢复升级前完整备份")
