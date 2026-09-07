"""Schema contract for the 0.2.21 cash-period and correction-source guards."""

WORKFLOW_TRIGGER_NAMES = frozenset({
    "trg_transactions_block_finalized_period",
    "trg_transactions_update_block_frozen_period",
    "trg_transactions_delete_block_frozen_period",
    "trg_settlement_validate_finalize",
    "trg_invoice_correction_validate_update",
})


def workflow_trigger_sql_is_legacy(trigger_sql: dict[str, str]) -> bool:
    if not WORKFLOW_TRIGGER_NAMES.issubset(trigger_sql):
        return False
    sql = " ".join(" ".join(trigger_sql[name].casefold().split()) for name in WORKFLOW_TRIGGER_NAMES)
    return (
        "settlement_missing_previous_quarter" not in sql
        and "invoice_correction_late_source_group_invalid" not in sql
        and "new.transaction_date > (" not in sql
        and "old.transaction_date > (" not in sql
        and "having count(distinct original_source.id) != 1" in sql
    )


def workflow_trigger_sql_is_current(trigger_sql: dict[str, str]) -> bool:
    if not WORKFLOW_TRIGGER_NAMES.issubset(trigger_sql):
        return False
    sql = {name: " ".join(trigger_sql[name].casefold().split()) for name in WORKFLOW_TRIGGER_NAMES}
    for name, prefixes in {
        "trg_transactions_block_finalized_period": ("new",),
        "trg_transactions_update_block_frozen_period": ("old", "new"),
        "trg_transactions_delete_block_frozen_period": ("old",),
    }.items():
        for prefix in prefixes:
            boundary = f"({prefix}.transaction_date >= line.start_date or {prefix}.transaction_date > ( select opening_snapshot.as_of_date from balance_snapshots as opening_snapshot where opening_snapshot.id = line.beginning_snapshot_id ))"
            if boundary not in sql[name]:
                return False
    finalize = sql["trg_settlement_validate_finalize"]
    correction = sql["trg_invoice_correction_validate_update"]
    return (
        "settlement_missing_previous_quarter" in finalize
        and "immediate.year * 4 + immediate.quarter = new.year * 4 + new.quarter - 1" in finalize
        and "invoice_correction_late_source_group_invalid" in correction
        and "replacement_settlement.platform_id in (" in correction
        and "having count(distinct original_source.id) > 1" in correction
        and "having count(distinct lineage.replacement_source_id) != 1" in correction
    )
