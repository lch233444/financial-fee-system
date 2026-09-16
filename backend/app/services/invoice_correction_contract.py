"""Independent payee/source policy and audited amendments for open corrections."""

CORRECTION_REVISION = "a916c0e2b102"
CORRECTION_COLUMNS = {"recalculate_settlements", "revision_no"}


def recalc_sql(alias: str) -> str:
    return f"COALESCE({alias}.recalculate_settlements, {alias}.target_company_id IS NULL)"


POLICY_CHANGED = f"(NEW.target_company_id IS NOT OLD.target_company_id OR {recalc_sql('NEW')} != {recalc_sql('OLD')})"
POLICY_GUARD = f"""
    SELECT RAISE(ABORT, 'invoice_correction_policy_invalid')
    WHERE NEW.revision_no IS NULL OR NEW.revision_no < 1
      OR (NEW.recalculate_settlements IS NOT NULL AND NEW.recalculate_settlements NOT IN (0, 1))
      OR ({recalc_sql('NEW')} = 0 AND NEW.target_company_id IS NULL);
    SELECT RAISE(ABORT, 'invoice_company_correction_target_invalid')
    WHERE NEW.target_company_id IS NOT NULL AND (
        NOT EXISTS (SELECT 1 FROM companies WHERE id = NEW.target_company_id)
        OR NEW.target_company_id = (SELECT COALESCE(payee_company_id, company_id) FROM invoices WHERE id = NEW.original_invoice_id)
    );
"""

# A revision is written only after its matching audit event in the same transaction.
AMEND_GUARD = f"""
    SELECT RAISE(ABORT, 'invoice_correction_history_immutable')
    WHERE (NEW.revision_no != OLD.revision_no OR {POLICY_CHANGED}
           OR NEW.recalculate_settlements IS NOT OLD.recalculate_settlements) AND (
        NOT {POLICY_CHANGED} OR NEW.revision_no != OLD.revision_no + 1
        OR OLD.status != 'OPEN' OR NEW.status != 'OPEN'
        OR OLD.replacement_invoice_id IS NOT NULL OR NEW.replacement_invoice_id IS NOT NULL
        OR (SELECT lifecycle_status FROM invoices WHERE id = NEW.original_invoice_id) != 'VOID'
        OR EXISTS (SELECT 1 FROM payments WHERE invoice_id = NEW.original_invoice_id)
        OR EXISTS (SELECT 1 FROM payment_allocations WHERE invoice_id = NEW.original_invoice_id OR correction_id = NEW.id)
        OR EXISTS (SELECT 1 FROM invoice_adjustments WHERE invoice_id = NEW.original_invoice_id OR correction_id = NEW.id)
        OR EXISTS (SELECT 1 FROM payment_refunds WHERE correction_id = NEW.id)
        OR EXISTS (
            SELECT 1 FROM invoices AS candidate JOIN invoices AS original ON original.id = NEW.original_invoice_id
            WHERE candidate.client_id = original.client_id AND candidate.year = original.year
              AND candidate.quarter = original.quarter AND candidate.lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
        )
        OR NOT EXISTS (
            SELECT 1 FROM audit_events AS audit WHERE audit.action = 'INVOICE_CORRECTION_UPDATED'
              AND audit.entity_type = 'INVOICE_CORRECTION' AND audit.entity_id = NEW.id
              AND json_extract(audit.details_json, '$.revision_no') = NEW.revision_no
              AND json_extract(audit.details_json, '$.before.target_company_id') IS OLD.target_company_id
              AND json_extract(audit.details_json, '$.after.target_company_id') IS NEW.target_company_id
              AND json_extract(audit.details_json, '$.before.recalculate_settlements') = {recalc_sql('OLD')}
              AND json_extract(audit.details_json, '$.after.recalculate_settlements') = {recalc_sql('NEW')}
              AND length(trim(json_extract(audit.details_json, '$.reason'))) >= 2
        )
    );
    SELECT RAISE(ABORT, 'invoice_correction_original_sources_changed')
    WHERE {POLICY_CHANGED} AND {recalc_sql('NEW')} = 0 AND (
        NOT EXISTS (SELECT 1 FROM invoice_sources WHERE invoice_id = NEW.original_invoice_id)
        OR EXISTS (
            SELECT 1 FROM invoice_sources AS source
            JOIN quarterly_settlements AS settlement ON settlement.id = source.settlement_id
            WHERE source.invoice_id = NEW.original_invoice_id
              AND (settlement.status != 'FINALIZED' OR source.locked_amount_cents != settlement.service_fee_cents)
        )
        OR EXISTS (
            SELECT 1 FROM quarterly_settlements AS settlement JOIN invoices AS original ON original.id = NEW.original_invoice_id
            WHERE settlement.client_id = original.client_id AND settlement.year = original.year
              AND settlement.quarter = original.quarter AND settlement.status = 'FINALIZED'
              AND NOT EXISTS (SELECT 1 FROM invoice_sources AS source
                              WHERE source.invoice_id = NEW.original_invoice_id AND source.settlement_id = settlement.id)
        )
    );
"""


