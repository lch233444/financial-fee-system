"""Schema contract for one active bill per client and quarter, across fee plans."""

INVOICE_GROUP_REVISION = "b7e2d9a41c60"
INVOICE_GROUP_INDEX = "uq_invoices_active_client_period"


def invoice_group_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    indexes = {row[1]: row for row in execute('PRAGMA index_list("invoices")')}
    index = indexes.get(INVOICE_GROUP_INDEX)
    if not index or index[2] != 1 or index[4] != 1:
        return False
    columns = tuple(row[2] for row in execute(f'PRAGMA index_info("{INVOICE_GROUP_INDEX}")'))
    sql = execute("SELECT sql FROM sqlite_master WHERE name = ?", (INVOICE_GROUP_INDEX,)).fetchone()[0]
    if columns != ("client_id", "year", "quarter") or " ".join(sql.lower().split()).split(" where ")[-1] != "lifecycle_status in ('draft', 'issuing', 'issued')":
        return False
    triggers = dict(execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'").fetchall())
    normalized = {name: " ".join(sql.lower().split()) for name, sql in triggers.items()}
    required = {
        "trg_invoice_validate_issue": "invoice_source_set_incomplete",
        "trg_invoice_correction_validate_update": "(replacement_settlement.platform_id, replacement_settlement.fee_plan_id) in (",
        "trg_invoice_correction_validate_insert": "invoice_correction_group_already_open",
        "trg_payment_validate_insert": "payment_invoice_group_has_open_correction",
        "trg_settlement_validate_finalize": "settlement_company_correction_open",
        "trg_settlement_validate_void": "settlement_company_correction_open",
    }
    return all(marker in normalized.get(name, "") for name, marker in required.items()) and all(
        forbidden not in normalized.get(name, "")
        for name, forbidden in (
            ("trg_invoice_validate_issue", "settlement.fee_plan_id"),
            ("trg_invoice_validate_issue", "original.fee_plan_id"),
            ("trg_invoice_correction_validate_update", "replacement.fee_plan_id"),
            ("trg_invoice_correction_validate_update", "original.fee_plan_id"),
            ("trg_invoice_correction_validate_insert", "original.fee_plan_id"),
            ("trg_payment_validate_insert", "original.fee_plan_id"),
            ("trg_settlement_validate_finalize", "original.fee_plan_id"),
            ("trg_settlement_validate_void", "original.fee_plan_id"),
        )
    )
