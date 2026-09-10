"""Aggregate all fee plans into one client-quarter invoice without rewriting history.

Revision ID: b7e2d9a41c60
Revises: a2e6c9d74f31
"""
from alembic import op

revision = "b7e2d9a41c60"
down_revision = "a2e6c9d74f31"
branch_labels = None
depends_on = None


def _replace(sql, old, new, count=1):
    if sql.count(old) != count:
        raise RuntimeError("客户季度合并迁移检测到非预期前序Trigger，已停止")
    return sql.replace(old, new)


def upgrade():
    from app.services.backup import LATEST_HEAD_TRIGGER_NAMES
    from app.services.invoice_payee_contract import payee_schema_is_current, payee_trigger_sql_is_current
    from app.services.workflow_guard_contract import workflow_trigger_sql_is_current
    from app.services.invoice_group_contract import invoice_group_schema_is_current

    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision:
        raise RuntimeError("客户季度合并迁移要求精确的a2e6c9d74f31前序数据库")
    previous = dict(connection.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if (set(previous) != LATEST_HEAD_TRIGGER_NAMES or not payee_schema_is_current(connection)
            or not payee_trigger_sql_is_current(previous) or not workflow_trigger_sql_is_current(previous)):
        raise RuntimeError("客户季度合并迁移要求完整前序财务保护")
    # Never choose one old bill, erase cash, or merge historical issued records.
    if connection.exec_driver_sql("""SELECT 1 FROM invoices
        WHERE lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
        GROUP BY client_id, year, quarter HAVING COUNT(*) > 1""").first():
        raise RuntimeError("同一客户季度存在多张有效Invoice；请先在旧版核对并通过作废/更正处理，保留全部收款历史后再升级")
    if connection.exec_driver_sql("""SELECT 1 FROM invoice_corrections AS correction
        JOIN invoices AS original ON original.id = correction.original_invoice_id
        WHERE correction.status = 'OPEN'
        GROUP BY original.client_id, original.year, original.quarter HAVING COUNT(*) > 1""").first():
        raise RuntimeError("同一客户季度存在多项未完成更正；请先在旧版完成更正再升级")
    updates = {}
    for name, old in (
        ("trg_payment_validate_insert", "AND original.fee_plan_id = target.fee_plan_id"),
        ("trg_invoice_correction_validate_insert", "AND original.fee_plan_id = candidate.fee_plan_id"),
        ("trg_settlement_validate_finalize", "AND original.fee_plan_id = NEW.fee_plan_id"),
        ("trg_settlement_validate_void", "AND original.fee_plan_id = NEW.fee_plan_id"),
    ):
        updates[name] = _replace(previous[name], old, "")
    issue = previous['trg_invoice_validate_issue']
    for old, count in (
        ("OR settlement.fee_plan_id IS NOT NEW.fee_plan_id", 1),
        ("AND settlement.fee_plan_id = NEW.fee_plan_id", 1),
        ("AND original.fee_plan_id = NEW.fee_plan_id", 2),
    ):
        issue = _replace(issue, old, "", count)
    updates['trg_invoice_validate_issue'] = issue
    correction = previous['trg_invoice_correction_validate_update']
    for old, new in (
        ("AND replacement.fee_plan_id = original.fee_plan_id", ""),
        ("OR settlement.fee_plan_id != original.fee_plan_id", ""),
        ("replacement_settlement.platform_id IN (", "(replacement_settlement.platform_id, replacement_settlement.fee_plan_id) IN ("),
        ("SELECT original_settlement.platform_id", "SELECT original_settlement.platform_id, original_settlement.fee_plan_id"),
    ):
        correction = _replace(correction, old, new)
    updates['trg_invoice_correction_validate_update'] = correction
    connection.exec_driver_sql('DROP INDEX uq_invoices_active_client_period_plan')
    connection.exec_driver_sql("""CREATE UNIQUE INDEX uq_invoices_active_client_period
        ON invoices (client_id, year, quarter)
        WHERE lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')""")
    for name, sql in updates.items():
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.exec_driver_sql(sql)
    if not invoice_group_schema_is_current(connection):
        raise RuntimeError("客户季度合并迁移后财务保护复验失败")
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError("客户季度合并迁移后外键复验失败")


def downgrade():
    raise RuntimeError("客户季度合并不可原地降级；请恢复升级前完整备份和匹配程序")
