"""2026-09-18 finance meeting schema and permanent evidence history contract."""
MEETING_REVISION = "c921f0181100"
EVIDENCE_TRIGGER_SQL = {
    "trg_attachment_financial_history_update": """CREATE TRIGGER trg_attachment_financial_history_update
BEFORE UPDATE ON attachments
WHEN (OLD.entity_type IN ('TRANSACTION', 'SNAPSHOT') AND OLD.entity_id IS NOT NULL)
  OR EXISTS (SELECT 1 FROM transactions WHERE attachment_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'financial_evidence_history_immutable')
    WHERE NEW.id IS NOT OLD.id OR NEW.entity_type IS NOT OLD.entity_type OR NEW.entity_id IS NOT OLD.entity_id
       OR NEW.original_name IS NOT OLD.original_name OR NEW.stored_path IS NOT OLD.stored_path
       OR NEW.sha256 IS NOT OLD.sha256 OR NEW.mime_type IS NOT OLD.mime_type
       OR NEW.size_bytes IS NOT OLD.size_bytes OR NEW.created_at IS NOT OLD.created_at
       OR (NEW.superseded IS NOT OLD.superseded AND NOT (OLD.superseded = 0 AND NEW.superseded = 1));
END""",
    "trg_attachment_financial_history_delete": """CREATE TRIGGER trg_attachment_financial_history_delete
BEFORE DELETE ON attachments
WHEN (OLD.entity_type IN ('TRANSACTION', 'SNAPSHOT') AND OLD.entity_id IS NOT NULL)
  OR EXISTS (SELECT 1 FROM transactions WHERE attachment_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'financial_evidence_history_immutable');
END""",
}


def _normalized(sql: str) -> str:
    return " ".join(sql.casefold().split())


def upgraded_meeting_finalize(sql: str) -> str:
    replacements = {
        "OR closing.eligible_for_closing != 1": "",
        "transaction_record.transaction_type = 'CONTRIBUTION'": "transaction_record.transaction_type IN ('CONTRIBUTION', 'MONTHLY_CONTRIBUTION')",
        "WHERE attachment.entity_type = 'SNAPSHOT'": "WHERE attachment.entity_type = 'SNAPSHOT' AND attachment.superseded = 0",
        "WHERE attachment.entity_type = 'TRANSACTION'": "WHERE attachment.entity_type = 'TRANSACTION' AND attachment.superseded = 0",
    }
    for old, new in replacements.items():
        if sql.count(old) != 1:
            raise RuntimeError("财务会议升级前Finalize保护与预期不一致，已停止")
        sql = sql.replace(old, new)
    return sql


def meeting_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    for table in ("fcs", "fee_plans"):
        if "code" in {row[1] for row in execute(f'PRAGMA table_info("{table}")')}:
            return False
    columns = {row[1]: row for row in execute('PRAGMA table_info("attachments")')}
    if ("superseded" not in columns or columns["superseded"][2].upper() != "BOOLEAN"
            or columns["superseded"][3] != 1 or str(columns["superseded"][4]) != "0"):
        return False
    if execute("SELECT 1 FROM attachments WHERE typeof(superseded) != 'integer' OR superseded NOT IN (0,1) LIMIT 1").fetchone():
        return False
    triggers = dict(execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall())
    if any(_normalized(triggers.get(name, "")) != _normalized(sql) for name, sql in EVIDENCE_TRIGGER_SQL.items()):
        return False
    if any(f"trg_{table}_code_unique_insert" in triggers for table in ("fcs", "fee_plans")):
        return False
    finalize = _normalized(triggers.get("trg_settlement_validate_finalize", ""))
    return (
        "eligible_for_closing" not in finalize
        and finalize.count("transaction_record.transaction_type in ('contribution', 'monthly_contribution')") == 1
        and finalize.count("attachment.superseded = 0") == 2
        and "closing.as_of_date != line.closing_date" in finalize
        and "closing.account_id != line.account_id" in finalize
        and "settlement_closing_date_invalid" in finalize
    )
