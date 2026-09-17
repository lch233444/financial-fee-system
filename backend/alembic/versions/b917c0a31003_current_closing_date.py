"""Check current Closing dates on future Finalize without rewriting history."""

from alembic import op

revision = "b917c0a31003"
down_revision = "a916c0e2b102"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.services.backup import LATEST_HEAD_TRIGGER_NAMES, validate_database_structure
    from app.services.closing_date_contract import (
        closing_date_schema_is_current,
        upgraded_closing_date_triggers,
    )
    from app.services.company_scope_contract import CODE_TRIGGER_SQL, company_scope_schema_is_current
    from app.services.invoice_correction_contract import correction_schema_is_current
    from app.services.invoice_group_contract import invoice_group_schema_is_current
    from app.services.invoice_payee_contract import payee_schema_is_current, payee_trigger_sql_is_current
    from app.services.release_100_contract import HWM_TRIGGER_SQL, release_100_schema_is_current

    connection = op.get_bind()
    if not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        validate_database_structure(connection.connection.driver_connection)
    except ValueError as exc:
        raise RuntimeError("Closing日期升级前财务保护结构不完整，已停止") from exc
    triggers = dict(connection.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if (
        connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() != down_revision
        or set(triggers) != LATEST_HEAD_TRIGGER_NAMES | set(CODE_TRIGGER_SQL) | set(HWM_TRIGGER_SQL)
        or not all((
            company_scope_schema_is_current(connection), invoice_group_schema_is_current(connection),
            payee_schema_is_current(connection), payee_trigger_sql_is_current(triggers),
            release_100_schema_is_current(connection), correction_schema_is_current(connection),
        ))
        or connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    ):
        raise RuntimeError("Closing日期升级前财务保护结构不完整，已停止")
    # Historical FINALIZED rows remain frozen exactly as stored. This adds a
    # transition guard; it does not recalculate or reject existing locked history.
    for name, sql in upgraded_closing_date_triggers(triggers).items():
        connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.exec_driver_sql(sql)
    if not closing_date_schema_is_current(connection):
        raise RuntimeError("Closing日期升级后结构校验失败")


def downgrade() -> None:
    raise RuntimeError("Closing日期保护不可原地降级，请使用升级前完整备份和匹配程序恢复")
