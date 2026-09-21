from datetime import date
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import ExportRecord, Invoice, InvoiceIssueAttempt
from app.services.invoice_archive import invoice_archive_paths, legacy_invoice_archive_paths
from app.services.storage import sha256_file
from test_invoice_aggregation import _draft, _finalized_settlement, _group
from test_invoice_company_correction import _company
from test_invoice_multi_plan import _issue_draft, _second_plan
from test_payment_corrections import WRITE_HEADERS, _calculate, _create, _finalize, _issue, _issued_case, _unclaimed_proof


def _pdf_bytes(*, pages=1, marker="synthetic"):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=200)
    writer.add_metadata({"/Title": marker})
    stream = BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _interrupted_invoice(client, suffix):
    suffix = "".join(character for character in suffix if character.isalnum())[:16]
    data = _group(client, suffix, platform_count=1)
    _finalized_settlement(client, data, 0, year=2026, quarter=1)
    draft = _draft(client, data, year=2026, quarter=1).json()
    number = f"SYNTHETIC-{suffix}"
    with SessionLocal() as db:
        invoice = db.get(Invoice, draft["id"])
        invoice.invoice_number = number
        invoice.issue_date = date(2026, 4, 10)
        invoice.due_date = date(2026, 4, 24)
        invoice.lifecycle_status = "ISSUING"
        db.add(InvoiceIssueAttempt(invoice_id=invoice.id, invoice_number=number, status="RESERVED"))
        db.commit()
    paths = invoice_archive_paths(number, get_settings().data_root / "output" / "pdf")
    for language, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_pdf_bytes(marker=language))
    return draft["id"], paths


def test_company_and_recalculation_accept_late_finalized_plan_but_company_only_rejects():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "QREVLATE", platform_count=1)
        _finalized_settlement(client, data, 0, year=2026, quarter=1)
        original = _issue_draft(client, _draft(client, data, year=2026, quarter=1))
        late = _finalized_settlement(client, _second_plan(client, data, "QREVLATE"), 0, year=2026, quarter=1)
        target = _company(client)
        payload = {"reason": "更换收款公司并纳入迟到计划", "target_company_id": target["id"], "recalculate_settlements": False}
        rejected = client.post(f"/api/invoices/{original['id']}/corrections", json=payload)
        assert rejected.status_code == 409
        accepted = client.post(f"/api/invoices/{original['id']}/corrections", json={**payload, "recalculate_settlements": True})
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["target_company_id"] == target["id"]
        assert accepted.json()["recalculate_settlements"] is True
        assert late["id"] not in original["settlement_ids"]
        assert client.get(f"/api/invoices/{original['id']}").json()["lifecycle_status"] == "VOID"


@pytest.mark.parametrize("damage", ["empty", "corrupt", "no-pages", "changed-after-hash"])
def test_recovery_rejects_invalid_pdf_without_issuing_or_replacing_trusted_hash(damage):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        invoice_id, paths = _interrupted_invoice(client, f"QREV-{damage}")
        original_bytes = {language: path.read_bytes() for language, path in paths.items()}
        with SessionLocal() as db:
            db.add(ExportRecord(export_type="PDF_INVOICE", entity_type="INVOICE", entity_id=invoice_id,
                                stored_path=str(paths["en"]), sha256=sha256_file(paths["en"]), language="en"))
            db.commit()
        invalid = {"empty": b"", "corrupt": b"%PDF-1.4\nnot a PDF\n%%EOF",
                   "no-pages": _pdf_bytes(pages=0), "changed-after-hash": _pdf_bytes(marker="changed")}[damage]
        try:
            paths["en"].write_bytes(invalid)
            listed = client.get(f"/api/invoices/{invoice_id}").json()
            assert listed["issue_recovery"]["files_complete"] is False
            assert listed["issue_recovery"]["can_complete"] is False
            rejected = client.post(f"/api/invoices/{invoice_id}/recover-issuing", json={"action": "COMPLETE"})
            assert rejected.status_code == 409, rejected.text
            with SessionLocal() as db:
                assert db.get(Invoice, invoice_id).lifecycle_status == "ISSUING"
                assert len(db.scalars(select(ExportRecord).where(ExportRecord.entity_type == "INVOICE", ExportRecord.entity_id == invoice_id)).all()) == 1
            assert paths["en"].read_bytes() == invalid
            returned = client.post(f"/api/invoices/{invoice_id}/recover-issuing", json={"action": "RETURN_TO_DRAFT"})
            assert returned.status_code == 200, returned.text
            assert all(not path.exists() for path in paths.values())
        finally:
            # These trusted records were injected into ISSUING solely for this
            # test; normal issuance commits them atomically with ISSUED.
            # Restore their originals before shared-database backup tests.
            for language, path in paths.items():
                path.write_bytes(original_bytes[language])


@pytest.mark.parametrize("trusted", [False, True])
def test_recovery_accepts_readable_bilingual_pdfs_with_matching_known_hash(trusted):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        invoice_id, paths = _interrupted_invoice(client, f"QREV-GOOD-{trusted}")
        before = {language: path.read_bytes() for language, path in paths.items()}
        if trusted:
            with SessionLocal() as db:
                for language, path in paths.items():
                    db.add(ExportRecord(export_type="PDF_INVOICE", entity_type="INVOICE", entity_id=invoice_id,
                                        stored_path=str(path), sha256=sha256_file(path), language=language))
                db.commit()
        assert client.get(f"/api/invoices/{invoice_id}").json()["issue_recovery"]["can_complete"] is True
        recovered = client.post(f"/api/invoices/{invoice_id}/recover-issuing", json={"action": "COMPLETE"})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["lifecycle_status"] == "ISSUED"
        for language in paths:
            downloaded = client.post(f"/api/invoices/{invoice_id}/pdf?language={language}")
            assert downloaded.status_code == 200
            assert downloaded.content == before[language]


