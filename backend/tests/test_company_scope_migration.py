from contextlib import closing
from datetime import date
import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.database as database_module
from app.services.backup import _validate_sqlite_database
from app.services.company_scope_contract import COMPANY_SCOPE_REVISION, CODE_TRIGGER_SQL, company_scope_schema_is_current
from test_deletion_guard_migration import _settings, _alembic_config, _trigger_sql
from test_workflow_guard_migration import _seed_history, _business_rows
from app.models import Attachment, Invoice, InvoiceLine, InvoiceSource, Payment, QuarterlySettlement, utcnow

PREVIOUS = 'b7e2d9a41c60'


def test_migration_preserves_every_row_including_duplicate_codes_and_frozen_history(tmp_path, monkeypatch):
    settings = _settings(tmp_path, 'company-history', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, 'd4f8a1c73b29')
    _seed_history(settings)
    command.upgrade(config, PREVIOUS)
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        settlement = db.get(QuarterlySettlement, 1)
        line = settlement.account_lines[0]
        bill = Invoice(settlement_id=settlement.id, client_id=settlement.client_id,
                       company_id=settlement.company_id, fc_id=settlement.fc_id, fee_plan_id=settlement.fee_plan_id,
                       year=2026, quarter=1, amount_cents=12000)
        db.add(bill)
        db.flush()
        source = InvoiceSource(invoice_id=bill.id, settlement_id=settlement.id, locked_amount_cents=12000)
        db.add(source)
        db.flush()
        db.add(InvoiceLine(invoice_id=bill.id, source_id=source.id, source_settlement_id=settlement.id,
                           source_account_line_id=line.id, platform_id=settlement.platform_id,
                           platform_name_snapshot='Historical Platform', account_number_snapshot=line.account.account_number,
                           start_date=line.start_date, closing_date=line.closing_date, service_fee_cents=12000))
        db.flush()
        bill.lifecycle_status = 'ISSUING'
        bill.invoice_number = 'HISTORICAL-20260405-1'
        bill.issue_date, bill.due_date = date(2026, 4, 5), date(2026, 4, 19)
        db.flush()
        bill.lifecycle_status, bill.issued_at = 'ISSUED', utcnow()
        bill.pdf_paths_json = {'zh': 'synthetic/original-zh.pdf', 'en': 'synthetic/original-en.pdf'}
        db.flush()
        proof = Attachment(entity_type='PAYMENT', entity_id=None, original_name='synthetic-proof.pdf',
                           stored_path='synthetic-proof.pdf', sha256='a' * 64, size_bytes=1)
        db.add(proof)
        db.flush()
        payment = Payment(invoice_id=bill.id, payment_date=date(2026, 4, 10), amount_cents=12000,
                          method='BANK_TRANSFER', proof_attachment_id=proof.id, company_difference_cents=0)
        db.add(payment)
        db.flush()
        # The payment insert trigger appends the original APPLY allocation.
        db.commit()
    engine.dispose()
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute("INSERT INTO companies (name,code,payment_terms_days,active,created_at,updated_at) VALUES ('Second','SECOND',14,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        for table in ('fcs', 'fee_plans'):
            columns = [row[1] for row in sql.execute(f'PRAGMA table_info({table})') if row[1] != 'id']
            projection = ','.join('2' if name == 'company_id' else name for name in columns)
            sql.execute(f"INSERT INTO {table} ({','.join(columns)}) SELECT {projection} FROM {table}")
    before = _business_rows(settings.database_path)
    command.upgrade(config, COMPANY_SCOPE_REVISION)
    assert _business_rows(settings.database_path) == before
    assert len(_trigger_sql(settings.database_path)) == 59
    with closing(sqlite3.connect(settings.database_path)) as sql:
        assert company_scope_schema_is_current(sql)
        assert sql.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert sql.execute('PRAGMA foreign_key_check').fetchall() == []
        with pytest.raises(sqlite3.IntegrityError, match='globally_unique'):
            sql.execute("INSERT INTO fcs (name,code,active,created_at,updated_at) VALUES ('Duplicate',' fc ',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
    _validate_sqlite_database(settings.database_path)


@pytest.mark.parametrize('failure_stage', ['copy', 'rename', 'trigger'])
def test_migration_failure_restores_tables_indexes_triggers_and_version(tmp_path, monkeypatch, failure_stage):
    settings = _settings(tmp_path, 'company-failure', monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    before, triggers = _business_rows(settings.database_path), _trigger_sql(settings.database_path)
    def fail(conn, cursor, statement, parameters, context, executemany):
        prefixes = {'copy': 'INSERT INTO "_company_scope_fee_plans"',
                    'rename': 'ALTER TABLE "_company_scope_fee_plans"',
                    'trigger': 'CREATE TRIGGER trg_fcs_code_unique_insert'}
        if statement.lstrip().startswith(prefixes[failure_stage]):
            raise RuntimeError('synthetic company migration failure')
    event.listen(Engine, 'before_cursor_execute', fail)
    try:
        with pytest.raises(RuntimeError, match='synthetic company migration failure'):
            command.upgrade(config, COMPANY_SCOPE_REVISION)
    finally:
        event.remove(Engine, 'before_cursor_execute', fail)
    assert _business_rows(settings.database_path) == before
    assert _trigger_sql(settings.database_path) == triggers
    _validate_sqlite_database(settings.database_path)
    with closing(sqlite3.connect(settings.database_path)) as sql:
        assert sql.execute('SELECT version_num FROM alembic_version').fetchone() == (PREVIOUS,)
        assert all(next(row for row in sql.execute(f'PRAGMA table_info({table})') if row[1] == 'company_id')[3] == 1 for table in ('fcs', 'fee_plans'))


@pytest.mark.parametrize('tamper', [False, True])
def test_unstamped_company_scope_requires_complete_schema_and_backup_guards(tmp_path, monkeypatch, tamper):
    settings = _settings(tmp_path, 'company-unstamped', monkeypatch)
    command.upgrade(_alembic_config(settings), 'head')
    if tamper:
        with closing(sqlite3.connect(settings.database_path)) as sql, sql:
            name = next(iter(CODE_TRIGGER_SQL))
            sql.execute(f'DROP TRIGGER {name}')
            sql.execute(f'CREATE TRIGGER {name} BEFORE INSERT ON fcs BEGIN SELECT 1; END')
        with pytest.raises(ValueError, match='公司解绑'):
            _validate_sqlite_database(settings.database_path)
    with closing(sqlite3.connect(settings.database_path)) as sql, sql:
        sql.execute('DROP TABLE alembic_version')
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, 'engine', engine)
    monkeypatch.setattr(database_module, 'settings', settings)
    try:
        if tamper:
            with pytest.raises(RuntimeError, match='公司解绑'):
                database_module.init_db()
        else:
            database_module.init_db()
            _validate_sqlite_database(settings.database_path)
    finally:
        engine.dispose()
