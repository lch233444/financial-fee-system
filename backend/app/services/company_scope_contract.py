"""Company is selected for a bill; legacy master ownership is metadata only."""

COMPANY_SCOPE_REVISION = "c6f3a8d92e10"
CODE_TRIGGER_SQL = {
    f"trg_{table}_code_unique_insert": f"""CREATE TRIGGER trg_{table}_code_unique_insert
    BEFORE INSERT ON {table}
    BEGIN
        SELECT RAISE(ABORT, '{table}_code_must_be_globally_unique')
        WHERE EXISTS (SELECT 1 FROM {table} WHERE upper(trim(code)) = upper(trim(NEW.code)));
    END"""
    for table in ("fcs", "fee_plans")
}


def company_scope_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    for table in ("fcs", "fee_plans"):
        columns = {row[1]: row for row in execute(f'PRAGMA table_info("{table}")')}
        company = columns.get("company_id")
        keys = {(row[3], row[2], row[4], row[6]) for row in execute(f'PRAGMA foreign_key_list("{table}")')}
        if not company or company[2].upper() != "INTEGER" or company[3] != 0 or (
            "company_id", "companies", "id", "RESTRICT"
        ) not in keys:
            return False
    triggers = dict(execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall())
    normalize = lambda sql: " ".join(sql.lower().split())
    if any(normalize(triggers.get(name, "")) != normalize(sql) for name, sql in CODE_TRIGGER_SQL.items()):
        return False
    finalize = normalize(triggers.get("trg_settlement_validate_finalize", ""))
    issue = normalize(triggers.get("trg_invoice_validate_issue", ""))
    return (
        all(marker in finalize for marker in ("settlement_company_independent", "new.fc_id = client.fc_id", "new.fee_rate_bps = plan.fee_rate_bps"))
        and all(marker not in finalize for marker in ("new.company_id=client.company_id", "new.company_id = client.company_id", "fc.company_id", "plan.company_id"))
        and "invoice_source_set_incomplete" in issue
        and "settlement.fc_id is not new.fc_id" in issue
        and "settlement.company_id" not in issue
    )
