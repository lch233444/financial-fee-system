"""Persist independent package cycles without rewriting historical financial rows."""
from alembic import op

revision = "e027a0ee1300"
down_revision = "d016f0ee1160"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.backup import validate_database_structure
    from app.services.annual_period_contract import PERIOD_TRIGGER_SQL, upgrade_finalize, annual_schema_is_current
    c = op.get_bind()
    if not c.connection.driver_connection.in_transaction:
        c.exec_driver_sql("BEGIN IMMEDIATE")
    if c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision:
        raise RuntimeError("年度结算升级要求精确前序版本")
    validate_database_structure(c.connection.driver_connection)
    for table in ('quarterly_settlements', 'invoices'):
        if 'period_type' in {r[1] for r in c.exec_driver_sql(f'PRAGMA table_info("{table}")')}:
            raise RuntimeError("已存在未完成的周期结构；停止升级")
        if c.exec_driver_sql(f"SELECT 1 FROM {table} WHERE quarter NOT BETWEEN 1 AND 4 LIMIT 1").first():
            raise RuntimeError("原有季度记录无效；停止升级，不推定为年度")
    if c.exec_driver_sql("SELECT 1 FROM app_settings WHERE key='settlement_cycle'").first():
        raise RuntimeError("原数据存在未知周期配置；停止升级")
    final = c.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='trg_settlement_validate_finalize'").scalar_one()
    upgraded = upgrade_finalize(final)
    for table in ('quarterly_settlements', 'invoices'):
        c.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN period_type VARCHAR(20) NOT NULL DEFAULT 'QUARTERLY'")
    c.exec_driver_sql("INSERT INTO app_settings(key,value,created_at,updated_at) VALUES('settlement_cycle','QUARTERLY',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    c.exec_driver_sql("DROP TRIGGER trg_settlement_validate_finalize")
    c.exec_driver_sql(upgraded)
    for sql in PERIOD_TRIGGER_SQL.values():
        c.exec_driver_sql(sql)
    if not annual_schema_is_current(c):
        raise RuntimeError("年度周期保护验证失败；回滚升级")


def downgrade() -> None:
    raise RuntimeError("不能原地降级年度数据库，请使用升级前完整备份")
