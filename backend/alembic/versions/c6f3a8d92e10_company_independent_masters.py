"""Unbind new master records without rewriting old ownership or cash history."""
import re

from alembic import op

revision = "c6f3a8d92e10"
down_revision = "b7e2d9a41c60"
branch_labels = None
depends_on = None


def _replace_once(sql, old, new):
    if sql.count(old) != 1:
        raise RuntimeError("公司解绑迁移检测到非预期前序保护，已停止")
    return sql.replace(old, new)


def upgrade():
    from app.services.backup import LATEST_HEAD_TRIGGER_NAMES
    from app.services.company_scope_contract import CODE_TRIGGER_SQL, company_scope_schema_is_current
    from app.services.invoice_group_contract import invoice_group_schema_is_current
    from app.services.invoice_payee_contract import payee_schema_is_current, payee_trigger_sql_is_current
    from app.services.workflow_guard_contract import workflow_trigger_sql_is_current

    connection = op.get_bind()
    # Alembic uses a dedicated connection with FK enforcement disabled. Refuse
    # another connection policy rather than disabling checks inside a transaction.
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
        raise RuntimeError("公司解绑表迁移要求专用迁移连接；已停止")
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision:
        raise RuntimeError("公司解绑迁移要求精确前序数据库")
    previous = dict(connection.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if (set(previous) != LATEST_HEAD_TRIGGER_NAMES or not invoice_group_schema_is_current(connection)
            or not payee_schema_is_current(connection) or not payee_trigger_sql_is_current(previous)
            or not workflow_trigger_sql_is_current(previous)):
        raise RuntimeError("公司解绑迁移要求完整前序财务保护")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("公司解绑迁移前外键校验失败")
    tables = {}
    for table in ("fcs", "fee_plans"):
        columns = {row[1]: row for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')}
        if columns["company_id"][3] != 1:
            raise RuntimeError("公司解绑迁移检测到部分迁移结构，已停止")
        sql = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar_one()
        sql = _replace_once(sql, "company_id INTEGER NOT NULL", "company_id INTEGER")
        sql, count = re.subn(r'^CREATE TABLE\s+"?' + table + r'"?\s*\(', f'CREATE TABLE "_company_scope_{table}" (', sql, count=1)
        if count != 1:
            raise RuntimeError("公司解绑迁移检测到非预期表定义，已停止")
        indexes = [row[0] for row in connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,))]
        tables[table] = (sql, indexes, list(columns))
    finalize = previous["trg_settlement_validate_finalize"]
    for clause in ("AND NEW.company_id = client.company_id", "AND fc.company_id = client.company_id", "AND plan.company_id = client.company_id"):
        finalize = _replace_once(finalize, clause, "")
    finalize = _replace_once(finalize, "AND NEW.fc_id = client.fc_id", "AND NEW.fc_id = client.fc_id /* settlement_company_independent */")
    previous["trg_settlement_validate_finalize"] = finalize
    previous["trg_invoice_validate_issue"] = _replace_once(previous["trg_invoice_validate_issue"], "OR settlement.company_id IS NOT NEW.company_id", "")
    # Drop/recreate all triggers in the same explicit transaction: table rename
    # must never temporarily leave triggers referring to a missing master table.
    for name in previous:
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    for table, (sql, indexes, columns) in tables.items():
        connection.exec_driver_sql(sql)
        names = ", ".join(f'"{column}"' for column in columns)
        connection.exec_driver_sql(f'INSERT INTO "_company_scope_{table}" ({names}) SELECT {names} FROM "{table}"')
        connection.exec_driver_sql(f'DROP TABLE "{table}"')
        connection.exec_driver_sql(f'ALTER TABLE "_company_scope_{table}" RENAME TO "{table}"')
        for sql in indexes:
            connection.exec_driver_sql(sql)
    for sql in (*previous.values(), *CODE_TRIGGER_SQL.values()):
        connection.exec_driver_sql(sql)
    if (not company_scope_schema_is_current(connection) or not invoice_group_schema_is_current(connection)
            or not payee_schema_is_current(connection) or not payee_trigger_sql_is_current(previous)):
        raise RuntimeError("公司解绑迁移后财务保护复验失败")
    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("公司解绑迁移后外键复验失败")


def downgrade():
    raise RuntimeError("公司解绑不可原地降级；请恢复升级前完整备份及匹配程序")
