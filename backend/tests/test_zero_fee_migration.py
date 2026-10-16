import sqlite3

from alembic import command
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.services.backup import validate_database_structure
from app.services.finance_meeting_contract import MEETING_REVISION
from app.services.zero_fee_invoice_contract import ZERO_FEE_REVISION, zero_fee_schema_is_current
from test_deletion_guard_migration import _alembic_config, _settings, _trigger_sql


def test_zero_fee_migration_changes_only_amount_guard_and_revision(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'zero-migration', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, MEETING_REVISION)
    before = _trigger_sql(settings.database_path)
    with sqlite3.connect(settings.database_path) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'")]
        rows = {t:db.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
    command.upgrade(config, 'head')
    after = _trigger_sql(settings.database_path)
    assert len(before) == len(after) == 60
    assert {n for n in before if before[n] != after[n]} == {'trg_invoice_validate_issue'}
    assert after['trg_invoice_validate_issue'] == before['trg_invoice_validate_issue'].replace('WHERE NEW.amount_cents <= 0;', 'WHERE NEW.amount_cents < 0;')
    with sqlite3.connect(settings.database_path) as db:
        assert zero_fee_schema_is_current(db)
        validate_database_structure(db)
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == (ZERO_FEE_REVISION,)
        assert all(db.execute(f'SELECT * FROM "{t}"').fetchall() == records for t,records in rows.items())


def test_zero_fee_migration_failed_trigger_creation_rolls_back(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'zero-rollback', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, MEETING_REVISION)
    before = _trigger_sql(settings.database_path)
    def fail(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().startswith('CREATE TRIGGER trg_invoice_validate_issue'):
            raise RuntimeError('synthetic zero fee DDL failure')
    event.listen(Engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic zero fee'):
            command.upgrade(config, 'head')
    finally:
        event.remove(Engine, 'before_cursor_execute', fail)
    assert _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as db:
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == (MEETING_REVISION,)
    command.upgrade(config, 'head')


@pytest.mark.parametrize('replacement', ['WHERE NEW.amount_cents <= 0;', 'WHERE NEW.amount_cents < -1;'])
def test_backup_rejects_wrong_zero_fee_guard(tmp_path, monkeypatch, replacement):
    settings = _settings(tmp_path, 'zero-guard', monkeypatch)
    command.upgrade(_alembic_config(settings), 'head')
    with sqlite3.connect(settings.database_path) as db:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='trg_invoice_validate_issue'").fetchone()[0]
        db.execute('DROP TRIGGER trg_invoice_validate_issue')
        db.execute(sql.replace('WHERE NEW.amount_cents < 0;', replacement))
        with pytest.raises(ValueError, match='零金额账单保护'):
            validate_database_structure(db)
