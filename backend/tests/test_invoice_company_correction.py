from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from app.main import app
from app.database import settings
from test_payment_corrections import WRITE_HEADERS, _calculate, _create, _finalize, _issued_case, _issue, _unclaimed_proof


def _company(client):
    return _create(client, "/api/companies", {
        "name": f"Replacement Company {uuid4().hex[:8]}", "code": uuid4().hex[:12],
        "address": "Synthetic replacement address", "bank_information": "New bank: 987654321",
        "payment_terms_days": 30,
    })


def test_unpaid_invoice_company_correction_preserves_sources_and_original_archives():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, "PAYEE")
        target = _company(client)
        original_pdf = client.post(f"/api/invoices/{original['id']}/pdf?language=en").content
        assert original_pdf.startswith(b'%PDF')
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {
            "reason": "Correct the company on this invoice only", "target_company_id": target["id"],
        })
        assert correction["target_company_id"] == target["id"]
        replacement = _issue(client, data)
        assert replacement["company_name"] == target["name"]
        assert replacement["invoice_number"].startswith(target["name"] + "-")
        assert replacement["due_date"] == "2026-05-05"
        assert replacement["amount"] == original["amount"] == "120.00"
        assert replacement["settlement_ids"] == original["settlement_ids"] == [settlement["id"]]
        completed = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {
            "replacement_invoice_id": replacement["id"],
        })
        assert completed["status"] == "COMPLETED"
        assert completed["payments"] == completed["allocations"] == completed["adjustments"] == []
        result = client.get(f"/api/invoices/{replacement['id']}").json()
        assert result["payment_status"] == "UNPAID"
        assert client.post(f"/api/invoices/{original['id']}/pdf?language=en").content == original_pdf
        pdf = client.post(f"/api/invoices/{replacement['id']}/pdf?language=en")
        assert pdf.status_code == 200, pdf.text
        text = " ".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
        assert target["name"] in text and "987654321" in text
        customer = next(row for row in client.get('/api/clients').json() if row['id'] == data['client']['id'])
        assert customer['company_id'] == data['company']['id']
        assert customer['fc_id'] == data['fc']['id']
        current = next(row for row in client.get('/api/settlements').json() if row['id'] == settlement['id'])
        assert current['status'] == 'FINALIZED' and current['fee_plan_id'] == data['plan']['id']
        # Repeated company corrections can use the same immutable settlement,
        # including a return to the original company. Its number is not reused.
        second = _create(client, f"/api/invoices/{replacement['id']}/corrections", {
            'reason': 'Return to original receiving company', 'target_company_id': data['company']['id'],
        })
        restored = _issue(client, data)
        _create(client, f"/api/invoice-corrections/{second['id']}/complete", {'replacement_invoice_id': restored['id']})
        assert restored['company_name'] == data['company']['name']
        assert restored['invoice_number'] != original['invoice_number']
        assert restored['invoice_number'].endswith('-2')


def test_paid_invoice_company_correction_is_rejected_before_voiding_or_reversing_cash():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, original = _issued_case(client, "PAIDPAYEE")
        target = _company(client)
        proof = _unclaimed_proof(client, 'PAYMENT', uuid4().hex)
        _create(client, f"/api/invoices/{original['id']}/payments", {
            "payment_date": "2026-04-10", "amount": "120.00", "method": "BANK_TRANSFER", "proof_attachment_id": proof,
        })
        before = client.get(f"/api/invoices/{original['id']}").json()
        result = client.post(f"/api/invoices/{original['id']}/corrections", json={
            "reason": "Wrong company", "target_company_id": target["id"],
        })
        assert result.status_code == 409, result.text
        assert '未收款' in result.json()['detail']
        assert client.get(f"/api/invoices/{original['id']}").json() == before


def test_later_financial_correction_keeps_the_corrected_invoice_payee():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, 'KEEPPAYEE')
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {
            'reason': 'Correct receiving company', 'target_company_id': target['id'],
        })
        company_invoice = _issue(client, data)
        _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {'replacement_invoice_id': company_invoice['id']})
        financial = _create(client, f"/api/invoices/{company_invoice['id']}/corrections", {'reason': 'Correct opening HWM'})
        _create(client, f"/api/settlements/{settlement['id']}/void", {'reason': 'Correct opening HWM'})
        replacement_settlement = _calculate(client, data, original_hwm='1100.00')
        _finalize(client, replacement_settlement)
        replacement = _issue(client, data)
        assert replacement['amount'] == '100.00'
        assert replacement['settlement_ids'] != original['settlement_ids']
        assert replacement['payee_company_id'] == target['id']
        assert replacement['invoice_number'].startswith(target['name'] + '-')
        result = _create(client, f"/api/invoice-corrections/{financial['id']}/complete", {'replacement_invoice_id': replacement['id']})
        assert result['status'] == 'COMPLETED'


