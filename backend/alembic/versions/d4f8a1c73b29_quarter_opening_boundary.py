"""Use the previous quarter-end snapshot as the calendar-quarter opening boundary.

Revision ID: d4f8a1c73b29
Revises: c1a7d5e9b402
"""

from __future__ import annotations

from alembic import op


revision = "d4f8a1c73b29"
down_revision = "c1a7d5e9b402"
branch_labels = None
depends_on = None


TARGET_TRIGGER_NAMES = (
    "trg_settlement_validate_finalize",
    "trg_attachment_update_block_finalized_evidence",
    "trg_attachment_delete_block_finalized_evidence",
)
OLD_TRANSACTION_BOUNDARY = "transaction_record.transaction_date > line.start_date"
NEW_TRANSACTION_BOUNDARY = """transaction_record.transaction_date > (
                            SELECT opening_snapshot.as_of_date
                            FROM balance_snapshots AS opening_snapshot
                            WHERE opening_snapshot.id = line.beginning_snapshot_id
                          )"""
OLD_INITIAL_BOUNDARY = (
    "OR (line.previous_line_id IS NULL AND beginning.as_of_date != line.start_date)"
)
NEW_INITIAL_BOUNDARY = """OR (line.previous_line_id IS NULL AND NOT (
                        beginning.as_of_date = line.start_date
                        OR (
                          strftime('%m-%d', line.start_date) IN
                            ('01-01', '04-01', '07-01', '10-01')
                          AND beginning.as_of_date = date(line.start_date, '-1 day')
                        )
                      ))"""
EXPECTED_OLD_BOUNDARY_COUNTS = {
    "trg_settlement_validate_finalize": 3,
    "trg_attachment_update_block_finalized_evidence": 2,
    "trg_attachment_delete_block_finalized_evidence": 1,
}


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name IN (?, ?, ?)",
        TARGET_TRIGGER_NAMES,
    ).fetchall()
    trigger_sql = {str(row[0]): str(row[1] or "") for row in rows}
    if set(trigger_sql) != set(TARGET_TRIGGER_NAMES):
        raise RuntimeError("季度边界迁移缺少Settlement保护Trigger")

    for name, expected_count in EXPECTED_OLD_BOUNDARY_COUNTS.items():
        if trigger_sql[name].count(OLD_TRANSACTION_BOUNDARY) != expected_count:
            raise RuntimeError("季度边界迁移检测到非预期Settlement Trigger语义")
    if trigger_sql["trg_settlement_validate_finalize"].count(OLD_INITIAL_BOUNDARY) != 1:
        raise RuntimeError("季度边界迁移检测到非预期Beginning Snapshot规则")

    updated_sql: dict[str, str] = {}
    for name in TARGET_TRIGGER_NAMES:
        updated_sql[name] = trigger_sql[name].replace(
            OLD_TRANSACTION_BOUNDARY,
            NEW_TRANSACTION_BOUNDARY,
        )
    updated_sql["trg_settlement_validate_finalize"] = updated_sql[
        "trg_settlement_validate_finalize"
    ].replace(OLD_INITIAL_BOUNDARY, NEW_INITIAL_BOUNDARY)

    for name in TARGET_TRIGGER_NAMES:
        op.execute(f'DROP TRIGGER "{name}"')
    for name in TARGET_TRIGGER_NAMES:
        op.execute(updated_sql[name])

    refreshed = {
        str(row[0]): str(row[1] or "")
        for row in connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND name IN (?, ?, ?)",
            TARGET_TRIGGER_NAMES,
        ).fetchall()
    }
    for name, expected_count in EXPECTED_OLD_BOUNDARY_COUNTS.items():
        if refreshed.get(name, "").count(NEW_TRANSACTION_BOUNDARY) != expected_count:
            raise RuntimeError("季度边界迁移未完整更新Settlement Trigger")
    if refreshed["trg_settlement_validate_finalize"].count(NEW_INITIAL_BOUNDARY) != 1:
        raise RuntimeError("季度边界迁移未完整更新Beginning Snapshot规则")


def downgrade() -> None:
    raise RuntimeError("季度边界规则已用于财务结算，禁止原地降级；请恢复升级前完整备份")
