from contextlib import closing
import sqlite3

from alembic import command
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.services.backup import _validate_sqlite_database
from app.services.release_100_contract import RELEASE_100_REVISION, HWM_TRIGGER_SQL, release_100_schema_is_current
from test_deletion_guard_migration import _settings, _alembic_config, _trigger_sql
from test_workflow_guard_migration import _seed_history, _business_rows

PREVIOUS = 'c6f3a8d92e10'


def test_release_100_preserves_original_financial_rows_and_adds_protected_metadata(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'release100-history', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, 'd4f8a1c73b29')
    _seed_history(settings)
    command.upgrade(config, PREVIOUS)
    with closing(sqlite3.connect(settings.database_path)) as sql:
        columns = {row[0]: [column[1] for column in sql.execute(f'PRAGMA table_info("{row[0]}")')]
            for row in sql.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')").fetchall()}
        before = {table: sql.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in columns}
    command.upgrade(config, RELEASE_100_REVISION)
    with closing(sqlite3.connect(settings.database_path)) as sql:
        for table, names in columns.items():
            projection = ','.join(f'"{name}"' for name in names)
            assert sql.execute(f'SELECT {projection} FROM "{table}" ORDER BY rowid').fetchall() == before[table]
        assert release_100_schema_is_current(sql)
        assert sql.execute('SELECT hwm_source_type,hwm_override_reason,hwm_override_confirmed FROM settlement_account_lines').fetchall() == [(None,None,0),(None,None,0)]
        assert sql.execute('PRAGMA foreign_key_check').fetchall() == []
        assert sql.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    assert len(_trigger_sql(settings.database_path)) == 60
    _validate_sqlite_database(settings.database_path)


@pytest.mark.parametrize('stage', ['column','table','trigger'])
def test_release_100_migration_failure_is_atomic(tmp_path, monkeypatch, stage):
    settings = _settings(tmp_path, f'release100-failure-{stage}', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    before, triggers = _business_rows(settings.database_path), _trigger_sql(settings.database_path)
    def fail(conn, cursor, statement, parameters, context, executemany):
        if (stage == 'column' and 'ADD COLUMN hwm_override_reason' in statement
            or stage == 'table' and 'CREATE TABLE invoice_monthly_sequences' in statement
            or stage == 'trigger' and 'CREATE TRIGGER trg_settlement_hwm_confirm_finalize' in statement):
            raise RuntimeError('synthetic release100 failure')
    event.listen(Engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic release100 failure'):
            command.upgrade(config, RELEASE_100_REVISION)
    finally:
        event.remove(Engine, 'before_cursor_execute', fail)
    assert _business_rows(settings.database_path) == before
    assert _trigger_sql(settings.database_path) == triggers
    _validate_sqlite_database(settings.database_path)
    command.upgrade(config, RELEASE_100_REVISION)
    _validate_sqlite_database(settings.database_path)


def test_backup_rejects_changed_hwm_guard(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'release100-tamper', monkeypatch)
    command.upgrade(_alembic_config(settings), RELEASE_100_REVISION)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        name = next(iter(HWM_TRIGGER_SQL))
        sql.execute(f'DROP TRIGGER {name}')
        sql.execute(f'CREATE TRIGGER {name} BEFORE UPDATE ON quarterly_settlements BEGIN SELECT 1; END')
    with pytest.raises(ValueError, match='HWM|月度'):
        _validate_sqlite_database(settings.database_path)
