from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select

from app.database import SessionLocal, settings
from app.main import app
from app.models import AuditEvent
from test_invoice_company_correction import _company
from test_payment_corrections import WRITE_HEADERS, _calculate, _create, _finalize, _issued_case, _issue, _unclaimed_proof


def _amend(client, correction, target, recalculate=False, **extra):
    return client.patch(f"/api/invoice-corrections/{correction['id']}", json={
        'target_company_id': target, 'recalculate_settlements': recalculate,
        'expected_revision': correction['revision_no'], 'reason': '核对未收款及当前结算后调整', **extra,
    })


def test_combined_company_and_financial_correction_requires_replacement_sources():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, 'COMBINED')
        target = _company(client)
        archive = client.post(f"/api/invoices/{original['id']}/pdf?language=en").content
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {
            'reason': '同时调整收款公司和首次收费基准', 'target_company_id': target['id'], 'recalculate_settlements': True,
        })
        assert correction['recalculate_settlements'] is True
        assert client.post('/api/invoices', json={'client_id': data['client']['id'], 'year': 2026, 'quarter': 1}).status_code == 409
        _create(client, f"/api/settlements/{settlement['id']}/void", {'reason': '更正收费基准'})
        updated = _calculate(client, data, original_hwm='1100.00')
        _finalize(client, updated)
        replacement = _issue(client, data, correction=True)
        assert replacement['payee_company_id'] == target['id']
        assert replacement['amount'] == '100.00' and replacement['settlement_ids'] == [updated['id']]
        completed = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {'replacement_invoice_id': replacement['id']})
        assert completed['status'] == 'COMPLETED' and not completed['can_amend']
        assert client.get(f"/api/invoices/{replacement['id']}").json()['payment_status'] == 'UNPAID'
        assert client.post(f"/api/invoices/{original['id']}/pdf?language=en").content == archive
        assert _amend(client, completed, None, True).status_code == 409


def test_open_ordinary_correction_can_switch_to_payee_only_with_audit():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, 'AMENDONLY')
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': '原来发起普通更正'})
        result = _amend(client, correction, target['id'])
        assert result.status_code == 200, result.text
        amended = result.json()
        assert amended['revision_no'] == 2 and amended['recalculate_settlements'] is False
        assert amended['reason'] == correction['reason']
        with SessionLocal() as db:
            audit = db.scalar(select(AuditEvent).where(AuditEvent.action == 'INVOICE_CORRECTION_UPDATED', AuditEvent.entity_id == correction['id']))
            assert audit.details_json['before'] == {'target_company_id': None, 'recalculate_settlements': True}
            assert audit.details_json['after'] == {'target_company_id': target['id'], 'recalculate_settlements': False}
            assert audit.details_json['revision_no'] == 2
        assert _amend(client, correction, None, True).status_code == 409
        replacement = _issue(client, data, correction=True)
        assert replacement['amount'] == original['amount']
        assert replacement['settlement_ids'] == [settlement['id']]
        _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {'replacement_invoice_id': replacement['id']})


def test_changed_settlement_requires_recalculation_but_allows_new_company():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, 'AMENDRECALC')
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': '重新核算'})
        _create(client, f"/api/settlements/{settlement['id']}/void", {'reason': '重新核算'})
        rejected = _amend(client, correction, target['id'])
        assert rejected.status_code == 409 and '原结算已变化' in rejected.json()['detail']
        assert client.get(f"/api/invoice-corrections/{correction['id']}").json()['revision_no'] == 1
        accepted = _amend(client, correction, target['id'], True)
        assert accepted.status_code == 200, accepted.text
        _finalize(client, _calculate(client, data, original_hwm='1100.00'))
        replacement = _issue(client, data, correction=True)
        assert replacement['payee_company_id'] == target['id'] and replacement['amount'] == '100.00'
        _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {'replacement_invoice_id': replacement['id']})


def test_payee_only_can_enable_recalculation_and_return_to_original_company():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, 'AMENDBACK')
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': '调整公司', 'target_company_id': target['id']})
        changed = _amend(client, correction, None, True)
        assert changed.status_code == 200, changed.text
        _create(client, f"/api/settlements/{settlement['id']}/void", {'reason': '重新核算'})
        _finalize(client, _calculate(client, data, original_hwm='1100.00'))
        replacement = _issue(client, data, correction=True)
        assert replacement['payee_company_id'] == original['payee_company_id']
        _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {'replacement_invoice_id': replacement['id']})


@pytest.mark.parametrize('issued', [False, True])
def test_active_replacement_blocks_amendment_until_voided(issued):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, _, original = _issued_case(client, 'AMENDBLOCK')
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': '调整公司', 'target_company_id': target['id']})
        if issued:
            replacement = _issue(client, data, correction=True)
        else:
            replacement = _create(client, '/api/invoices', {'client_id': data['client']['id'], 'year': 2026, 'quarter': 1})
        rejected = _amend(client, correction, None, True)
        assert rejected.status_code == 409 and '替代账单' in rejected.json()['detail']
        _create(client, f"/api/invoices/{replacement['id']}/void", {'reason': '调整前作废替代账单'})
        assert _amend(client, correction, None, True).status_code == 200


def test_paid_correction_cannot_change_company_or_amend_existing_policy():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, original = _issued_case(client, 'AMENDPAID')
        target = _company(client)
        proof = _unclaimed_proof(client, 'PAYMENT', uuid4().hex)
        _create(client, f"/api/invoices/{original['id']}/payments", {
            'payment_date': '2026-04-10', 'amount': '120.00', 'method': 'BANK_TRANSFER', 'proof_attachment_id': proof,
        })
        assert client.post(f"/api/invoices/{original['id']}/corrections", json={
            'reason': '不允许转移收款', 'target_company_id': target['id'], 'recalculate_settlements': True,
        }).status_code == 409
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': '普通更正保留现金规则'})
        assert not correction['can_amend']
        assert _amend(client, correction, target['id'], True).status_code == 409
        assert client.get(f"/api/invoice-corrections/{correction['id']}").json() == correction


def test_sql_amendment_requires_revision_and_matching_audit_and_cannot_edit_completed():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, original = _issued_case(client, 'AMENDSQL')
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': '普通更正'})
        with closing(sqlite3.connect(settings.database_path)) as sql:
            for mutation in ('target_company_id=?', 'target_company_id=?, revision_no=revision_no+1', 'target_company_id=?, recalculate_settlements=0, revision_no=revision_no+1'):
                with pytest.raises(sqlite3.IntegrityError, match='history_immutable'):
                    sql.execute(f'UPDATE invoice_corrections SET {mutation} WHERE id=?', (target['id'], correction['id']))
                sql.rollback()
        assert _amend(client, correction, target['id'], False).status_code == 200


def test_concurrent_amendments_accept_only_one_revision():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, original = _issued_case(client, 'AMENDRACE')
        companies = [_company(client), _company(client)]
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': '普通更正'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda company: _amend(client, correction, company['id']), companies))
        assert sorted(result.status_code for result in results) == [200, 409]
        assert client.get(f"/api/invoice-corrections/{correction['id']}").json()['revision_no'] == 2


@pytest.mark.parametrize('value', ['false', 0, 1, 'true'])
def test_recalculation_requires_explicit_boolean(value):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        response = client.post('/api/invoices/2147483647/corrections', json={'reason': '检查输入', 'recalculate_settlements': value})
        assert response.status_code == 422
