import sqlite3
from contextlib import closing
from alembic import command
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.services.backup import validate_database_structure
from app.services.finance_meeting_contract import MEETING_REVISION, meeting_schema_is_current
from test_deletion_guard_migration import _alembic_config, _settings, _trigger_sql


def test_migration_removes_only_codes_and_keeps_legacy_financial_rows(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "meeting-history", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "b917c0a31003")
    with sqlite3.connect(settings.database_path) as db:
        db.execute("INSERT INTO companies (id,name,code,payment_terms_days,active,created_at,updated_at) VALUES (1,'Company','CO',14,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO fcs (id,company_id,name,code,remark,active,created_at,updated_at) VALUES (1,1,'FC','FC','original',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO fee_plans (id,company_id,name,code,fee_rate_bps,calculation_method,active,created_at,updated_at) VALUES (1,1,'Plan','P20',2000,'HIGH_WATER_MARK',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO clients (id,name,status,created_at,updated_at) VALUES (1,'Legacy','DRAFT',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO sub_accounts (id,client_id,account_number,currency,status,created_at,updated_at) VALUES (1,1,'LEGACY','HKD','DRAFT',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO transactions (id,account_id,transaction_date,transaction_type,amount_cents,remark,created_at,updated_at) VALUES (1,1,'2026-01-02','CONTRIBUTION',219242,'原始备注不得改写',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        db.execute("INSERT INTO balance_snapshots (id,account_id,as_of_date,total_balance_cents,currency,source_type,eligible_for_closing,created_at,updated_at) VALUES (1,1,'2026-03-31',100000,'HKD','MANUAL',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        # Keep the established ID high-water contract valid for synthetic legacy rows.
        for key in ('clients', 'sub_accounts', 'balance_snapshots'):
            db.execute("UPDATE app_settings SET value='1' WHERE key=?", (f'id_high_water.{key}',))
        before = {table: db.execute(f'SELECT * FROM {table}').fetchall() for table in ('transactions','balance_snapshots','companies')}
        fc = db.execute('SELECT id,company_id,name,remark,active,created_at,updated_at FROM fcs').fetchall()
        plan = db.execute('SELECT id,company_id,name,fee_rate_bps,calculation_method,active,created_at,updated_at FROM fee_plans').fetchall()
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as db:
        assert meeting_schema_is_current(db)
        validate_database_structure(db)
        for table, rows in before.items():
            assert db.execute(f'SELECT * FROM {table}').fetchall() == rows
        assert db.execute('SELECT * FROM fcs').fetchall() == fc
        assert db.execute('SELECT * FROM fee_plans').fetchall() == plan
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == (MEETING_REVISION,)


def test_meeting_migration_ddl_failure_rolls_back_schema_and_retries(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "meeting-rollback", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "b917c0a31003")
    before = _trigger_sql(settings.database_path)
    def fail_create(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().startswith('CREATE TRIGGER trg_attachment_financial_history_update'):
            raise RuntimeError('synthetic meeting DDL failure')
    event.listen(Engine, 'before_cursor_execute', fail_create)
    try:
        with pytest.raises(RuntimeError, match='synthetic meeting DDL failure'):
            command.upgrade(config, 'head')
    finally:
        event.remove(Engine, 'before_cursor_execute', fail_create)
    assert _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as db:
        assert 'code' in {row[1] for row in db.execute('PRAGMA table_info(fcs)')}
        assert 'superseded' not in {row[1] for row in db.execute('PRAGMA table_info(attachments)')}
        assert db.execute('SELECT version_num FROM alembic_version').fetchone() == ('b917c0a31003',)
    command.upgrade(config, 'head')
    with sqlite3.connect(settings.database_path) as db:
        assert meeting_schema_is_current(db)


def test_meeting_restore_rejects_missing_history_guard_and_superseded_formula(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'meeting-guard', monkeypatch)
    command.upgrade(_alembic_config(settings), 'head')
    with sqlite3.connect(settings.database_path) as db:
        validate_database_structure(db)
        db.execute('DROP TRIGGER trg_attachment_financial_history_delete')
        with pytest.raises(ValueError, match='结构不兼容'):
            validate_database_structure(db)


@pytest.mark.parametrize("entrypoint", ["startup", "backup"])
def test_superseded_wrong_declared_type_is_rejected(tmp_path, monkeypatch, entrypoint):
    import app.database as database_module
    from sqlalchemy import create_engine
    settings = _settings(tmp_path, 'meeting-type-' + entrypoint, monkeypatch)
    command.upgrade(_alembic_config(settings), 'head')
    with closing(sqlite3.connect(settings.database_path)) as db:
        dump = '\n'.join(db.iterdump())
    assert dump.count('superseded BOOLEAN NOT NULL DEFAULT 0') == 1
    tampered = settings.database_path.with_name('wrong-type.sqlite3')
    with closing(sqlite3.connect(tampered)) as db:
        db.executescript(dump.replace('superseded BOOLEAN NOT NULL DEFAULT 0', 'superseded TEXT NOT NULL DEFAULT 0'))
        assert not meeting_schema_is_current(db)
        if entrypoint == 'backup':
            with pytest.raises(ValueError, match='财务会议保护结构不兼容'):
                validate_database_structure(db)
            return
    # Use the exact malformed schema at the isolated startup path.
    settings.database_path.unlink()
    tampered.rename(settings.database_path)
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, 'engine', engine)
    monkeypatch.setattr(database_module, 'settings', settings)
    try:
        with pytest.raises(RuntimeError, match='财务会议保护结构不兼容'):
            database_module.init_db()
    finally:
        engine.dispose()
