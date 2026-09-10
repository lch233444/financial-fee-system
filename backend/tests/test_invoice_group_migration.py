from contextlib import closing
import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

import app.database as database_module
from app.services.backup import _validate_sqlite_database
from app.services.invoice_group_contract import INVOICE_GROUP_REVISION, invoice_group_schema_is_current
from test_deletion_guard_migration import _settings, _alembic_config, _trigger_sql
from test_workflow_guard_migration import _seed_history, _business_rows

PREVIOUS = "a2e6c9d74f31"


def test_client_quarter_upgrade_preserves_history_and_rejects_regressed_backup_guard(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "group-history", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "d4f8a1c73b29")
    _seed_history(settings)
    command.upgrade(config, PREVIOUS)
    before = _business_rows(settings.database_path)
    command.upgrade(config, INVOICE_GROUP_REVISION)
    assert _business_rows(settings.database_path) == before
    assert len(_trigger_sql(settings.database_path)) == 57
    _validate_sqlite_database(settings.database_path)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        assert invoice_group_schema_is_current(sql)
        trigger = sql.execute("SELECT sql FROM sqlite_master WHERE name='trg_invoice_validate_issue'").fetchone()[0]
        sql.execute("DROP TRIGGER trg_invoice_validate_issue")
        sql.execute(trigger.replace("AND settlement.quarter = NEW.quarter", "AND settlement.quarter = NEW.quarter AND settlement.fee_plan_id = NEW.fee_plan_id"))
    with pytest.raises(ValueError, match="客户季度合并"):
        _validate_sqlite_database(settings.database_path)


def test_client_quarter_migration_rejects_multiple_active_legacy_bills_without_mutation(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "group-conflict", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "d4f8a1c73b29")
    _seed_history(settings)
    command.upgrade(config, PREVIOUS)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute("INSERT INTO fee_plans (company_id,name,code,fee_rate_bps,calculation_method,active,created_at,updated_at) SELECT company_id,'Second Plan','P2',1000,'HIGH_WATER_MARK',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP FROM fee_plans LIMIT 1")
        for plan in sql.execute("SELECT id FROM fee_plans").fetchall():
            sql.execute("""INSERT INTO invoices (settlement_id,client_id,year,quarter,fee_plan_id,company_id,fc_id,lifecycle_status,amount_cents,language,created_at,updated_at)
                SELECT id,client_id,year,quarter,?,company_id,fc_id,'DRAFT',12000,'zh',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
                FROM quarterly_settlements WHERE quarter=1""", plan)
    before, triggers = _business_rows(settings.database_path), _trigger_sql(settings.database_path)
    with pytest.raises(RuntimeError, match="多张有效Invoice"):
        command.upgrade(config, INVOICE_GROUP_REVISION)
    assert _business_rows(settings.database_path) == before and _trigger_sql(settings.database_path) == triggers
    with closing(sqlite3.connect(settings.database_path)) as sql:
        assert sql.execute("SELECT version_num FROM alembic_version").fetchone() == (PREVIOUS,)


def test_client_quarter_ddl_failure_rolls_back_index_and_all_triggers(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "group-rollback", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    before = _trigger_sql(settings.database_path)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith("CREATE TRIGGER trg_invoice_validate_issue"):
            raise RuntimeError("synthetic group DDL failure")
    event.listen(Engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic group DDL failure"):
            command.upgrade(config, INVOICE_GROUP_REVISION)
    finally:
        event.remove(Engine, "before_cursor_execute", fail)
    assert _trigger_sql(settings.database_path) == before
    with closing(sqlite3.connect(settings.database_path)) as sql:
        assert sql.execute("SELECT version_num FROM alembic_version").fetchone() == (PREVIOUS,)
        assert "uq_invoices_active_client_period_plan" in {row[1] for row in sql.execute("PRAGMA index_list(invoices)")}
        assert "uq_invoices_active_client_period" not in {row[1] for row in sql.execute("PRAGMA index_list(invoices)")}


def test_unstamped_client_quarter_database_is_recognized_without_rerunning_old_migration(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "group-unstamped", monkeypatch)
    command.upgrade(_alembic_config(settings), INVOICE_GROUP_REVISION)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute("DROP TABLE alembic_version")
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "settings", settings)
    try:
        database_module.init_db()
        _validate_sqlite_database(settings.database_path)
        with closing(sqlite3.connect(settings.database_path)) as sql:
            assert sql.execute("SELECT version_num FROM alembic_version").fetchone() == (INVOICE_GROUP_REVISION,)
    finally:
        engine.dispose()
