from contextlib import closing
import sqlite3

from alembic import command
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.services.annual_period_contract import ANNUAL_REVISION, PERIOD_TRIGGER_SQL, annual_schema_is_current
from app.services.backup import validate_database_structure
from test_deletion_guard_migration import _settings, _alembic_config, _trigger_sql
from test_workflow_guard_migration import _seed_history


def test_annual_migration_preserves_every_old_column_including_skipped_quarter_history(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'old', monkeypatch)
    config = _alembic_config(settings)
    # A genuine old schema with old finalized history, not a schema fabricated
    # from current ORM metadata. Skipped history remains immutable.
    command.upgrade(config, 'd4f8a1c73b29')
    _seed_history(settings)
    command.upgrade(config, 'd016f0ee1160')
    with closing(sqlite3.connect(settings.database_path)) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')")]
        columns = {t: ','.join('"'+r[1]+'"' for r in db.execute(f'PRAGMA table_info("{t}")')) for t in tables}
        before = {t: db.execute(f'SELECT {columns[t]} FROM "{t}" ORDER BY rowid').fetchall() for t in tables}
    triggers = _trigger_sql(settings.database_path)
    command.upgrade(config, 'head')
    with closing(sqlite3.connect(settings.database_path)) as db:
        for table in tables:
            where = "WHERE key != 'settlement_cycle'" if table == 'app_settings' else ''
            assert db.execute(f'SELECT {columns[table]} FROM "{table}" {where} ORDER BY rowid').fetchall() == before[table]
        assert db.execute("SELECT DISTINCT period_type FROM quarterly_settlements").fetchall() == [('QUARTERLY',)]
        assert db.execute("SELECT value FROM app_settings WHERE key='settlement_cycle'").fetchone() == ('QUARTERLY',)
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == (ANNUAL_REVISION,)
        assert annual_schema_is_current(db)
        validate_database_structure(db)
        assert db.execute('PRAGMA foreign_key_check').fetchall() == []
    after = _trigger_sql(settings.database_path)
    assert set(after) - set(triggers) == set(PERIOD_TRIGGER_SQL)
    assert {n for n in triggers if triggers[n] != after[n]} == {'trg_settlement_validate_finalize'}


@pytest.mark.parametrize('stage', ['second_column', 'finalize', 'period_guard'])
def test_annual_migration_failure_rolls_back_all_ddl_and_configuration(tmp_path, monkeypatch, stage):
    settings = _settings(tmp_path, 'rollback', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, 'd016f0ee1160')
    before = _trigger_sql(settings.database_path)
    def fail(conn, cursor, statement, parameters, context, executemany):
        prefix = {'second_column': 'ALTER TABLE invoices ADD COLUMN',
                  'finalize': 'CREATE TRIGGER trg_settlement_validate_finalize',
                  'period_guard': 'CREATE TRIGGER trg_annual_settlement_dates'}[stage]
        if statement.startswith(prefix):
            raise RuntimeError('synthetic annual DDL failure')
    event.listen(Engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic annual DDL'):
            command.upgrade(config, 'head')
    finally:
        event.remove(Engine, 'before_cursor_execute', fail)
    assert _trigger_sql(settings.database_path) == before
    with closing(sqlite3.connect(settings.database_path)) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('d016f0ee1160',)
        assert db.execute("SELECT value FROM app_settings WHERE key='settlement_cycle'").fetchone() is None
        for table in ('quarterly_settlements', 'invoices'):
            assert 'period_type' not in {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
    command.upgrade(config, 'head')


def test_missing_annual_guard_is_rejected_by_backup_validation(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'guard', monkeypatch)
    command.upgrade(_alembic_config(settings), 'head')
    with closing(sqlite3.connect(settings.database_path)) as db:
        db.execute('DROP TRIGGER trg_cycle_setting_update')
        with pytest.raises(ValueError):
            validate_database_structure(db)
