"""Permit zero fee invoices without changing any historical business row."""

from alembic import op

revision = "d016f0ee1160"
down_revision = "c921f0181100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.backup import validate_database_structure
    from app.services.zero_fee_invoice_contract import (
        ISSUE_TRIGGER, OLD_AMOUNT_GUARD, NEW_AMOUNT_GUARD, zero_fee_schema_is_current,
    )

    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    if connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision:
        raise RuntimeError("零金额账单升级要求精确前序数据库")
    validate_database_structure(connection.connection.driver_connection)
    sql = connection.exec_driver_sql(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (ISSUE_TRIGGER,),
    ).scalar_one()
    if sql.count(OLD_AMOUNT_GUARD) != 1 or NEW_AMOUNT_GUARD in sql:
        raise RuntimeError("零金额账单升级前金额保护不完整，已停止")
    connection.exec_driver_sql(f'DROP TRIGGER "{ISSUE_TRIGGER}"')
    connection.exec_driver_sql(sql.replace(OLD_AMOUNT_GUARD, NEW_AMOUNT_GUARD))
    if not zero_fee_schema_is_current(connection):
        raise RuntimeError("零金额账单升级后金额保护校验失败")


def downgrade() -> None:
    raise RuntimeError("零金额账单不可原地降级，请使用升级前完整备份及匹配程序恢复")
