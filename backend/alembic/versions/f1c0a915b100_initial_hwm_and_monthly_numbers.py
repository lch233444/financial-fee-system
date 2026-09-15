"""Add auditable first-HWM overrides and system-wide monthly invoice counters."""
from alembic import op
import sqlalchemy as sa

revision = "f1c0a915b100"
down_revision = "c6f3a8d92e10"
branch_labels = None
depends_on = None


def upgrade():
    from app.services.backup import LATEST_HEAD_TRIGGER_NAMES
    from app.services.company_scope_contract import CODE_TRIGGER_SQL, company_scope_schema_is_current
    from app.services.release_100_contract import HWM_COLUMNS, HWM_TRIGGER_SQL, release_100_schema_is_current

    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision:
        raise RuntimeError("1.0升级要求精确的0.2.34数据库结构")
    triggers = dict(connection.exec_driver_sql("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if set(triggers) != LATEST_HEAD_TRIGGER_NAMES | set(CODE_TRIGGER_SQL) or not company_scope_schema_is_current(connection):
        raise RuntimeError("1.0升级前财务保护结构不完整，已停止")
    columns = {row[1] for row in connection.exec_driver_sql('PRAGMA table_info("settlement_account_lines")')}
    if HWM_COLUMNS & columns or connection.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE name='invoice_monthly_sequences'").first():
        raise RuntimeError("1.0升级检测到部分迁移结构，已停止")
    if connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError("1.0升级前外键校验失败")
    op.add_column('settlement_account_lines', sa.Column('hwm_source_type', sa.String(30), nullable=True))
    op.add_column('settlement_account_lines', sa.Column('hwm_override_reason', sa.Text(), nullable=True))
    op.add_column('settlement_account_lines', sa.Column('hwm_override_confirmed', sa.Boolean(), nullable=False, server_default=sa.text('0')))
    op.create_table('invoice_monthly_sequences',
        sa.Column('issue_month', sa.String(6), primary_key=True),
        sa.Column('last_number', sa.Integer(), nullable=False),
        sa.CheckConstraint('last_number BETWEEN 0 AND 999', name='ck_monthly_invoice_capacity'))
    # Preserve every old number and attempt. Renumbering this installation's
    # authorized test data is a separate, backed-up conversion at release time.
    numbers = connection.exec_driver_sql('SELECT invoice_number FROM invoices UNION SELECT invoice_number FROM invoice_issue_attempts').scalars()
    counters = {}
    for number in numbers:
        if number and len(number) == 9 and number.isascii() and number.isdigit() and 1 <= int(number[4:6]) <= 12:
            counters[number[:6]] = max(counters.get(number[:6], 0), int(number[6:]))
    for month, last in counters.items():
        connection.exec_driver_sql('INSERT INTO invoice_monthly_sequences(issue_month,last_number) VALUES (?,?)', (month,last))
    for sql in HWM_TRIGGER_SQL.values():
        connection.exec_driver_sql(sql)
    if not release_100_schema_is_current(connection):
        raise RuntimeError('1.0升级后结构校验失败')


def downgrade():
    raise RuntimeError('1.0不可原地降级，请使用升级前完整备份和匹配程序恢复')
