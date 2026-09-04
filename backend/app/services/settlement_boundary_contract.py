from __future__ import annotations


BOUNDARY_TRIGGER_NAMES = frozenset(
    {
        "trg_settlement_validate_finalize",
        "trg_attachment_update_block_finalized_evidence",
        "trg_attachment_delete_block_finalized_evidence",
    }
)

LEGACY_TRANSACTION_BOUNDARY = "transaction_record.transaction_date > line.start_date"
CURRENT_TRANSACTION_BOUNDARY = (
    "transaction_record.transaction_date > ( select opening_snapshot.as_of_date "
    "from balance_snapshots as opening_snapshot "
    "where opening_snapshot.id = line.beginning_snapshot_id )"
)
CURRENT_INITIAL_BOUNDARY = (
    "strftime('%m-%d', line.start_date) in ('01-01', '04-01', '07-01', '10-01')"
)


def _normalized(sql: str) -> str:
    return " ".join(sql.casefold().split())


def settlement_boundary_trigger_sql_is_legacy(trigger_sql: dict[str, str]) -> bool:
    if not BOUNDARY_TRIGGER_NAMES.issubset(trigger_sql):
        return False
    normalized = {name: _normalized(trigger_sql[name]) for name in BOUNDARY_TRIGGER_NAMES}
    return (
        normalized["trg_settlement_validate_finalize"].count(LEGACY_TRANSACTION_BOUNDARY) == 3
        and normalized["trg_attachment_update_block_finalized_evidence"].count(
            LEGACY_TRANSACTION_BOUNDARY
        )
        == 2
        and normalized["trg_attachment_delete_block_finalized_evidence"].count(
            LEGACY_TRANSACTION_BOUNDARY
        )
        == 1
        and CURRENT_TRANSACTION_BOUNDARY
        not in normalized["trg_settlement_validate_finalize"]
    )


def settlement_boundary_trigger_sql_is_current(trigger_sql: dict[str, str]) -> bool:
    if not BOUNDARY_TRIGGER_NAMES.issubset(trigger_sql):
        return False
    normalized = {name: _normalized(trigger_sql[name]) for name in BOUNDARY_TRIGGER_NAMES}
    finalize_sql = normalized["trg_settlement_validate_finalize"]
    return (
        finalize_sql.count(CURRENT_TRANSACTION_BOUNDARY) == 3
        and normalized["trg_attachment_update_block_finalized_evidence"].count(
            CURRENT_TRANSACTION_BOUNDARY
        )
        == 2
        and normalized["trg_attachment_delete_block_finalized_evidence"].count(
            CURRENT_TRANSACTION_BOUNDARY
        )
        == 1
        and LEGACY_TRANSACTION_BOUNDARY not in " ".join(normalized.values())
        and CURRENT_INITIAL_BOUNDARY in finalize_sql
        and "beginning.as_of_date = date(line.start_date, '-1 day')" in finalize_sql
    )
