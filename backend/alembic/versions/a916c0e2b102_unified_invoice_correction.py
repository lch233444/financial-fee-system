"""Unify correction payee and source choices; preserve all historical records."""
from alembic import op
import sqlalchemy as sa

revision = "a916c0e2b102"
down_revision = "f1c0a915b100"
branch_labels = None
depends_on = None


def upgrade():
    from app.services.backup import LATEST_HEAD_TRIGGER_NAMES
    from app.services.company_scope_contract import CODE_TRIGGER_SQL, company_scope_schema_is_current
    from app.services.invoice_group_contract import invoice_group_schema_is_current
    from app.services.invoice_payee_contract import payee_schema_is_current, payee_trigger_sql_is_current
    from app.services.release_100_contract import HWM_TRIGGER_SQL, release_100_schema_is_current
    from app.services.invoice_correction_contract import CORRECTION_COLUMNS, upgraded_correction_triggers, correction_schema_is_current

    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    triggers = dict(connection.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if (connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalar_one() != down_revision
            or set(triggers) != LATEST_HEAD_TRIGGER_NAMES | set(CODE_TRIGGER_SQL) | set(HWM_TRIGGER_SQL)
            or not all((company_scope_schema_is_current(connection), invoice_group_schema_is_current(connection),
                        payee_schema_is_current(connection), payee_trigger_sql_is_current(triggers), release_100_schema_is_current(connection)))):
        raise RuntimeError('统一更正升级前财务保护结构不完整，已停止')
    columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info("invoice_corrections")')}
    if columns & CORRECTION_COLUMNS or connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError('统一更正升级检测到部分结构或外键异常，已停止')
    replacements = upgraded_correction_triggers(triggers)
    op.add_column('invoice_corrections', sa.Column('recalculate_settlements', sa.Boolean(), nullable=True))
    op.add_column('invoice_corrections', sa.Column('revision_no', sa.Integer(), nullable=False, server_default='1'))
    for name, sql in replacements.items():
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.exec_driver_sql(sql)
    if not correction_schema_is_current(connection):
        raise RuntimeError('统一更正升级后结构校验失败')


def downgrade():
    raise RuntimeError('统一更正不可原地降级，请使用升级前完整备份和匹配程序恢复')
