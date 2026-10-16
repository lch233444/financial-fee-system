from io import BytesIO
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from app.main import app
from app.config import get_settings
from test_invoice_aggregation import _group, _finalized_settlement
from test_payment_corrections import (
    WRITE_HEADERS, _calculate, _create, _finalize, _issued_case, _issue, _unclaimed_proof,
)


def test_zero_fee_invoice_has_number_bilingual_archives_and_no_payment():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, 'ZERO' + uuid4().hex[:8])
        sources = [_finalized_settlement(client, data, i, year=2026, quarter=1, closing=closing)
                   for i, closing in enumerate(('900.00', '1000.00'))]
        payload = dict(client_id=data['client']['id'], year=2026, quarter=1, payee_company_id=data['company']['id'])
        draft = _create(client, '/api/invoices', payload)
        assert draft['amount'] == '0.00'
        assert draft['settlement_ids'] == [s['id'] for s in sources]
        assert client.post('/api/invoices', json=payload).status_code == 409
        # The database still rejects a negative issued total before other checks.
        with sqlite3.connect(get_settings().database_path) as db:
            with pytest.raises(sqlite3.IntegrityError, match='invoice_amount_invalid'):
                db.execute("UPDATE invoices SET amount_cents=-1,lifecycle_status='ISSUING' WHERE id=?", (draft['id'],))
        invoice = _create(client, f"/api/invoices/{draft['id']}/issue", {'issue_date':'2026-04-05'})
        assert invoice['invoice_number'] and invoice['lifecycle_status'] == 'ISSUED'
        assert invoice['payment_status'] == 'NOT_REQUIRED' and not invoice['is_overdue']
        assert invoice['paid_amount'] == invoice['adjustment_amount'] == invoice['outstanding_amount'] == '0.00'
        assert invoice['payments'] == invoice['adjustments'] == []
        for language, phrase in [('en','No payment'), ('zh','無需付款')]:
            response = client.post(f"/api/invoices/{invoice['id']}/pdf?language={language}")
            assert response.status_code == 200
            text = ''.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
            assert phrase in text and 'HKD 0.00' in text and invoice['invoice_number'] in text
        payment = client.post(f"/api/invoices/{invoice['id']}/payments", json={
            'amount':'0.01','payment_date':'2026-04-06','method':'BANK_TRANSFER','proof_attachment_id':999999,
        })
        assert payment.status_code == 409 and '无需付款' in payment.json()['detail']
        before = client.get(f"/api/invoices/{invoice['id']}").json()
        target = _create(client, '/api/companies', {'name':'Zero Target '+uuid4().hex[:8], 'code':uuid4().hex[:10]})
        correction = _create(client, f"/api/invoices/{invoice['id']}/corrections", {
            'reason':'Correct company on zero fee notice', 'target_company_id':target['id'],
        })
        replacement = _issue(client, data, correction=True)
        assert replacement['amount'] == '0.00' and replacement['invoice_number'] != before['invoice_number']
        completed = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {'replacement_invoice_id':replacement['id']})
        assert completed['status'] == 'COMPLETED'
        assert client.get(f"/api/invoices/{replacement['id']}").json()['payment_status'] == 'NOT_REQUIRED'


def test_paid_invoice_corrected_to_zero_requires_full_refund_and_keeps_original_cash():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, 'REFUNDZERO')
        proof = _unclaimed_proof(client, 'PAYMENT', uuid4().hex)
        paid = _create(client, f"/api/invoices/{original['id']}/payments", {
            'payment_date':'2026-04-10','amount':'120.00','method':'BANK_TRANSFER','proof_attachment_id':proof,
        })
        payment_id = paid['payments'][0]['id']
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {'reason':'Correct HWM to zero charge'})
        _create(client, f"/api/settlements/{settlement['id']}/void", {'reason':'Correct HWM to zero charge'})
        _finalize(client, _calculate(client, data, original_hwm='1600.00'))
        replacement = _issue(client, data, correction=True)
        assert replacement['amount'] == '0.00'
        incomplete = client.post(f"/api/invoice-corrections/{correction['id']}/complete", json={'replacement_invoice_id':replacement['id']})
        assert incomplete.status_code in {400,409}
        refund_proof = _unclaimed_proof(client, 'PAYMENT_REFUND', uuid4().hex)
        completed = _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {
            'replacement_invoice_id':replacement['id'],
            'retained_allocations':[{'payment_id':payment_id,'amount':'0.00'}],
            'refunds':[{'payment_id':payment_id,'amount':'120.00','refund_date':'2026-04-11','method':'BANK_TRANSFER','reason':'Full refund for zero fee correction','proof_attachment_id':refund_proof}],
        })
        assert completed['status'] == 'COMPLETED' and completed['refunds'][0]['amount'] == '120.00'
        current = client.get(f"/api/invoices/{replacement['id']}").json()
        assert current['payment_status'] == 'NOT_REQUIRED' and current['paid_amount'] == '0.00'
        assert not current['payments']
