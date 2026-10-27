"""Additive annual-period migration and its independently checked SQL guards."""
ANNUAL_REVISION = "e027a0ee1300"
CYCLE_KEY = "settlement_cycle"
OLD_CONTINUITY = "immediate.year * 4 + immediate.quarter = NEW.year * 4 + NEW.quarter - 1"
NEW_CONTINUITY = "immediate.year * 4 + immediate.quarter = NEW.year * 4 + NEW.quarter - CASE WHEN NEW.period_type = 'ANNUAL' THEN 4 ELSE 1 END"
OLD_CLOSING = "CASE NEW.quarter\n                WHEN 1 THEN '03-31'"
NEW_CLOSING = "CASE NEW.quarter\n                WHEN 0 THEN '12-31' WHEN 1 THEN '03-31'"


def upgrade_finalize(sql: str) -> str:
    for old, new in ((OLD_CONTINUITY, NEW_CONTINUITY), (OLD_CLOSING, NEW_CLOSING)):
        if sql.count(old) != 1:
            raise RuntimeError("年度升级前结算保护不符合精确前序，已停止")
        sql = sql.replace(old, new)
    return sql


def legacy_finalize(sql: str) -> str:
    """Canonical legacy view for existing guards; annual guards are checked too."""
    return sql.replace(NEW_CONTINUITY, OLD_CONTINUITY).replace(NEW_CLOSING, OLD_CLOSING)


PERIOD_TRIGGER_SQL = {
    "trg_cycle_setting_insert": """CREATE TRIGGER trg_cycle_setting_insert
    BEFORE INSERT ON app_settings WHEN NEW.key = 'settlement_cycle'
    BEGIN
      SELECT RAISE(ABORT, 'settlement_cycle_invalid') WHERE NEW.value NOT IN ('QUARTERLY','ANNUAL');
      SELECT RAISE(ABORT, 'settlement_cycle_history_exists') WHERE EXISTS (SELECT 1 FROM quarterly_settlements) OR EXISTS (SELECT 1 FROM invoices);
    END""",
    "trg_cycle_setting_update": """CREATE TRIGGER trg_cycle_setting_update
    BEFORE UPDATE ON app_settings WHEN OLD.key = 'settlement_cycle' OR NEW.key = 'settlement_cycle'
    BEGIN
      SELECT RAISE(ABORT, 'settlement_cycle_invalid') WHERE NEW.key IS NOT OLD.key OR NEW.value NOT IN ('QUARTERLY','ANNUAL');
      SELECT RAISE(ABORT, 'settlement_cycle_history_exists') WHERE NEW.value IS NOT OLD.value AND
        (EXISTS (SELECT 1 FROM quarterly_settlements) OR EXISTS (SELECT 1 FROM invoices));
    END""",
    "trg_cycle_setting_delete": """CREATE TRIGGER trg_cycle_setting_delete
    BEFORE DELETE ON app_settings WHEN OLD.key = 'settlement_cycle'
    BEGIN SELECT RAISE(ABORT, 'settlement_cycle_delete_forbidden'); END""",
}
for table, prefix in (("quarterly_settlements", "settlement"), ("invoices", "invoice")):
    for action in ("INSERT", "UPDATE"):
        name = f"trg_{prefix}_period_{action.lower()}"
        immutable = "SELECT RAISE(ABORT, 'period_type_immutable') WHERE NEW.period_type IS NOT OLD.period_type;" if action == "UPDATE" else ""
        PERIOD_TRIGGER_SQL[name] = f"""CREATE TRIGGER {name} BEFORE {action} ON {table}
        BEGIN
          SELECT RAISE(ABORT, 'package_period_mismatch') WHERE
            NEW.period_type IS NOT (SELECT value FROM app_settings WHERE key='settlement_cycle')
            OR NOT ((NEW.period_type='ANNUAL' AND NEW.quarter=0) OR (NEW.period_type='QUARTERLY' AND NEW.quarter BETWEEN 1 AND 4))
            OR NEW.year NOT BETWEEN 2000 AND 2200;
          {immutable}
        END"""

PERIOD_TRIGGER_SQL["trg_annual_settlement_dates"] = """CREATE TRIGGER trg_annual_settlement_dates
BEFORE UPDATE OF status ON quarterly_settlements
WHEN NEW.status='FINALIZED' AND OLD.status!='FINALIZED' AND NEW.period_type='ANNUAL'
BEGIN
  SELECT RAISE(ABORT, 'settlement_period_dates_invalid') WHERE
    NEW.start_date < printf('%04d-%s', NEW.year, CASE NEW.quarter WHEN 0 THEN '01-01' WHEN 1 THEN '01-01' WHEN 2 THEN '04-01' WHEN 3 THEN '07-01' WHEN 4 THEN '10-01' END)
    OR NEW.closing_date > printf('%04d-%s', NEW.year, CASE NEW.quarter WHEN 0 THEN '12-31' WHEN 1 THEN '03-31' WHEN 2 THEN '06-30' WHEN 3 THEN '09-30' WHEN 4 THEN '12-31' END)
    OR EXISTS (SELECT 1 FROM settlement_account_lines line WHERE line.settlement_id=NEW.id AND (
      line.start_date < NEW.start_date OR line.closing_date > NEW.closing_date OR line.closing_date < line.start_date
      OR (NEW.period_type='ANNUAL' AND line.previous_line_id IS NULL AND EXISTS (
        SELECT 1 FROM balance_snapshots opening WHERE opening.id=line.beginning_snapshot_id
        AND opening.as_of_date IS NOT line.start_date
        AND NOT (line.start_date=printf('%04d-01-01',NEW.year) AND opening.as_of_date=date(line.start_date,'-1 day'))
      ))
    ));
END"""


def annual_schema_is_current(connection) -> bool:
    execute = getattr(connection, "exec_driver_sql", None) or connection.execute
    normalize = lambda sql: " ".join(sql.lower().split())
    triggers = dict(execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall())
    for name, expected in PERIOD_TRIGGER_SQL.items():
        if normalize(triggers.get(name, "")) != normalize(expected):
            return False
    final = normalize(triggers.get("trg_settlement_validate_finalize", ""))
    if final.count(normalize(NEW_CONTINUITY)) != 1 or final.count(normalize(NEW_CLOSING)) != 1:
        return False
    row = execute("SELECT value FROM app_settings WHERE key='settlement_cycle'").fetchone()
    if row is None or row[0] not in ('QUARTERLY', 'ANNUAL'):
        return False
    for table in ('quarterly_settlements', 'invoices'):
        columns = {row[1]: row for row in execute(f'PRAGMA table_info("{table}")')}
        column = columns.get('period_type')
        if column is None or column[3] != 1 or column[4] != "'QUARTERLY'":
            return False
        if execute(f"SELECT 1 FROM {table} WHERE period_type IS NOT ? OR NOT ((period_type='ANNUAL' AND quarter=0) OR (period_type='QUARTERLY' AND quarter BETWEEN 1 AND 4)) LIMIT 1", (row[0],)).fetchone():
            return False
    return True
