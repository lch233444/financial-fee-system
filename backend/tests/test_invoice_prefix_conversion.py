import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('prefix_converter', ROOT / 'scripts/prefix-issued-invoice.py')
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


@pytest.fixture
def paid_copy(tmp_path):
    root = tmp_path / 'copy'
    env = {**os.environ, 'FINANCIAL_DATA_ROOT':str(root), 'FINANCIAL_CODEX_HOME':str(tmp_path / 'ai'), 'FINANCIAL_TESTING':'1'}
    source = '''
import sys
sys.path[:0] = [sys.argv[1] + '/backend', sys.argv[1] + '/backend/tests']
from fastapi.testclient import TestClient
from app.main import app
from app.routes import invoices
from app.database import SessionLocal
from app.models import FC, InvoiceMonthlySequence
from test_invoice_aggregation import _group, _finalized_settlement, _draft, WRITE_HEADERS
numbers = iter(['202609001','202609002'])
invoices._next_number = lambda *args: next(numbers)
with TestClient(app, headers=WRITE_HEADERS) as client:
    for suffix in ['OLDVOID','PAIDPREFIX']:
        data = _group(client,suffix,platform_count=1,company_name='Void Company' if suffix=='OLDVOID' else 'Synthetic Number Company')
        with SessionLocal() as db:
            db.get(FC,data['fc']['id']).name = 'TW'
            db.commit()
        _finalized_settlement(client,data,0,year=2026,quarter=1)
        draft = _draft(client,data,year=2026,quarter=1).json()
        result = client.post(f"/api/invoices/{draft['id']}/issue",json={'issue_date':'2026-09-11','language':'en'})
        assert result.status_code == 200,result.text
        if suffix=='OLDVOID':
            result = client.post(f"/api/invoices/{draft['id']}/void",json={'reason':'Synthetic historical void'})
            assert result.status_code == 200,result.text
        else:
            proof = client.post('/api/attachments',data={'entity_type':'PAYMENT'},files={'file':('proof.pdf',b'%PDF-1.4 synthetic','application/pdf')})
            result = client.post(f"/api/invoices/{draft['id']}/payments",json={'payment_date':'2026-09-12','amount':'20.00','method':'BANK_TRANSFER','proof_attachment_id':proof.json()['id']})
            assert result.status_code == 201,result.text
    with SessionLocal() as db:
        db.add(InvoiceMonthlySequence(issue_month='202609',last_number=2))
        db.commit()
'''
    result = subprocess.run([sys.executable,'-c',source,str(ROOT)],env=env,capture_output=True,text=True,timeout=90)
    assert result.returncode == 0,result.stderr + result.stdout
    return root


def snapshot(root):
    with sqlite3.connect(root / 'database/financial_system.sqlite3') as db:
        return converter.rows(db),dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))


def convert(root, **changes):
    values = dict(live_root=root.parent / 'separate-live', invoice_id=2, expected_number='202609002',
        expected_company='Synthetic Number Company', expected_fc='TW', english_name='Tony Wu', acknowledged_copy=True)
    values.update(changes)
    return converter.prefix_copy(root, **values)


def test_selected_paid_invoice_preserves_void_cash_and_old_archives(paid_copy):
    before,triggers = snapshot(paid_copy)
    archives = {p:converter.digest(p) for p in paid_copy.rglob('*.pdf')}
    result = convert(paid_copy)
    assert result['new_number'] == 'Synthetic Number Company-TW-202609002'
    after,new_triggers = snapshot(paid_copy)
    assert new_triggers == triggers
    assert after['invoices'][0] == before['invoices'][0]
    for table in ['payments','payment_allocations','invoice_adjustments','invoice_corrections','invoice_sources','invoice_lines','quarterly_settlements','settlement_account_lines','invoice_issue_attempts','invoice_monthly_sequences']:
        assert before[table] == after[table],table
    assert all(converter.digest(path) == sha for path,sha in archives.items())
    assert after['export_records'][:-2] == before['export_records']
    with sqlite3.connect(paid_copy / 'database/financial_system.sqlite3') as db:
        assert db.execute('SELECT name FROM fcs WHERE id=2').fetchone() == ('Tony Wu',)
        paths = json.loads(db.execute('SELECT pdf_paths_json FROM invoices WHERE id=2').fetchone()[0])
        assert db.execute('SELECT last_number FROM invoice_monthly_sequences').fetchone() == (2,)
        audit = json.loads(db.execute("SELECT details_json FROM audit_events WHERE action='INVOICE_NUMBER_PREFIX_APPLIED'").fetchone()[0])
        assert audit == result
    for path in paths.values():
        content = ''.join(''.join(page.extract_text() for page in PdfReader(path).pages).split())
        assert 'SyntheticNumberCompany-TW-202609002' in content and '11/09/2026' in content
    with pytest.raises(ValueError,match='已改变'):
        convert(paid_copy)
    # The package must contain both generations of PDFs and remain valid after
    # numbering changes; use another process with this synthetic root only.
    source = '''
import sys
from pathlib import Path
sys.path.insert(0,sys.argv[1] + '/backend')
from app.services.backup import create_backup,validate_backup
root = Path(sys.argv[2])
archive = create_backup(snapshot_year=2026,snapshot_quarter=3)
manifest = validate_backup(archive,root.parent/'v')
assert len([item for item in manifest['files'] if item['path'].startswith('output/pdf/')]) == 6
'''
    env = {**os.environ,'FINANCIAL_DATA_ROOT':str(paid_copy),'FINANCIAL_CODEX_HOME':str(paid_copy.parent/'ai'),'FINANCIAL_TESTING':'1'}
    checked = subprocess.run([sys.executable,'-c',source,str(ROOT),str(paid_copy)],env=env,capture_output=True,text=True,timeout=90)
    assert checked.returncode == 0,checked.stdout + checked.stderr