@pytest.mark.parametrize('target_kind', ['same', 'missing'])
def test_invalid_company_leaves_issued_invoice_unchanged(target_kind):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, _, invoice = _issued_case(client, "BADPAYEE")
        response = client.post(f"/api/invoices/{invoice['id']}/corrections", json={
            "reason": "Wrong company", "target_company_id": data['company']['id'] if target_kind == 'same' else 2147483647,
        })
        assert response.status_code == (400 if target_kind == 'same' else 404), response.text
        assert client.get(f"/api/invoices/{invoice['id']}").json()['lifecycle_status'] == 'ISSUED'


def test_company_correction_sql_guards_freeze_target_payee_and_settlement():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, 'SQLPAYEE')
        target = _company(client)
        with closing(sqlite3.connect(settings.database_path)) as sql:
            with pytest.raises(sqlite3.IntegrityError, match='invoice_financial_header_locked'):
                sql.execute('UPDATE invoices SET payee_company_id=? WHERE id=?', (target['id'], original['id']))
            sql.rollback()
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': 'Wrong company', 'target_company_id': target['id']})
        with closing(sqlite3.connect(settings.database_path)) as sql:
            with pytest.raises(sqlite3.IntegrityError, match='history_immutable'):
                sql.execute('UPDATE invoice_corrections SET target_company_id=NULL WHERE id=?', (correction['id'],))
            sql.rollback()
            with pytest.raises(sqlite3.IntegrityError, match='settlement_company_correction_open'):
                sql.execute("UPDATE quarterly_settlements SET status='VOID', void_reason='Synthetic test' WHERE id=?", (settlement['id'],))
            sql.rollback()
        draft = _create(client, '/api/invoices', {'client_id': data['client']['id'], 'year': 2026, 'quarter': 1, 'fee_plan_id': data['plan']['id']})
        with closing(sqlite3.connect(settings.database_path)) as sql:
            sql.execute('UPDATE invoices SET payee_company_id=NULL WHERE id=?', (draft['id'],))
            sql.commit()
            with pytest.raises(sqlite3.IntegrityError, match='invoice_payee_company_target_mismatch'):
                sql.execute("UPDATE invoices SET lifecycle_status='ISSUING', invoice_number='SYNTHETIC-PAYEE', issue_date='2026-04-05', due_date='2026-05-05' WHERE id=?", (draft['id'],))
            sql.rollback()
        result = client.post(f"/api/invoices/{draft['id']}/issue", json={'issue_date': '2026-04-05'})
        assert result.status_code == 409 and '收款公司' in result.json()['detail']


def test_company_correction_and_payment_race_preserves_one_consistent_outcome():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, original = _issued_case(client, 'RACEPAYEE')
        target = _company(client)
        proof = _unclaimed_proof(client, 'PAYMENT', uuid4().hex)
        with ThreadPoolExecutor(max_workers=2) as pool:
            correction = pool.submit(client.post, f"/api/invoices/{original['id']}/corrections", json={'reason': 'Wrong company', 'target_company_id': target['id']})
            payment = pool.submit(client.post, f"/api/invoices/{original['id']}/payments", json={
                'payment_date': '2026-04-10', 'amount': '120.00', 'method': 'BANK_TRANSFER', 'proof_attachment_id': proof,
            })
            statuses = sorted([correction.result().status_code, payment.result().status_code])
        assert statuses == [201, 409]
        current = client.get(f"/api/invoices/{original['id']}").json()
        assert (current['lifecycle_status'], current['paid_amount']) in [('ISSUED', '120.00'), ('VOID', '0.00')]


def test_paid_company_correction_cannot_be_inserted_directly():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        _, _, original = _issued_case(client, 'SQLPAID')
        target = _company(client)
        proof = _unclaimed_proof(client, 'PAYMENT', uuid4().hex)
        _create(client, f"/api/invoices/{original['id']}/payments", {'payment_date': '2026-04-10', 'amount': '120.00', 'method': 'BANK_TRANSFER', 'proof_attachment_id': proof})
        with closing(sqlite3.connect(settings.database_path)) as sql:
            with pytest.raises(sqlite3.IntegrityError, match='invoice_company_correction_unpaid_required'):
                sql.execute("INSERT INTO invoice_corrections (original_invoice_id,target_company_id,status,reason,opened_at,created_at,updated_at) VALUES (?,?,'OPEN','Wrong company',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (original['id'], target['id']))


def test_company_correction_can_rebuild_voided_draft_and_blocks_target_company_deletion():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, _, original = _issued_case(client, 'REDRAFTPAYEE')
        target = _company(client)
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason': 'Wrong company', 'target_company_id': target['id']})
        draft = _create(client, '/api/invoices', {'client_id': data['client']['id'], 'year': 2026, 'quarter': 1, 'fee_plan_id': data['plan']['id']})
        _create(client, f"/api/invoices/{draft['id']}/void", {'reason': 'Rebuild draft'})
        assert client.delete(f"/api/companies/{target['id']}").status_code == 409
        replacement = _issue(client, data)
        result = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {'replacement_invoice_id': replacement['id']})
        assert result['status'] == 'COMPLETED'
