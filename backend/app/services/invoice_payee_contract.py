"""Database guards for invoice-only, unpaid receiving-company corrections."""

PAYEE_REVISION = "a2e6c9d74f31"
PAYEE_COLUMNS = (("invoices", "payee_company_id"), ("invoice_corrections", "target_company_id"))
PAYEE_TRIGGER_MARKERS = {
    "trg_invoice_financial_header_update_lock": "new.payee_company_id is not old.payee_company_id",
    "trg_invoice_correction_validate_insert": "invoice_company_correction_unpaid_required",
    "trg_invoice_correction_validate_update": "invoice_company_correction_sources_unchanged",
    "trg_invoice_validate_issue": "invoice_payee_company_not_authorized",
    "trg_settlement_validate_finalize": "settlement_company_correction_open",
    "trg_settlement_validate_void": "settlement_company_correction_open",
}


def payee_trigger_sql_is_current(trigger_sql: dict[str, str]) -> bool:
    normalized = {name: " ".join(sql.casefold().split()) for name, sql in trigger_sql.items()}
    return all(marker in normalized.get(name, "") for name, marker in PAYEE_TRIGGER_MARKERS.items()) and (
        "new.target_company_id is not old.target_company_id" in normalized.get("trg_invoice_correction_validate_update", "")
        and "invoice_company_correction_unpaid_required" in normalized.get("trg_invoice_correction_validate_update", "")
        and "invoice_payee_company_target_mismatch" in normalized.get("trg_invoice_validate_issue", "")
    )


def payee_schema_is_current(connection) -> bool:
    # SQLite PRAGMA preserves inline REFERENCES actions that SQLAlchemy's
    # CREATE TABLE reflection may omit after ALTER TABLE ADD COLUMN.
    execute = getattr(connection, 'exec_driver_sql', None) or connection.execute
    for table, column in PAYEE_COLUMNS:
        info = {row[1]: row for row in execute(f'PRAGMA table_info("{table}")')}
        if column not in info or info[column][2].upper() != 'INTEGER' or info[column][3] != 0:
            return False
        foreign_keys = {(row[3], row[2], row[4], row[6]) for row in execute(f'PRAGMA foreign_key_list("{table}")')}
        if (column, 'companies', 'id', 'RESTRICT') not in foreign_keys:
            return False
    return True