@pytest.mark.parametrize("damage", ["empty", "corrupt", "changed-after-hash"])
def test_recovery_does_not_fall_back_to_legacy_when_both_archive_pairs_exist(damage):
    with TestClient(app, headers=WRITE_HEADERS) as client:
        invoice_id, paths = _interrupted_invoice(client, f"QRDUAL-{damage}")
        original_bytes = paths["en"].read_bytes()
        with SessionLocal() as db:
            invoice = db.get(Invoice, invoice_id)
            legacy = legacy_invoice_archive_paths(invoice.invoice_number, paths["en"].parent)
            trusted_hash = sha256_file(paths["en"])
            db.add(ExportRecord(export_type="PDF_INVOICE", entity_type="INVOICE", entity_id=invoice_id,
                                stored_path=str(paths["en"]), sha256=trusted_hash, language="en"))
            db.commit()
        for language, path in legacy.items():
            path.write_bytes(_pdf_bytes(marker=f"untracked-legacy-{language}"))
        try:
            paths["en"].write_bytes({"empty": b"", "corrupt": b"not PDF",
                                    "changed-after-hash": _pdf_bytes(marker="changed")}[damage])
            assert client.get(f"/api/invoices/{invoice_id}").json()["issue_recovery"]["can_complete"] is False
            rejected = client.post(f"/api/invoices/{invoice_id}/recover-issuing", json={"action": "COMPLETE"})
            assert rejected.status_code == 409, rejected.text
            assert "同时发现" in rejected.json()["detail"]
            with SessionLocal() as db:
                assert db.get(Invoice, invoice_id).lifecycle_status == "ISSUING"
                records = db.scalars(select(ExportRecord).where(ExportRecord.entity_type == "INVOICE", ExportRecord.entity_id == invoice_id)).all()
                assert [record.sha256 for record in records] == [trusted_hash]
        finally:
            paths["en"].write_bytes(original_bytes)


def test_void_never_issued_draft_pdf_returns_controlled_error():
    with TestClient(app, headers=WRITE_HEADERS, raise_server_exceptions=False) as client:
        data = _group(client, "QREVVOID", platform_count=1)
        _finalized_settlement(client, data, 0, year=2026, quarter=1)
        draft = _draft(client, data, year=2026, quarter=1).json()
        _create(client, f"/api/invoices/{draft['id']}/void", {"reason": "作废未签发草稿"})
        response = client.post(f"/api/invoices/{draft['id']}/pdf?language=en")
        assert response.status_code == 409, response.text
        assert "编号" in response.json()["detail"] or "签发" in response.json()["detail"]


def test_cash_detail_matches_net_allocation_during_open_completed_and_repeat_correction():
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data, settlement, original = _issued_case(client, "QREVCASH")
        proof = _unclaimed_proof(client, "PAYMENT", "QREV-CASH")
        paid = _create(client, f"/api/invoices/{original['id']}/payments", {
            "payment_date": "2026-04-10", "amount": "120.00", "method": "BANK_TRANSFER", "proof_attachment_id": proof})
        payment_id = paid["payments"][0]["id"]
        assert paid["payments"][0]["amount"] == "120.00"
        correction = _create(client, f"/api/invoices/{original['id']}/corrections", {"reason": "核对原单"})
        opened = client.get(f"/api/invoices/{original['id']}").json()
        assert opened["paid_amount"] == "0.00"
        assert opened["payments"][0]["amount"] == "0.00"
        assert opened["payments"][0]["original_amount"] == "120.00"
        _create(client, f"/api/settlements/{settlement['id']}/void", {"reason": "更正核算"})
        _finalize(client, _calculate(client, data, original_hwm="1100.00"))
        replacement = _issue(client, data, correction=True)
        refund_proof = _unclaimed_proof(client, "PAYMENT_REFUND", "QREV-REFUND")
        _create(client, f"/api/invoice-corrections/{correction['id']}/complete", {
            "replacement_invoice_id": replacement["id"], "retained_allocations": [{"payment_id": payment_id, "amount": "100.00"}],
            "refunds": [{"payment_id": payment_id, "refund_date": "2026-04-11", "amount": "20.00", "method": "BANK_TRANSFER", "reason": "退回多收", "proof_attachment_id": refund_proof}]})
        old = client.get(f"/api/invoices/{original['id']}").json()
        current = client.get(f"/api/invoices/{replacement['id']}").json()
        assert old["payments"][0]["amount"] == "0.00"
        assert current["paid_amount"] == current["payments"][0]["amount"] == "100.00"
        assert current["payments"][0]["original_amount"] == "120.00"
        _create(client, f"/api/invoices/{replacement['id']}/corrections", {"reason": "再次核对"})
        repeated = client.get(f"/api/invoices/{replacement['id']}").json()
        assert repeated["paid_amount"] == "0.00"
        assert sum(float(row["amount"]) for row in repeated["payments"]) == 0
