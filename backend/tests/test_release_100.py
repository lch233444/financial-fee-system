from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.database import SessionLocal
from app.models import AuditEvent, SubAccount, InvoiceMonthlySequence
from test_account_hwm_evidence import WRITE_HEADERS, _master, _snapshot
from test_invoice_aggregation import _group, _finalized_settlement, _draft


def test_first_system_snapshot_hwm_override_and_strict_inheritance():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, 'FIRST100')
        account_id = data['accounts'][0]['id']
        with SessionLocal() as db:
            db.get(SubAccount, account_id).start_date = date(2025, 3, 1)
            db.commit()
        beginning = _snapshot(client, account_id, '2026-01-01', '1000', closing=False, evidence=True)
        closing = _snapshot(client, account_id, '2026-03-31', '1200', closing=True, evidence=True)
        line = dict(account_id=account_id, beginning_snapshot_id=beginning['id'], closing_snapshot_id=closing['id'])
        payload = dict(client_id=data['client']['id'], platform_id=data['platform']['id'], fee_plan_id=data['plan']['id'], year=2026, quarter=1, account_lines=[line])
        automatic = client.post('/api/settlements/calculate', json=payload)
        assert automatic.status_code == 200, automatic.text
        assert automatic.json()['account_lines'][0]['original_hwm'] == '1000.00'
        assert automatic.json()['account_lines'][0]['hwm_source_type'] == 'INITIAL_SNAPSHOT'
        line['original_hwm'] = '1100'
        assert client.post('/api/settlements/calculate', json=payload).status_code == 400
        line['hwm_override_reason'] = '核对首次计费基准'
        assert client.post('/api/settlements/calculate', json=payload).status_code == 400
        line['hwm_override_confirmed'] = 'true'
        assert client.post('/api/settlements/calculate', json=payload).status_code == 422
        line['hwm_override_confirmed'] = True
        draft = client.post('/api/settlements/calculate', json=payload)
        assert draft.status_code == 200, draft.text
        item = draft.json()
        assert item['account_lines'][0]['hwm_override_confirmed'] is True
        with SessionLocal() as db:
            # A direct database status write must not bypass the separate confirmation.
            db.execute(text('UPDATE settlement_account_lines SET hwm_override_confirmed=0 WHERE settlement_id=:id'), {'id': item['id']})
            with pytest.raises(IntegrityError, match='settlement_hwm_source_or_confirmation_invalid'):
                db.execute(text("UPDATE quarterly_settlements SET status='FINALIZED' WHERE id=:id"), {'id': item['id']})
            db.rollback()
        finalized = client.post(f"/api/settlements/{item['id']}/finalize", json={})
        assert finalized.status_code == 200, finalized.text
        with SessionLocal() as db:
            event = db.scalars(select(AuditEvent).where(AuditEvent.action == 'SETTLEMENT_HWM_CONFIRMED', AuditEvent.entity_id == item['id']).order_by(AuditEvent.id.desc())).first()
            assert event is not None
            assert event.details_json['accounts'][0]['override_reason'] == '核对首次计费基准'
        next_closing = _snapshot(client, account_id, '2026-06-30', '1300', closing=True, evidence=True)
        next_line = dict(account_id=account_id, closing_snapshot_id=next_closing['id'])
        payload.update(quarter=2, account_lines=[next_line])
        inherited = client.post('/api/settlements/calculate', json=payload)
        assert inherited.status_code == 200, inherited.text
        assert inherited.json()['account_lines'][0]['original_hwm'] == finalized.json()['account_lines'][0]['next_hwm']
        assert inherited.json()['account_lines'][0]['hwm_source_type'] == 'PREVIOUS_SETTLEMENT'
        next_line.update(original_hwm='1100', hwm_override_reason='不允许覆盖继承值', hwm_override_confirmed=True)
        assert client.post('/api/settlements/calculate', json=payload).status_code == 409


def test_monthly_invoice_number_is_global_and_changes_month():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        issued = []
        for suffix, issue_date in [('MONA100','2091-09-01'), ('MONB100','2091-09-30'), ('MONC100','2091-10-01')]:
            data = _group(client, suffix, platform_count=1)
            _finalized_settlement(client, data, 0, year=2026, quarter=1)
            draft = _draft(client, data, year=2026, quarter=1).json()
            response = client.post(f"/api/invoices/{draft['id']}/issue", json={'issue_date':issue_date,'language':'zh'})
            assert response.status_code == 200, response.text
            issued.append(response.json()['invoice_number'])
        assert issued == ['209109001','209109002','209110001']
        data = _group(client, 'CAP100', platform_count=1)
        _finalized_settlement(client, data, 0, year=2026, quarter=1)
        draft = _draft(client, data, year=2026, quarter=1).json()
        with SessionLocal() as db:
            db.add(InvoiceMonthlySequence(issue_month='209111', last_number=999))
            db.commit()
        rejected = client.post(f"/api/invoices/{draft['id']}/issue", json={'issue_date':'2091-11-01','language':'zh'})
        assert rejected.status_code == 409
        current = next(item for item in client.get('/api/invoices').json() if item['id'] == draft['id'])
        assert current['lifecycle_status'] == 'DRAFT' and current['invoice_number'] is None


def test_managed_clients_follow_actual_account_period_and_deduplicate():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _master(client, 'MANAGED100', accounts=2)
        registration = client.post('/api/clients', json={'name':'仅登记100','management_start_date':'2026-01-01'}).json()
        with SessionLocal() as db:
            for item in data['accounts']:
                account = db.get(SubAccount, item['id'])
                account.start_date = date(2026, 3, 15)
                account.end_date = date(2026, 4, 15)
                account.status = 'CLOSED'
            db.commit()
        for quarter, expected in [(1,True),(2,True),(3,False)]:
            response = client.get(f'/api/dashboard?year=2026&quarter={quarter}')
            assert response.status_code == 200
            result = response.json()
            rows = [row for row in result['client_overview'] if row['client_id'] == data['client']['id']]
            assert len(rows) == int(expected)
            assert result['managed_clients'] == len(result['client_overview'])
            assert registration['id'] not in [row['client_id'] for row in result['client_overview']]
            if rows:
                assert rows[0]['fc_id'] == data['fc']['id']
                assert [plan['id'] for plan in rows[0]['fee_plans']] == [data['plan']['id']]
