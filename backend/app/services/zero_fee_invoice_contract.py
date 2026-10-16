"""Allow a zero fee invoice while retaining the negative amount guard."""

ZERO_FEE_REVISION = "d016f0ee1160"
ISSUE_TRIGGER = "trg_invoice_validate_issue"
OLD_AMOUNT_GUARD = "WHERE NEW.amount_cents <= 0;"
NEW_AMOUNT_GUARD = "WHERE NEW.amount_cents < 0;"


def zero_fee_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    row = execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (ISSUE_TRIGGER,)).fetchone()
    sql = " ".join((row[0] if row else "").lower().split())
    guard = "select raise(abort, 'invoice_amount_invalid') where new.amount_cents < 0;"
    return sql.count(guard) == 1 and "new.amount_cents <= 0" not in sql
