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
spec = importlib.util.spec_from_file_location('test_invoice_converter', ROOT / 'scripts/convert-test-invoices.py')
converter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(converter)


@pytest.fixture
def legacy_test_copy(tmp_path):
    root = tmp_path / 'isolated-copy'
    env = {**os.environ, 'FINANCIAL_DATA_ROOT': str(root), 'FINANCIAL_CODEX_HOME': str(tmp_path / 'ai'), 'FINANCIAL_TESTING': '1'}
    source = '''
import sys
sys.path[:0] = [sys.argv[1] + '/backend', sys.argv[1] + '/backend/tests']
from fastapi.testclient import TestClient
from app.main import app
from app.routes import invoices
from test_invoice_aggregation import _group, _finalized_settlement, _draft, WRITE_HEADERS
invoices._next_number = lambda db, invoice, day: 'Synthetic-FC-' + day.strftime('%Y%m%d') + '-1'
with TestClient(app, headers=WRITE_HEADERS) as client:
    data = _group(client, 'CONVERT100', platform_count=1)
    _finalized_settlement(client, data, 0, year=2026, quarter=1)
    draft = _draft(client, data, year=2026, quarter=1).json()
    issued = client.post(f"/api/invoices/{draft['id']}/issue", json={'issue_date':'2026-04-05','language':'en'})
    assert issued.status_code == 200, issued.text
    proof = client.post('/api/attachments', data={'entity_type':'PAYMENT'}, files={'file':('proof.pdf',b'%PDF-1.4 synthetic', 'application/pdf')})
    assert proof.status_code == 201, proof.text
    payment = client.post(f"/api/invoices/{draft['id']}/payments",json={'payment_date':'2026-04-06','amount':issued.json()['amount'],'method':'BANK_TRANSFER','proof_attachment_id':proof.json()['id']})
    assert payment.status_code == 201, payment.text
'''
    result = subprocess.run([sys.executable,'-c',source,str(ROOT)], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr + result.stdout
    return root


def read_database(root):
    with sqlite3.connect(root / 'database/financial_system.sqlite3') as sql:
        return converter._rows(sql), dict(sql.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))


def test_authorized_conversion_updates_both_pdfs_and_preserves_payment_ledger(legacy_test_copy):
    before, triggers = read_database(legacy_test_copy)
    report = converter.convert_test_copy(legacy_test_copy, acknowledged_test_copy=True)
    assert report['mapping'] == {'Synthetic-FC-20260405-1':'202604001'}
    assert report['converted_invoices'] == 1 and report['new_pdf_files'] == 2
    after, restored_triggers = read_database(legacy_test_copy)
    assert restored_triggers == triggers
    for table in ['payments','payment_allocations','invoice_adjustments','invoice_corrections','invoice_sources','invoice_lines','quarterly_settlements','settlement_account_lines']:
        assert before[table] == after[table]
    with sqlite3.connect(legacy_test_copy / 'database/financial_system.sqlite3') as sql:
        paths = json.loads(sql.execute('SELECT pdf_paths_json FROM invoices').fetchone()[0])
        assert sql.execute('SELECT invoice_number FROM invoice_issue_attempts').fetchall() == [('202604001',)]
    for path in paths.values():
        content = ' '.join(page.extract_text() for page in PdfReader(path).pages)
        assert '202604001' in content and '05/04/2026' in content
    assert converter.convert_test_copy(legacy_test_copy, acknowledged_test_copy=True)['converted_invoices'] == 0


def test_conversion_requires_acknowledgement_and_render_failure_rolls_back(legacy_test_copy):
    before = read_database(legacy_test_copy)
    files_before = sorted(path.relative_to(legacy_test_copy) for path in legacy_test_copy.rglob('*.pdf'))
    with pytest.raises(ValueError, match='明确确认'):
        converter.convert_test_copy(legacy_test_copy, acknowledged_test_copy=False)
    calls = 0
    def fail_second_pdf(**kwargs):
        nonlocal calls
        calls += 1
        kwargs['output_path'].write_bytes(b'partial')
        if calls == 2:
            raise RuntimeError('synthetic PDF failure')
    with pytest.raises(RuntimeError, match='synthetic PDF failure'):
        converter.convert_test_copy(legacy_test_copy, acknowledged_test_copy=True, renderer=fail_second_pdf)
    assert read_database(legacy_test_copy) == before
    assert sorted(path.relative_to(legacy_test_copy) for path in legacy_test_copy.rglob('*.pdf')) == files_before
