"""Synthetic annual packages exercise the same financial workflow as quarters."""
from contextlib import closing
from io import BytesIO
import json
import sqlite3
import zipfile

import pytest
from pypdf import PdfReader
from openpyxl import load_workbook

from app import database
from app.services import backup
from test_datasets import workspace, headers
from test_invoice_aggregation import _group, _snapshot
from evidence_fixtures import upload_evidence


@pytest.fixture
def annual(workspace):
    client, manager, settings = workspace
    response = client.post('/api/datasets', headers=headers(manager), json={
        'name': 'Annual customers', 'settlement_cycle': 'ANNUAL'})
    assert response.status_code == 200, response.text
    identity = response.json()['id']
    response = client.post('/api/datasets/switch', headers=headers(manager), json={'id': identity})
    assert response.status_code == 200, response.text
    client.headers.update(headers(manager))
    data = _group(client, 'Annual', platform_count=2)
    return client, manager, settings, data


def calculate(client, data, *, year=2026, index=0, beginning='1000.00', ending='1600.00',
              opening_date=None, start=None, end=None, initial=True):
    account = data['accounts'][index]
    start = start or f'{year}-01-01'
    end = end or f'{year}-12-31'
    closing = _snapshot(client, account['id'], end, ending, closing=True)
    line = {'account_id': account['id'], 'closing_snapshot_id': closing['id'],
            'start_date': start, 'closing_date': end}
    if initial:
        opening = _snapshot(client, account['id'], opening_date or start, beginning, closing=False)
        line['beginning_snapshot_id'] = opening['id']
    payload = {'client_id': data['client']['id'], 'platform_id': data['platforms'][index]['id'],
               'fee_plan_id': data['plan']['id'], 'year': year, 'quarter': 0, 'account_lines': [line]}
    return client.post('/api/settlements/calculate', json=payload), payload


def finalize(client, result):
    assert result.status_code == 200, result.text
    result = client.post(f"/api/settlements/{result.json()['id']}/finalize")
    assert result.status_code == 200, result.text
    return result.json()


def test_annual_fee_hwm_carry_skip_year_and_quarter_rejection(annual):
    client, manager, settings, data = annual
    result, payload = calculate(client, data)
    first = finalize(client, result)
    assert first['period_type'] == 'ANNUAL' and first['quarter'] == 0
    assert first['start_date'] == '2026-01-01' and first['closing_date'] == '2026-12-31'
    assert first['service_fee'] == '120.00'
    assert first['account_lines'][0]['next_hwm'] == '1600.00'
    assert client.post('/api/settlements/calculate', json=payload).status_code == 409
    wrong = {**payload, 'quarter': 4}
    assert client.post('/api/settlements/calculate', json=wrong).status_code == 409
    result, _ = calculate(client, data, year=2028, ending='2000.00', initial=False)
    assert result.status_code == 409 and '2027年度' in result.json()['detail']
    result, _ = calculate(client, data, year=2027, ending='2000.00', initial=False)
    second = finalize(client, result)
    assert second['service_fee'] == '80.00'
    assert second['account_lines'][0]['original_hwm'] == '1600.00'
    with closing(sqlite3.connect(settings.database_path)) as db:
        with pytest.raises(sqlite3.IntegrityError, match='history_exists'):
            db.execute("UPDATE app_settings SET value='QUARTERLY' WHERE key='settlement_cycle'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE quarterly_settlements SET quarter=4 WHERE id=?', (first['id'],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM app_settings WHERE key='settlement_cycle'")


@pytest.mark.parametrize('end', ['2026-03-31', '2026-06-30', '2026-09-30'])
def test_annual_rejects_quarter_end_without_actual_exit(annual, end):
    client, _, _, data = annual
    result, _ = calculate(client, data, end=end)
    assert result.status_code == 400 and '退出日' in result.json()['detail']


@pytest.mark.parametrize('opening_date,expected', [('2025-12-31', 200), ('2026-01-01', 200), ('2025-12-30', 400)])
def test_annual_opening_boundary(annual, opening_date, expected):
    client, _, _, data = annual
    result, _ = calculate(client, data, opening_date=opening_date)
    assert result.status_code == expected, result.text
    if expected == 200:
        assert finalize(client, result)['service_fee'] == '120.00'


