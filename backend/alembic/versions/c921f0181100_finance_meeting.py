"""Remove FC/plan codes, retain proof history and use date-based Closing."""
import re
from alembic import op

revision = "c921f0181100"
down_revision = "b917c0a31003"
branch_labels = None
depends_on = None


def upgrade():
    from app.services.backup import validate_database_structure
    from app.services.company_scope_contract import CODE_TRIGGER_SQL
    from app.services.finance_meeting_contract import EVIDENCE_TRIGGER_SQL, meeting_schema_is_current, upgraded_meeting_finalize
    connection = op.get_bind()
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
        raise RuntimeError("财务会议表迁移要求专用迁移连接")
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision:
        raise RuntimeError("财务会议升级要求精确前序数据库")
    validate_database_structure(connection.connection.driver_connection)
    previous = dict(connection.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if "superseded" in {row[1] for row in connection.exec_driver_sql('PRAGMA table_info("attachments")')}:
        raise RuntimeError("财务会议升级检测到部分迁移结构")
    tables = {}
    for table, constraint in (("fcs", "uq_fc_company_code"), ("fee_plans", "uq_fee_plan_company_code")):
        columns = [row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')]
        if "code" not in columns:
            raise RuntimeError("财务会议升级检测到部分编码结构")
        sql = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar_one()
        sql, removed = re.subn(r'\s*code VARCHAR\(\d+\) NOT NULL,', '', sql)
        sql, unique = re.subn(r',?\s*CONSTRAINT ' + constraint + r' UNIQUE \(company_id, code\),?', ',', sql)
        sql = re.sub(r',\s*\)', '\n)', sql)
        sql, renamed = re.subn(r'^CREATE TABLE\s+"?' + table + r'"?\s*\(', f'CREATE TABLE "_meeting_{table}" (', sql, count=1)
        if (removed, unique, renamed) != (1, 1, 1):
            raise RuntimeError("财务会议升级检测到非预期编码表定义")
        indexes = [row[0] for row in connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL", (table,))]
        tables[table] = sql, indexes, [column for column in columns if column != "code"]
    previous["trg_settlement_validate_finalize"] = upgraded_meeting_finalize(previous["trg_settlement_validate_finalize"])
    for name in previous:
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    for name in CODE_TRIGGER_SQL:
        previous.pop(name)
    for table, (sql, indexes, columns) in tables.items():
        connection.exec_driver_sql(sql)
        names = ", ".join(f'"{column}"' for column in columns)
        connection.exec_driver_sql(f'INSERT INTO "_meeting_{table}" ({names}) SELECT {names} FROM "{table}"')
        connection.exec_driver_sql(f'DROP TABLE "{table}"')
        connection.exec_driver_sql(f'ALTER TABLE "_meeting_{table}" RENAME TO "{table}"')
        for sql in indexes:
            connection.exec_driver_sql(sql)
    connection.exec_driver_sql("ALTER TABLE attachments ADD COLUMN superseded BOOLEAN NOT NULL DEFAULT 0")
    for sql in (*previous.values(), *EVIDENCE_TRIGGER_SQL.values()):
        connection.exec_driver_sql(sql)
    if not meeting_schema_is_current(connection) or connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError("财务会议升级后保护结构校验失败")


def downgrade():
    raise RuntimeError("财务会议升级不可原地降级；请恢复升级前完整备份及匹配程序")
