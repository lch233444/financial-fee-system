from datetime import date
from types import SimpleNamespace
from io import BytesIO

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.database import SessionLocal
from app.main import app
from app.models import InvoiceIssueAttempt, InvoiceMonthlySequence
from app.routes.invoices import _next_number
from app.services.invoice_number import invoice_number_prefix
from test_invoice_aggregation import WRITE_HEADERS, _group, _finalized_settlement, _draft


@pytest.mark.parametrize(('name', 'initials'), [
    ('Tony Wu', 'TW'), ('  tom\tWong  ', 'TW'), ('Chan Tai Man', 'CTM'),
    ("Anne-Marie O'Neil", 'AO'), ('WONG', 'W'),
])
def test_prefix_uses_company_full_name_and_each_english_word(name, initials):
    assert invoice_number_prefix('香港智石科技有限公司', name) == f'香港智石科技有限公司-{initials}'


@pytest.mark.parametrize('name', ['', '   ', '王先生', 'Tony 王'])
def test_missing_english_initial_is_not_guessed(name):
    with pytest.raises(ValueError, match='英文姓名'):
        invoice_number_prefix('Company', name)


def test_prefix_keeps_full_long_company_name_without_filename_sanitization():
    name = 'Alpha/香港: ' + 'Advisory Limited ' * 10
    assert invoice_number_prefix(name, 'Tony Wu') == name.strip() + '-TW'


def test_monthly_sequence_skips_old_and_other_company_prefix_reservations():
    engine = create_engine('sqlite://')
    # This focused sequence test needs no financial state transitions or PDF renderer.
    Base.metadata.create_all(engine)
    invoice = SimpleNamespace(id=1, receiving_company=SimpleNamespace(name='香港智石科技有限公司'), fc=SimpleNamespace(name='Tony Wu'))
    with Session(engine) as db:
        db.add_all([
            InvoiceIssueAttempt(invoice_id=99, invoice_number='202609001', status='FAILED'),
            InvoiceIssueAttempt(invoice_id=98, invoice_number='Other Company-AB-202609002', status='FAILED'),
        ])
        db.flush()
        assert _next_number(db, invoice, date(2026, 9, 28)) == '香港智石科技有限公司-TW-202609003'
        assert db.get(InvoiceMonthlySequence, '202609').last_number == 3
        assert _next_number(db, invoice, date(2026, 10, 1)) == '香港智石科技有限公司-TW-202610001'
    engine.dispose()


def test_invalid_fc_does_not_reserve_a_monthly_number():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    invoice = SimpleNamespace(id=1, receiving_company=SimpleNamespace(name='Company'), fc=SimpleNamespace(name='王先生'))
    with Session(engine) as db:
        with pytest.raises(HTTPException) as rejected:
            _next_number(db, invoice, date(2026, 9, 28))
        assert rejected.value.status_code == 400
        assert db.get(InvoiceMonthlySequence, '202609') is None
    engine.dispose()


def test_full_number_is_frozen_in_both_pdf_archives(tmp_path):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, 'PREFIXPDF', platform_count=1,
            company_name='香港智石科技有限公司', fc_name='Tony Wu')
        _finalized_settlement(client, data, 0, year=2026, quarter=1)
        draft = _draft(client, data, year=2026, quarter=1).json()
        with SessionLocal() as db:
            db.add(InvoiceMonthlySequence(issue_month='209809',last_number=1))
            db.commit()
        result = client.post(f"/api/invoices/{draft['id']}/issue",json={'issue_date':'2098-09-28','language':'zh'})
        assert result.status_code == 200,result.text
        assert result.json()['invoice_number'] == '香港智石科技有限公司-TW-209809002'
        for language in ('zh','en'):
            pdf = client.post(f"/api/invoices/{draft['id']}/pdf?language={language}")
            assert pdf.status_code == 200,pdf.text
            text = ''.join(''.join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages).split())
            assert '香港智石科技有限公司-TW-209809002' in text
            (tmp_path / f'prefixed-{language}.pdf').write_bytes(pdf.content)