def _replace_once(sql: str, old: str, new: str) -> str:
    if sql.count(old) != 1:
        raise RuntimeError("更正升级前触发器与预期不一致，已停止")
    return sql.replace(old, new, 1)


def upgraded_correction_triggers(previous: dict[str, str]) -> dict[str, str]:
    result = {}
    for name in ("trg_invoice_correction_validate_insert", "trg_invoice_correction_validate_update"):
        sql = previous[name]
        if name.endswith("update"):
            sql = _replace_once(sql, "OR NEW.target_company_id IS NOT OLD.target_company_id", "")
            sql = _replace_once(sql, "NEW.replacement_invoice_id IS NOT NULL AND NEW.target_company_id IS NULL",
                                f"NEW.replacement_invoice_id IS NOT NULL AND {recalc_sql('NEW')} = 1")
            sql = _replace_once(sql, "AND (NEW.target_company_id IS NULL OR (", f"AND ({recalc_sql('NEW')} = 1 OR (")
            sql = _replace_once(sql, "WHERE NEW.target_company_id IS NOT NULL AND NEW.replacement_invoice_id IS NOT NULL AND (",
                                f"WHERE {recalc_sql('NEW')} = 0 AND NEW.replacement_invoice_id IS NOT NULL AND (")
            sql = sql.rstrip().removesuffix("END") + AMEND_GUARD + POLICY_GUARD + "END"
        else:
            sql = sql.rstrip().removesuffix("END") + POLICY_GUARD + "\nSELECT RAISE(ABORT, 'invoice_correction_revision_invalid') WHERE NEW.revision_no != 1;\nEND"
        result[name] = sql
    for name in ("trg_settlement_validate_finalize", "trg_settlement_validate_void"):
        result[name] = _replace_once(previous[name], "correction.status = 'OPEN' AND correction.target_company_id IS NOT NULL",
                                     f"correction.status = 'OPEN' AND {recalc_sql('correction')} = 0")
    return result


def correction_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    columns = {row[1]: row for row in execute('PRAGMA table_info("invoice_corrections")')}
    if not CORRECTION_COLUMNS <= columns.keys():
        return False
    if columns['recalculate_settlements'][2].upper() != 'BOOLEAN' or columns['recalculate_settlements'][3] != 0:
        return False
    if columns['revision_no'][2].upper() != 'INTEGER' or columns['revision_no'][3] != 1 or str(columns['revision_no'][4]).strip("'\"") != '1':
        return False
    normalize = lambda value: " ".join(value.casefold().split())
    triggers = {name: normalize(sql) for name, sql in execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
    update = triggers.get('trg_invoice_correction_validate_update', '')
    insert = triggers.get('trg_invoice_correction_validate_insert', '')
    return (normalize(AMEND_GUARD + POLICY_GUARD) in update
            and normalize(POLICY_GUARD) in insert
            and 'invoice_correction_revision_invalid' in insert
            and normalize(f"new.replacement_invoice_id is not null and {recalc_sql('new')} = 1") in update
            and normalize(f"where {recalc_sql('new')} = 0 and new.replacement_invoice_id is not null") in update
            and normalize(f"and ({recalc_sql('new')} = 1 or (") in update
            and all(normalize(f"correction.status = 'open' and {recalc_sql('correction')} = 0") in triggers.get(name, '')
                    for name in ('trg_settlement_validate_finalize', 'trg_settlement_validate_void')))