def test_refuses_live_unacknowledged_void_and_stale_identity(paid_copy):
    before = snapshot(paid_copy)
    for overrides,match in [({'acknowledged_copy':False},'明确确认'),
        ({'live_root':paid_copy},'正式数据根'),
        ({'invoice_id':1,'expected_number':'202609001','expected_company':'Void Company'},'已改变'),
        ({'expected_fc':'Other Name'},'已改变'),({'expected_company':'Other Company'},'已改变')]:
        with pytest.raises(ValueError,match=match):
            convert(paid_copy,**overrides)
        assert snapshot(paid_copy) == before


def test_second_pdf_failure_and_content_change_leave_copy_unchanged(paid_copy):
    before = snapshot(paid_copy)
    files = {p:converter.digest(p) for p in paid_copy.rglob('*.pdf')}
    calls = 0
    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            kwargs['output_path'].write_bytes(b'partial')
            raise RuntimeError('synthetic second PDF failure')
        return converter.generate_invoice_pdf(**kwargs)
    with pytest.raises(RuntimeError,match='second PDF'):
        convert(paid_copy,renderer=fail_second)
    assert snapshot(paid_copy) == before
    assert {p:converter.digest(p) for p in paid_copy.rglob('*.pdf')} == files
    def changed_amount(**kwargs):
        kwargs['invoice'].amount_cents += 1
        return converter.generate_invoice_pdf(**kwargs)
    with pytest.raises(ValueError,match='除编号外'):
        convert(paid_copy,renderer=changed_amount)
    assert snapshot(paid_copy) == before
    assert {p:converter.digest(p) for p in paid_copy.rglob('*.pdf')} == files


def test_existing_new_archive_is_not_overwritten(paid_copy):
    path = converter.invoice_archive_paths('Synthetic Number Company-TW-202609002',paid_copy / 'output/pdf')['zh']
    path.write_bytes(b'keep existing')
    before = snapshot(paid_copy)
    with pytest.raises(FileExistsError):
        convert(paid_copy)
    assert path.read_bytes() == b'keep existing'
    assert snapshot(paid_copy) == before


def test_uncertain_commit_keeps_registered_new_archives(paid_copy, monkeypatch):
    from types import SimpleNamespace
    original_connect = sqlite3.connect
    class CommitErrorConnection:
        def __init__(self, *args, **kwargs):
            self.connection = original_connect(*args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def commit(self):
            self.connection.commit()
            raise RuntimeError('synthetic ambiguous commit')
    monkeypatch.setattr(converter, 'sqlite3', SimpleNamespace(connect=CommitErrorConnection))
    with pytest.raises(RuntimeError,match='ambiguous commit'):
        convert(paid_copy)
    with original_connect(paid_copy / 'database/financial_system.sqlite3') as db:
        number,serialized = db.execute('SELECT invoice_number,pdf_paths_json FROM invoices WHERE id=2').fetchone()
        assert number == 'Synthetic Number Company-TW-202609002'
        for path in json.loads(serialized).values():
            registered = db.execute('SELECT sha256 FROM export_records WHERE stored_path=?', (path,)).fetchone()[0]
            assert converter.digest(Path(path)) == registered


def test_tampered_original_archive_stops_before_any_changes(paid_copy):
    with sqlite3.connect(paid_copy / 'database/financial_system.sqlite3') as db:
        path = Path(json.loads(db.execute('SELECT pdf_paths_json FROM invoices WHERE id=2').fetchone()[0])['en'])
    path.write_bytes(path.read_bytes() + b'changed')
    before = snapshot(paid_copy)
    files = {p:converter.digest(p) for p in paid_copy.rglob('*.pdf')}
    with pytest.raises(ValueError,match='登记哈希'):
        convert(paid_copy)
    assert snapshot(paid_copy) == before
    assert {p:converter.digest(p) for p in paid_copy.rglob('*.pdf')} == files
