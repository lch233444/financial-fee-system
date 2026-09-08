"""Allow unpaid invoice-only receiving-company corrections without changing settlements.

Revision ID: a2e6c9d74f31
Revises: e8b2c6d91a04
"""
from alembic import op

revision = "a2e6c9d74f31"
down_revision = "e8b2c6d91a04"
branch_labels = None
depends_on = None

UNPAID_GUARD = """
    SELECT RAISE(ABORT, 'invoice_company_correction_unpaid_required')
    WHERE NEW.target_company_id IS NOT NULL AND (
        EXISTS (SELECT 1 FROM payments WHERE invoice_id = NEW.original_invoice_id)
        OR EXISTS (SELECT 1 FROM payment_allocations WHERE invoice_id = NEW.original_invoice_id)
        OR EXISTS (SELECT 1 FROM invoice_adjustments WHERE invoice_id = NEW.original_invoice_id)
    );
"""

INSERT_GUARD = UNPAID_GUARD + """
    SELECT RAISE(ABORT, 'invoice_company_correction_target_invalid')
    WHERE NEW.target_company_id IS NOT NULL AND (
        NOT EXISTS (SELECT 1 FROM companies WHERE id = NEW.target_company_id)
        OR NEW.target_company_id = (SELECT COALESCE(payee_company_id, company_id) FROM invoices WHERE id = NEW.original_invoice_id)
    );
"""

UPDATE_GUARD = UNPAID_GUARD + """
    SELECT RAISE(ABORT, 'invoice_company_correction_target_invalid')
    WHERE NEW.replacement_invoice_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM invoices AS original JOIN invoices AS replacement ON replacement.id = NEW.replacement_invoice_id
        WHERE original.id = NEW.original_invoice_id
          AND COALESCE(replacement.payee_company_id, replacement.company_id)
              = COALESCE(NEW.target_company_id, original.payee_company_id, original.company_id)
          AND (NEW.target_company_id IS NULL OR (
              replacement.amount_cents = original.amount_cents AND replacement.company_id = original.company_id
              AND replacement.fc_id = original.fc_id
          ))
    );
    SELECT RAISE(ABORT, 'invoice_company_correction_sources_unchanged')
    WHERE NEW.target_company_id IS NOT NULL AND NEW.replacement_invoice_id IS NOT NULL AND (
        NOT EXISTS (SELECT 1 FROM invoice_sources WHERE invoice_id = NEW.original_invoice_id)
        OR EXISTS (
            SELECT settlement_id, locked_amount_cents FROM invoice_sources WHERE invoice_id = NEW.original_invoice_id
            EXCEPT
            SELECT settlement_id, locked_amount_cents FROM invoice_sources WHERE invoice_id = NEW.replacement_invoice_id AND active = 1
        )
        OR EXISTS (
            SELECT settlement_id, locked_amount_cents FROM invoice_sources WHERE invoice_id = NEW.replacement_invoice_id AND active = 1
            EXCEPT
            SELECT settlement_id, locked_amount_cents FROM invoice_sources WHERE invoice_id = NEW.original_invoice_id
        )
    );
"""

# An explicit payee is authorized only by the correction for this invoice group.
# The original service-company/FC/plan validation remains in the existing trigger.
ISSUE_GUARD = """
    SELECT RAISE(ABORT, 'invoice_payee_company_target_mismatch')
    WHERE EXISTS (
        SELECT 1 FROM invoice_corrections AS correction JOIN invoices AS original ON original.id = correction.original_invoice_id
        WHERE correction.status = 'OPEN' AND original.client_id = NEW.client_id
          AND original.year = NEW.year AND original.quarter = NEW.quarter AND original.fee_plan_id = NEW.fee_plan_id
          AND COALESCE(NEW.payee_company_id, NEW.company_id)
              != COALESCE(correction.target_company_id, original.payee_company_id, original.company_id)
    );
    SELECT RAISE(ABORT, 'invoice_payee_company_not_authorized')
    WHERE COALESCE(NEW.payee_company_id, NEW.company_id) != NEW.company_id
      AND NOT EXISTS (
        SELECT 1 FROM invoice_corrections AS correction JOIN invoices AS original ON original.id = correction.original_invoice_id
        WHERE original.client_id = NEW.client_id AND original.year = NEW.year
          AND original.quarter = NEW.quarter AND original.fee_plan_id = NEW.fee_plan_id
          AND (correction.status = 'OPEN' OR correction.replacement_invoice_id = NEW.id)
          AND COALESCE(NEW.payee_company_id, NEW.company_id)
              = COALESCE(correction.target_company_id, original.payee_company_id, original.company_id)
    );
"""

