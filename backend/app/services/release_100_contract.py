"""Persisted first-HWM confirmation and monthly invoice numbering contract."""

RELEASE_100_REVISION = "f1c0a915b100"
HWM_COLUMNS = {"hwm_source_type", "hwm_override_reason", "hwm_override_confirmed"}
HWM_TRIGGER_SQL = {
    "trg_settlement_hwm_confirm_finalize": """CREATE TRIGGER trg_settlement_hwm_confirm_finalize
    BEFORE UPDATE OF status ON quarterly_settlements
    WHEN NEW.status = 'FINALIZED' AND OLD.status != 'FINALIZED'
         AND NEW.calculation_mode = 'ACCOUNT_HWM'
    BEGIN
        SELECT RAISE(ABORT, 'settlement_hwm_source_or_confirmation_invalid')
        WHERE EXISTS (
            SELECT 1 FROM settlement_account_lines line WHERE line.settlement_id = NEW.id AND (
                (line.previous_line_id IS NULL AND (
                    line.hwm_source_type IS NOT 'INITIAL_SNAPSHOT'
                    OR (line.original_hwm_cents != line.beginning_cents AND (
                        coalesce(length(trim(line.hwm_override_reason)), 0) < 2
                        OR line.hwm_override_confirmed IS NOT 1
                    ))
                )) OR (line.previous_line_id IS NOT NULL AND (
                    line.hwm_source_type IS NOT 'PREVIOUS_SETTLEMENT'
                    OR line.hwm_override_reason IS NOT NULL
                    OR line.hwm_override_confirmed IS NOT 0
                ))
            )
        );
    END"""
}


def release_100_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    columns = {row[1]: row for row in execute('PRAGMA table_info("settlement_account_lines")')}
    if not HWM_COLUMNS.issubset(columns):
        return False
    if columns['hwm_override_confirmed'][3] != 1 or str(columns['hwm_override_confirmed'][4]) != '0':
        return False
    sequence_columns = {row[1]: row for row in execute('PRAGMA table_info("invoice_monthly_sequences")')}
    if set(sequence_columns) != {'issue_month', 'last_number'} or sequence_columns['issue_month'][5] != 1:
        return False
    sequence_sql = execute("SELECT sql FROM sqlite_master WHERE name='invoice_monthly_sequences' AND type='table'").fetchone()
    if not sequence_sql or 'ck_monthly_invoice_capacity' not in sequence_sql[0]:
        return False
    triggers = dict(execute("SELECT name, sql FROM sqlite_master WHERE type='trigger'").fetchall())
    normalize = lambda sql: ' '.join(sql.lower().split())
    return all(normalize(triggers.get(name, '')) == normalize(sql) for name, sql in HWM_TRIGGER_SQL.items())
