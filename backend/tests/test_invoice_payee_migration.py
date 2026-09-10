from contextlib import closing
import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from app.services.backup import _validate_sqlite_database
import app.database as database_module
from app.services.invoice_group_contract import INVOICE_GROUP_REVISION
from app.services.invoice_payee_contract import PAYEE_REVISION, payee_trigger_sql_is_current
from test_deletion_guard_migration import _settings, _alembic_config, _trigger_sql
from test_workflow_guard_migration import _seed_history

PREVIOUS = 'e8b2c6d91a04'


def test_receiving_company_upgrade_preserves_all_original_columns_and_validates_backups(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'payee-history', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, 'd4f8a1c73b29')
    _seed_history(settings)
    command.upgrade(config, PREVIOUS)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute("""INSERT INTO invoices (settlement_id,client_id,year,quarter,fee_plan_id,company_id,fc_id,
            lifecycle_status,amount_cents,language,created_at,updated_at)
            SELECT id,client_id,year,quarter,fee_plan_id,company_id,fc_id,'DRAFT',12000,'zh',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP
            FROM quarterly_settlements WHERE quarter=1""")
        columns = {row[0]: [column[1] for column in sql.execute(f'PRAGMA table_info("{row[0]}")')]
                   for row in sql.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')").fetchall()}
        before = {table: sql.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in columns}
    command.upgrade(config, PAYEE_REVISION)
    with closing(sqlite3.connect(settings.database_path)) as sql:
        for table, names in columns.items():
            projected = ','.join(f'"{name}"' for name in names)
            assert sql.execute(f'SELECT {projected} FROM "{table}" ORDER BY rowid').fetchall() == before[table]
        assert sql.execute('SELECT payee_company_id FROM invoices').fetchall() == [(None,)]
        assert sql.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert sql.execute('PRAGMA foreign_key_check').fetchall() == []
    assert len(_trigger_sql(settings.database_path)) == 57
    assert payee_trigger_sql_is_current(_trigger_sql(settings.database_path))
    _validate_sqlite_database(settings.database_path)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        trigger = sql.execute("SELECT sql FROM sqlite_master WHERE name='trg_invoice_validate_issue'").fetchone()[0]
        sql.execute('DROP TRIGGER trg_invoice_validate_issue')
        sql.execute(trigger.replace('invoice_payee_company_target_mismatch', 'synthetic_missing_contract'))
    with pytest.raises(ValueError, match='收款公司'):
        _validate_sqlite_database(settings.database_path)


def test_receiving_company_migration_failure_rolls_back_columns_triggers_and_revision(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'payee-rollback', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    before = _trigger_sql(settings.database_path)
    count = 0
    def fail_create(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        if statement.lstrip().upper().startswith('CREATE TRIGGER'):
            count += 1
            if count == 2:
                raise RuntimeError('synthetic payee DDL failure')
    event.listen(Engine, 'before_cursor_execute', fail_create)
    try:
        with pytest.raises(RuntimeError, match='synthetic payee DDL failure'):
            command.upgrade(config, PAYEE_REVISION)
    finally:
        event.remove(Engine, 'before_cursor_execute', fail_create)
    assert _trigger_sql(settings.database_path) == before
    with closing(sqlite3.connect(settings.database_path)) as sql:
        assert sql.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        assert 'payee_company_id' not in {row[1] for row in sql.execute('PRAGMA table_info(invoices)')}
        assert 'target_company_id' not in {row[1] for row in sql.execute('PRAGMA table_info(invoice_corrections)')}


def test_receiving_company_migration_rejects_incomplete_previous_guards(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'payee-incomplete', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute('DROP TRIGGER trg_invoice_financial_header_update_lock')
    before = _trigger_sql(settings.database_path)
    with pytest.raises(RuntimeError, match='完整前序财务保护'):
        command.upgrade(config, PAYEE_REVISION)
    assert _trigger_sql(settings.database_path) == before


@pytest.mark.parametrize('revision', [PREVIOUS, PAYEE_REVISION])
def test_unstamped_payee_or_previous_database_gets_proven_schema(tmp_path, monkeypatch, revision):
    settings = _settings(tmp_path, 'payee-unstamped', monkeypatch)
    command.upgrade(_alembic_config(settings), revision)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute('DROP TABLE alembic_version')
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, 'engine', engine)
    monkeypatch.setattr(database_module, 'settings', settings)
    try:
        database_module.init_db()
        _validate_sqlite_database(settings.database_path)
        with closing(sqlite3.connect(settings.database_path)) as sql:
            assert sql.execute('SELECT version_num FROM alembic_version').fetchone() == (INVOICE_GROUP_REVISION,)
    finally:
        engine.dispose()


def test_unstamped_partial_payee_shape_is_not_stamped_as_current(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'payee-partial', monkeypatch)
    command.upgrade(_alembic_config(settings), PREVIOUS)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute('DROP TABLE alembic_version')
        sql.execute('ALTER TABLE invoices ADD COLUMN payee_company_id INTEGER REFERENCES companies(id) ON DELETE RESTRICT')
    before = _trigger_sql(settings.database_path)
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, 'engine', engine)
    monkeypatch.setattr(database_module, 'settings', settings)
    try:
        with pytest.raises(RuntimeError, match='收款公司字段或保护不完整'):
            database_module.init_db()
        assert _trigger_sql(settings.database_path) == before
        with closing(sqlite3.connect(settings.database_path)) as sql:
            assert not sql.execute("SELECT name FROM sqlite_master WHERE name='alembic_version'").fetchall()
    finally:
        engine.dispose()
