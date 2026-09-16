import sqlite3

from alembic import command
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

import app.database as database_module
from app.main import app
from app.services.backup import _validate_sqlite_database
from app.services.invoice_correction_contract import CORRECTION_REVISION, CORRECTION_COLUMNS, correction_schema_is_current
from test_deletion_guard_migration import _settings, _alembic_config, _trigger_sql
from test_invoice_company_correction import _company
from test_payment_corrections import WRITE_HEADERS, _create, _issued_case
from test_workflow_guard_migration import _business_rows

PREVIOUS = 'f1c0a915b100'


def test_migration_preserves_open_corrections_and_all_existing_financial_fields(tmp_path, monkeypatch):
    # Create populated synthetic records through the public API, then form an
    # exact previous schema using its real migration-generated guards.
    with TestClient(app, headers=WRITE_HEADERS) as client:
        originals = [_issued_case(client, 'MIGCORRA')[2], _issued_case(client, 'MIGCORRB')[2]]
        target = _company(client)
        corrections = [
            _create(client, f"/api/invoices/{originals[0]['id']}/corrections", {'reason': '历史普通更正'}),
            _create(client, f"/api/invoices/{originals[1]['id']}/corrections", {'reason': '历史公司更正', 'target_company_id': target['id']}),
        ]
    settings = _settings(tmp_path, 'previous', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    old_triggers = _trigger_sql(settings.database_path)
    with sqlite3.connect(database_module.settings.database_path) as source, sqlite3.connect(settings.database_path) as copy:
        source.backup(copy)
        names = ('trg_invoice_correction_validate_insert', 'trg_invoice_correction_validate_update',
                 'trg_settlement_validate_finalize', 'trg_settlement_validate_void')
        for name in names:
            copy.execute(f'DROP TRIGGER "{name}"')
        for column in CORRECTION_COLUMNS:
            copy.execute(f'ALTER TABLE invoice_corrections DROP COLUMN "{column}"')
        for name in names:
            copy.execute(old_triggers[name])
        copy.execute('UPDATE alembic_version SET version_num=?', (PREVIOUS,))
    before = _business_rows(settings.database_path)
    command.upgrade(config, 'head')
    after = _business_rows(settings.database_path)
    after['invoice_corrections'] = [row[:-2] for row in after['invoice_corrections']]
    assert after == before
    with sqlite3.connect(settings.database_path) as sql:
        assert correction_schema_is_current(sql)
        assert sql.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert sql.execute('PRAGMA foreign_key_check').fetchall() == []
        for correction in corrections:
            assert sql.execute('SELECT recalculate_settlements,revision_no FROM invoice_corrections WHERE id=?', (correction['id'],)).fetchone() == (None, 1)
        assert len(_trigger_sql(settings.database_path)) == 60


def test_migration_failure_rolls_back_columns_and_triggers(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'rollback', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    before = _trigger_sql(settings.database_path)
    created = 0

    def fail_second(conn, cursor, statement, parameters, context, executemany):
        nonlocal created
        if statement.lstrip().upper().startswith('CREATE TRIGGER'):
            created += 1
            if created == 2:
                raise RuntimeError('synthetic correction DDL interruption')

    event.listen(Engine, 'before_cursor_execute', fail_second)
    try:
        with pytest.raises(RuntimeError, match='synthetic correction DDL interruption'):
            command.upgrade(config, 'head')
    finally:
        event.remove(Engine, 'before_cursor_execute', fail_second)
    assert created == 2 and _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as sql:
        assert sql.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        assert not CORRECTION_COLUMNS & {row[1] for row in sql.execute('PRAGMA table_info(invoice_corrections)')}


@pytest.mark.parametrize('revision', [PREVIOUS, CORRECTION_REVISION])
def test_unversioned_current_and_previous_schema_are_verified_and_upgraded(tmp_path, monkeypatch, revision):
    settings = _settings(tmp_path, revision, monkeypatch)
    command.upgrade(_alembic_config(settings), revision)
    with sqlite3.connect(settings.database_path) as sql:
        sql.execute('DROP TABLE alembic_version')
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, 'engine', engine)
    monkeypatch.setattr(database_module, 'settings', settings)
    try:
        database_module.init_db()
        with sqlite3.connect(settings.database_path) as sql:
            assert sql.execute('SELECT version_num FROM alembic_version').fetchone() == (CORRECTION_REVISION,)
            assert correction_schema_is_current(sql)
        _validate_sqlite_database(settings.database_path)
    finally:
        engine.dispose()


def test_partial_policy_guard_is_rejected_by_startup_and_backup(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'tampered', monkeypatch)
    command.upgrade(_alembic_config(settings), 'head')
    with sqlite3.connect(settings.database_path) as sql:
        statement = _trigger_sql(settings.database_path)['trg_invoice_correction_validate_update']
        sql.execute('DROP TRIGGER trg_invoice_correction_validate_update')
        sql.execute(statement.replace("audit.action = 'INVOICE_CORRECTION_UPDATED'", "audit.action != 'INVOICE_CORRECTION_UPDATED'"))
    with pytest.raises(ValueError, match='统一更正'):
        _validate_sqlite_database(settings.database_path)
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, 'engine', engine)
    monkeypatch.setattr(database_module, 'settings', settings)
    try:
        with pytest.raises(RuntimeError, match='统一更正'):
            database_module.init_db()
    finally:
        engine.dispose()