SETTLEMENT_GUARD = """
    SELECT RAISE(ABORT, 'settlement_company_correction_open')
    WHERE EXISTS (
        SELECT 1 FROM invoice_corrections AS correction JOIN invoices AS original ON original.id = correction.original_invoice_id
        WHERE correction.status = 'OPEN' AND correction.target_company_id IS NOT NULL
          AND original.client_id = NEW.client_id AND original.year = NEW.year
          AND original.quarter = NEW.quarter AND original.fee_plan_id = NEW.fee_plan_id
    );
"""


def _replace(sql, old, new):
    if sql.count(old) != 1:
        raise RuntimeError("收款公司迁移检测到非预期前序Trigger，已停止")
    return sql.replace(old, new)


def _append_guard(sql, guard):
    index = sql.rfind("END")
    if index < 0:
        raise RuntimeError("收款公司迁移缺少Trigger结束标记")
    return sql[:index] + guard + sql[index:]


def upgrade():
    from app.services.backup import LATEST_HEAD_TRIGGER_NAMES
    from app.services.workflow_guard_contract import workflow_trigger_sql_is_current
    from app.services.invoice_payee_contract import payee_trigger_sql_is_current

    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision:
        raise RuntimeError("收款公司迁移要求精确的e8b2c6d91a04前序数据库")
    previous = dict(connection.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if set(previous) != LATEST_HEAD_TRIGGER_NAMES or not workflow_trigger_sql_is_current(previous):
        raise RuntimeError("收款公司迁移要求完整前序财务保护")
    for table, column in (("invoices", "payee_company_id"), ("invoice_corrections", "target_company_id")):
        if column in {row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')}:
            raise RuntimeError("收款公司迁移检测到未版本化的部分字段")
        connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN "{column}" INTEGER REFERENCES companies(id) ON DELETE RESTRICT')

    name = "trg_invoice_financial_header_update_lock"
    header = _replace(previous[name], "company_id, fc_id, amount_cents", "company_id, payee_company_id, fc_id, amount_cents")
    header = _replace(header, "OR NEW.company_id IS NOT OLD.company_id", "OR NEW.company_id IS NOT OLD.company_id\n            OR NEW.payee_company_id IS NOT OLD.payee_company_id")
    updates = {name: header}
    name = "trg_invoice_correction_validate_update"
    update = _replace(previous[name], "OR NEW.reason IS NOT OLD.reason", "OR NEW.reason IS NOT OLD.reason\n               OR NEW.target_company_id IS NOT OLD.target_company_id")
    update = _replace(update, "SELECT RAISE(ABORT, 'invoice_correction_source_lineage_invalid')\n            WHERE NEW.replacement_invoice_id IS NOT NULL", "SELECT RAISE(ABORT, 'invoice_correction_source_lineage_invalid')\n            WHERE NEW.replacement_invoice_id IS NOT NULL AND NEW.target_company_id IS NULL")
    updates[name] = _append_guard(update, UPDATE_GUARD)
    updates['trg_invoice_correction_validate_insert'] = _append_guard(previous['trg_invoice_correction_validate_insert'], INSERT_GUARD)
    # Let the unchanged lifecycle guard reject invalid transitions. Recreating
    # a trigger must not make a VOID -> ISSUED request report a source error.
    issue = _replace(previous['trg_invoice_validate_issue'],
                     "AND OLD.lifecycle_status != NEW.lifecycle_status",
                     "AND ((OLD.lifecycle_status = 'DRAFT' AND NEW.lifecycle_status = 'ISSUING')\n"
                     "               OR (OLD.lifecycle_status = 'ISSUING' AND NEW.lifecycle_status = 'ISSUED'))")
    updates['trg_invoice_validate_issue'] = _append_guard(issue, ISSUE_GUARD)
    for name in ('trg_settlement_validate_finalize', 'trg_settlement_validate_void'):
        updates[name] = _append_guard(previous[name], SETTLEMENT_GUARD)
    for name, sql in updates.items():
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.exec_driver_sql(sql)
    actual = dict(connection.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if set(actual) != set(previous) or not payee_trigger_sql_is_current(actual) or not workflow_trigger_sql_is_current(actual):
        raise RuntimeError("收款公司迁移后保护复验失败")
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError("收款公司迁移后外键复验失败")


def downgrade():
    raise RuntimeError("收款公司更正不可原地降级；请恢复升级前完整备份")
