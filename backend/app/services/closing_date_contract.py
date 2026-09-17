"""Current Closing date guard, applied only to future settlement finalization."""

CLOSING_DATE_REVISION = "b917c0a31003"
FINALIZE_TRIGGER_NAME = "trg_settlement_validate_finalize"
CLOSING_DATE_GUARD = """
    SELECT RAISE(ABORT, 'settlement_closing_date_invalid')
    WHERE NEW.calculation_mode = 'ACCOUNT_HWM' AND EXISTS (
        SELECT 1
        FROM settlement_account_lines AS line
        JOIN sub_accounts AS account ON account.id = line.account_id
        WHERE line.settlement_id = NEW.id AND NOT (
            line.closing_date IS printf('%04d-%s', NEW.year, CASE NEW.quarter
                WHEN 1 THEN '03-31' WHEN 2 THEN '06-30'
                WHEN 3 THEN '09-30' WHEN 4 THEN '12-31' END)
            OR line.closing_date IS account.end_date
        )
    );
"""


def _normalized(sql: str) -> str:
    return " ".join(sql.casefold().split())


def upgraded_closing_date_triggers(previous: dict[str, str]) -> dict[str, str]:
    sql = previous.get(FINALIZE_TRIGGER_NAME, "").rstrip()
    if (
        not sql.endswith("END")
        or "settlement_closing_date_invalid" in sql
        or "when new.status = 'finalized' and old.status != 'finalized'" not in _normalized(sql)
    ):
        raise RuntimeError("Closing日期升级前Finalize保护与预期不一致，已停止")
    return {FINALIZE_TRIGGER_NAME: sql.removesuffix("END") + CLOSING_DATE_GUARD + "END"}


def closing_date_trigger_sql_is_current(trigger_sql: dict[str, str]) -> bool:
    sql = _normalized(trigger_sql.get(FINALIZE_TRIGGER_NAME, ""))
    return (
        "when new.status = 'finalized' and old.status != 'finalized'" in sql
        and sql.count(_normalized(CLOSING_DATE_GUARD)) == 1
    )


def closing_date_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    triggers = dict(execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall())
    return closing_date_trigger_sql_is_current(triggers)