def test_annual_invoice_independent_accounts_exports_and_package(annual, tmp_path):
    client, manager, settings, data = annual
    first = finalize(client, calculate(client, data)[0])
    second = finalize(client, calculate(client, data, index=1, ending='900.00')[0])
    assert second['service_fee'] == '0.00'  # losses never offset another account
    assert second['account_lines'][0]['next_hwm'] == '1000.00'
    response = client.post('/api/invoices', json={'client_id': data['client']['id'],
        'year': 2026, 'quarter': 0, 'payee_company_id': data['company']['id']})
    assert response.status_code == 201, response.text
    invoice = response.json()
    assert invoice['period_type'] == 'ANNUAL' and invoice['amount'] == '120.00'
    issued = client.post(f"/api/invoices/{invoice['id']}/issue", json={'issue_date': '2027-01-05'})
    assert issued.status_code == 200, issued.text
    for language in ['en', 'zh']:
        pdf = client.post(f"/api/invoices/{invoice['id']}/pdf?language={language}")
        assert pdf.status_code == 200
        text = ''.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
        assert '120.00' in text and 'Q0' not in text
    pdf = client.post(f"/api/exports/pdf?settlement_id={first['id']}&language=en")
    assert pdf.status_code == 200, pdf.text
    text = ''.join(p.extract_text() for p in PdfReader(BytesIO(pdf.content)).pages)
    assert 'Annual Settlement' in text and '2026' in text and 'Quarterly' not in text
    exported = client.post(f"/api/exports/excel?settlement_ids={first['id']},{second['id']}")
    assert exported.status_code == 200, exported.text
    workbook = load_workbook(BytesIO(exported.content), data_only=False)
    values = [cell.value for sheet in workbook for row in sheet for cell in row]
    assert '2026 Annual' in values and '2026 Q0' not in values
    report = client.get('/api/dashboard?year=2026&quarter=0')
    assert report.status_code == 200
    assert client.get('/api/dashboard?year=2026&quarter=1').status_code == 409
    package = backup.create_backup(snapshot_year=2026, snapshot_quarter=0)
    with zipfile.ZipFile(package) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    assert manifest['settlement_cycle'] == 'ANNUAL'
    assert manifest['snapshot_period'] == {'year': 2026, 'quarter': 0}
    checked = backup.validate_backup(package, tmp_path/'checked')
    assert checked == manifest
    assert not list((tmp_path/'checked').rglob('*-wal'))
    original_bytes = package.read_bytes()
    with package.open('rb') as stream:
        imported = client.post('/api/datasets/import', data={'name': 'Annual copy'}, files={'file': ('annual.zip', stream, 'application/zip')})
    assert imported.status_code == 200, imported.text
    assert package.read_bytes() == original_bytes
    state = client.get('/api/datasets').json()
    assert next(x for x in state['datasets'] if x['id'] == imported.json()['id'])['settlement_cycle'] == 'ANNUAL'
    response = client.post('/api/datasets/switch', json={'id': 'primary'})
    assert response.status_code == 200
    client.headers.update(headers(manager))
    assert client.get('/api/datasets').json()['settlement_cycle'] == 'QUARTERLY'
    assert client.get('/api/invoices').json() == []
    assert client.get('/api/settlements').json() == []
    assert client.get('/api/dashboard?year=2026&quarter=0').status_code == 409


def test_annual_package_manifest_cannot_misrepresent_database(annual, tmp_path):
    package = backup.create_backup()
    changed = tmp_path/'mismatch.zip'
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(changed, 'w') as target:
        for name in source.namelist():
            content = source.read(name)
            if name == 'manifest.json':
                value = json.loads(content)
                value['settlement_cycle'] = 'QUARTERLY'
                value['snapshot_period']['quarter'] = 4
                content = json.dumps(value).encode()
            target.writestr(name, content)
    with pytest.raises(ValueError, match='周期不一致'):
        backup.validate_backup(changed, tmp_path/'bad')


@pytest.mark.parametrize('opening_date,fee', [('2025-12-31', '50.00'), ('2026-01-01', '70.00')])
def test_whole_year_cash_and_opening_day_boundary(annual, opening_date, fee):
    client, _, _, data = annual
    for day, kind, amount in [('2026-01-01','MONTHLY_CONTRIBUTION','100.00'),
                              ('2026-04-20','CONTRIBUTION','200.00'),
                              ('2026-08-11','WITHDRAWAL','50.00'),
                              ('2026-12-31','MONTHLY_CONTRIBUTION','100.00')]:
        response = client.post('/api/transactions', json={'account_id': data['accounts'][0]['id'],
            'transaction_date':day, 'transaction_type':kind, 'amount':amount,
            'remark':'Synthetic verified cash', 'attachment_ids':[upload_evidence(client,'TRANSACTION')]})
        assert response.status_code == 201, response.text
    result = finalize(client, calculate(client, data, opening_date=opening_date)[0])
    assert result['service_fee'] == fee


def test_annual_midyear_onboarding_exit_and_leap_year(annual):
    client, _, _, data = annual
    account = data['accounts'][0]['id']
    response = client.patch(f'/api/accounts/{account}', json={'start_date':'2026-04-01', 'end_date':'2026-09-15'})
    assert response.status_code == 200, response.text
    result = finalize(client, calculate(client, data, start='2026-04-01', end='2026-09-15')[0])
    assert result['service_fee'] == '120.00' and result['closing_date'] == '2026-09-15'
    leap = finalize(client, calculate(client, data, index=1, year=2028)[0])
    assert leap['days'] == 366 and leap['service_fee'] == '120.00'


def test_annual_stale_draft_cannot_finalize_and_finalized_cash_is_locked(annual):
    client, _, _, data = annual
    response, payload = calculate(client, data)
    assert response.status_code == 200
    transaction = {'account_id': data['accounts'][0]['id'], 'transaction_date':'2026-07-01',
                   'transaction_type':'MONTHLY_CONTRIBUTION', 'amount':'100.00',
                   'attachment_ids':[upload_evidence(client, 'TRANSACTION')]}
    assert client.post('/api/transactions', json=transaction).status_code == 201
    stale = client.post(f"/api/settlements/{response.json()['id']}/finalize")
    assert stale.status_code == 409
    fresh = finalize(client, client.post('/api/settlements/calculate', json=payload))
    assert fresh['service_fee'] == '100.00'
    transaction['attachment_ids'] = [upload_evidence(client, 'TRANSACTION')]
    assert client.post('/api/transactions', json=transaction).status_code == 409
