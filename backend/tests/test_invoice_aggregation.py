from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.main import app
from app.models import ExportRecord, FeePlan, Invoice, InvoiceIssueAttempt, InvoiceSource
from app.routes import invoices as invoices_module
from app.services.calculation import quarter_dates
from app.services.pdf_invoice import _money


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _snapshot(client: TestClient, account_id: int, as_of_date: str, amount: str, *, closing: bool) -> dict:
    response = client.post(
        "/api/balance-snapshots",
        json={
            "account_id": account_id,
            "as_of_date": as_of_date,
            "total_balance": amount,
            "eligible_for_closing": closing,
        },
    )
    assert response.status_code == 201, response.text
    snapshot = response.json()
    evidence = client.post(
        "/api/attachments",
        data={"entity_type": "SNAPSHOT", "entity_id": str(snapshot["id"])},
        files={"file": (f"snapshot-{snapshot['id']}.pdf", b"%PDF-1.4\nevidence\n%%EOF", "application/pdf")},
    )
    assert evidence.status_code == 201, evidence.text
    return snapshot


def _group(
    client: TestClient,
    suffix: str,
    *,
    platform_count: int = 2,
    company_name: str | None = None,
    bank_information: str | None = None,
) -> dict:
    company = client.post(
        "/api/companies",
        json={
            "name": company_name or f"Frozen Issuer {suffix}",
            "code": f"C{suffix}",
            "bank_information": bank_information or f"ORIGINAL BANK {suffix}",
        },
    ).json()
    fc = client.post(
        "/api/fcs",
        json={"company_id": company["id"], "name": f"FC {suffix}", "code": f"F{suffix}"},
    ).json()
    plan = client.post(
        "/api/fee-plans",
        json={
            "company_id": company["id"],
            "name": f"Profit 20 {suffix}",
            "code": f"PL{suffix}",
            "fee_rate_percent": "20.00",
        },
    ).json()
    customer = client.post(
        "/api/clients",
        json={
            "company_id": company["id"],
            "fc_id": fc["id"],
            "name": f"Client {suffix}",
            "management_start_date": "2025-01-01",
            "status": "ACTIVE",
        },
    ).json()
    platforms: list[dict] = []
    accounts: list[dict] = []
    for index in range(1, platform_count + 1):
        platform = client.post(
            "/api/platforms",
            json={"name": f"Platform {suffix} {index}", "code": f"P{suffix}{index}"},
        ).json()
        account = client.post(
            "/api/accounts",
            json={
                "client_id": customer["id"],
                "platform_id": platform["id"],
                "fee_plan_id": plan["id"],
                "account_number": f"ACC-{suffix}-{index}",
                "start_date": "2025-01-01",
                "status": "ACTIVE",
            },
        ).json()
        platforms.append(platform)
        accounts.append(account)
    return {
        "company": company,
        "fc": fc,
        "plan": plan,
        "client": customer,
        "platforms": platforms,
        "accounts": accounts,
    }


def _finalized_settlement(
    client: TestClient,
    data: dict,
    index: int,
    *,
    year: int,
    quarter: int,
    beginning: str = "1000.00",
    closing: str = "1100.00",
) -> dict:
    start_date, closing_date = quarter_dates(year, quarter)
    account = data["accounts"][index]
    platform = data["platforms"][index]
    beginning_snapshot = _snapshot(client, account["id"], start_date.isoformat(), beginning, closing=False)
    closing_snapshot = _snapshot(client, account["id"], closing_date.isoformat(), closing, closing=True)
    calculated = client.post(
        "/api/settlements/calculate",
        json={
            "client_id": data["client"]["id"],
            "platform_id": platform["id"],
            "fee_plan_id": data["plan"]["id"],
            "year": year,
            "quarter": quarter,
            "account_lines": [
                {
                    "account_id": account["id"],
                    "beginning_snapshot_id": beginning_snapshot["id"],
                    "closing_snapshot_id": closing_snapshot["id"],
                    "original_hwm": beginning,
                }
            ],
        },
    )
    assert calculated.status_code == 200, calculated.text
    finalized = client.post(f"/api/settlements/{calculated.json()['id']}/finalize")
    assert finalized.status_code == 200, finalized.text
    return finalized.json()


def _draft(client: TestClient, data: dict, *, year: int, quarter: int):
    return client.post(
        "/api/invoices",
        json={
            "client_id": data["client"]["id"],
            "year": year,
            "quarter": quarter,
            "fee_plan_id": data["plan"]["id"],
            "language": "zh",
        },
    )


def _pdf_text(content: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)


def test_cross_platform_invoice_freezes_all_lines_and_releases_all_sources_on_void() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "AGG829")
        positive = _finalized_settlement(client, data, 0, year=2026, quarter=1)
        zero = _finalized_settlement(
            client,
            data,
            1,
            year=2026,
            quarter=1,
            beginning="1000.00",
            closing="1000.00",
        )
        response = _draft(client, data, year=2026, quarter=1)
        assert response.status_code == 201, response.text
        invoice = response.json()
        assert invoice["settlement_ids"] == [positive["id"], zero["id"]]
        assert invoice["source_count"] == 2
        assert invoice["amount"] == "20.00"
        assert [line["service_fee"] for line in invoice["account_lines"]] == ["20.00", "0.00"]
        assert {line["platform_name"] for line in invoice["account_lines"]} == {
            data["platforms"][0]["name"],
            data["platforms"][1]["name"],
        }
        assert _draft(client, data, year=2026, quarter=1).status_code == 409
        assert client.post(
            f"/api/settlements/{zero['id']}/void", json={"reason": "active source blocks every source"}
        ).status_code == 409

        report_row = next(
            row
            for row in client.get("/api/reports/fc?year=2026&quarter=1").json()
            if row["fc_id"] == data["fc"]["id"]
        )
        assert report_row["active_client_count"] == 1
        assert report_row["charged_client_count"] == 1
        assert report_row["service_fee_generated"] == "20.00"
        assert "paid_amount" not in report_row
        assert "outstanding_amount" not in report_row

        voided = client.post(f"/api/invoices/{invoice['id']}/void", json={"reason": "rebuild group"})
        assert voided.status_code == 200, voided.text
        with SessionLocal() as db:
            active_flags = db.scalars(
                select(InvoiceSource.active).where(InvoiceSource.invoice_id == invoice["id"])
            ).all()
            assert active_flags == [False, False]
        assert client.post(
            f"/api/settlements/{zero['id']}/void", json={"reason": "source released after Invoice void"}
        ).status_code == 200


def test_concurrent_draft_creation_keeps_one_complete_invoice_group() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "RACE829")
        _finalized_settlement(client, data, 0, year=2026, quarter=1)
        _finalized_settlement(client, data, 1, year=2026, quarter=1)

        def create_draft() -> int:
            return _draft(client, data, year=2026, quarter=1).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _index: create_draft(), range(2)))
        assert sorted(statuses) == [201, 409]
        with SessionLocal() as db:
            invoices = db.scalars(
                select(Invoice).where(
                    Invoice.client_id == data["client"]["id"],
                    Invoice.year == 2026,
                    Invoice.quarter == 1,
                    Invoice.fee_plan_id == data["plan"]["id"],
                    Invoice.lifecycle_status.in_(["DRAFT", "ISSUING", "ISSUED"]),
                )
            ).all()
            assert len(invoices) == 1
            source_count = len(
                db.scalars(select(InvoiceSource).where(InvoiceSource.invoice_id == invoices[0].id)).all()
            )
            assert source_count == 2
            assert len(invoices[0].lines) == 2


def test_issue_rejects_late_finalized_settlement_and_second_active_invoice(monkeypatch) -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "LATE829")
        first = _finalized_settlement(client, data, 0, year=2026, quarter=2)
        draft = _draft(client, data, year=2026, quarter=2)
        assert draft.status_code == 201, draft.text
        late = _finalized_settlement(client, data, 1, year=2026, quarter=2)

        issued = client.post(
            f"/api/invoices/{draft.json()['id']}/issue",
            json={"issue_date": "2026-07-05", "language": "zh"},
        )
        assert issued.status_code == 409
        assert "Finalized Settlement" in issued.json()["detail"]
        monkeypatch.setattr(invoices_module, "_validate_issue_sources", lambda _db, _invoice: None)
        trigger_rejection = client.post(
            f"/api/invoices/{draft.json()['id']}/issue",
            json={"issue_date": "2026-07-05", "language": "zh"},
        )
        assert trigger_rejection.status_code == 409
        assert "并发请求改变" in trigger_rejection.json()["detail"]
        assert _draft(client, data, year=2026, quarter=2).status_code == 409
        assert set(draft.json()["settlement_ids"]) == {first["id"]}
        assert late["id"] not in draft.json()["settlement_ids"]


def test_invoice_group_rejects_inconsistent_frozen_fc() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "MIX829")
        _finalized_settlement(client, data, 0, year=2026, quarter=1)
        replacement_fc = client.post(
            "/api/fcs",
            json={
                "company_id": data["company"]["id"],
                "name": "Replacement FC MIX829",
                "code": "RF829",
            },
        ).json()
        changed = client.patch(
            f"/api/clients/{data['client']['id']}", json={"fc_id": replacement_fc["id"]}
        )
        assert changed.status_code == 200, changed.text
        _finalized_settlement(client, data, 1, year=2026, quarter=1)
        response = _draft(client, data, year=2026, quarter=1)
        assert response.status_code == 409
        assert "Company或FC不一致" in response.json()["detail"]


def test_finalize_rejects_client_company_change_after_calculate() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "OWN829", platform_count=1)
        start_date, closing_date = quarter_dates(2026, 2)
        account = data["accounts"][0]
        beginning = _snapshot(client, account["id"], start_date.isoformat(), "1000.00", closing=False)
        closing = _snapshot(client, account["id"], closing_date.isoformat(), "1100.00", closing=True)
        calculated = client.post(
            "/api/settlements/calculate",
            json={
                "client_id": data["client"]["id"],
                "platform_id": data["platforms"][0]["id"],
                "fee_plan_id": data["plan"]["id"],
                "year": 2026,
                "quarter": 2,
                "account_lines": [
                    {
                        "account_id": account["id"],
                        "beginning_snapshot_id": beginning["id"],
                        "closing_snapshot_id": closing["id"],
                        "original_hwm": "1000.00",
                    }
                ],
            },
        )
        assert calculated.status_code == 200, calculated.text
        replacement_company = client.post(
            "/api/companies", json={"name": "Replacement Owner OWN829", "code": "RO829"}
        ).json()
        replacement_fc = client.post(
            "/api/fcs",
            json={"company_id": replacement_company["id"], "name": "Replacement FC OWN829", "code": "RFC829"},
        ).json()
        changed = client.patch(
            f"/api/clients/{data['client']['id']}",
            json={"company_id": replacement_company["id"], "fc_id": replacement_fc["id"]},
        )
        assert changed.status_code == 200, changed.text
        finalized = client.post(f"/api/settlements/{calculated.json()['id']}/finalize")
        assert finalized.status_code == 409
        assert "Fee Plan不一致" in finalized.json()["detail"]


def test_invoice_create_and_issue_recheck_fee_plan_company_ownership() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "PLAN829", platform_count=1)
        _finalized_settlement(client, data, 0, year=2026, quarter=3)
        replacement_company = client.post(
            "/api/companies", json={"name": "Replacement Plan Owner PLAN829", "code": "RPO829"}
        ).json()

        with SessionLocal() as db:
            plan = db.get(FeePlan, data["plan"]["id"])
            plan.company_id = replacement_company["id"]
            db.commit()
        rejected_draft = _draft(client, data, year=2026, quarter=3)
        assert rejected_draft.status_code == 409
        assert "Fee Plan归属不一致" in rejected_draft.json()["detail"]

        with SessionLocal() as db:
            plan = db.get(FeePlan, data["plan"]["id"])
            plan.company_id = data["company"]["id"]
            db.commit()
        draft = _draft(client, data, year=2026, quarter=3)
        assert draft.status_code == 201, draft.text
        with SessionLocal() as db:
            plan = db.get(FeePlan, data["plan"]["id"])
            plan.company_id = replacement_company["id"]
            db.commit()
        rejected_issue = client.post(
            f"/api/invoices/{draft.json()['id']}/issue",
            json={"issue_date": "2026-10-05", "language": "zh"},
        )
        assert rejected_issue.status_code == 409
        assert "Fee Plan归属不一致" in rejected_issue.json()["detail"]


def test_settlement_and_invoice_pdfs_use_frozen_company_after_client_reassignment() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(
            client,
            "PDF829",
            platform_count=1,
            company_name="Frozen <Issuer> & PDF829",
            bank_information='ORIGINAL <BANK> & "Desk" PDF829',
        )
        settlement = _finalized_settlement(client, data, 0, year=2026, quarter=3)
        replacement_company = client.post(
            "/api/companies",
            json={
                "name": "Current Client Company PDF829",
                "code": "NEWPDF829",
                "bank_information": "NEW BANK PDF829",
            },
        ).json()
        replacement_fc = client.post(
            "/api/fcs",
            json={"company_id": replacement_company["id"], "name": "New FC PDF829", "code": "NF829"},
        ).json()
        changed = client.patch(
            f"/api/clients/{data['client']['id']}",
            json={"company_id": replacement_company["id"], "fc_id": replacement_fc["id"]},
        )
        assert changed.status_code == 200, changed.text

        settlement_pdf = client.post(f"/api/exports/pdf?settlement_id={settlement['id']}&language=en")
        assert settlement_pdf.status_code == 200, settlement_pdf.text
        settlement_text = _pdf_text(settlement_pdf.content)
        normalized_settlement_text = " ".join(settlement_text.split())
        assert data["company"]["name"] in normalized_settlement_text
        assert replacement_company["name"] not in settlement_text

        draft = _draft(client, data, year=2026, quarter=3)
        assert draft.status_code == 201, draft.text
        assert draft.json()["company_name"] == data["company"]["name"]
        issued = client.post(
            f"/api/invoices/{draft.json()['id']}/issue",
            json={"issue_date": "2026-10-05", "language": "en"},
        )
        assert issued.status_code == 200, issued.text
        invoice_pdf = client.post(f"/api/invoices/{draft.json()['id']}/pdf?language=en")
        assert invoice_pdf.status_code == 200, invoice_pdf.text
        invoice_text = _pdf_text(invoice_pdf.content)
        normalized_invoice_text = " ".join(invoice_text.split())
        assert data["company"]["name"] in normalized_invoice_text
        assert 'ORIGINAL <BANK> & "Desk" PDF829' in normalized_invoice_text
        assert replacement_company["name"] not in invoice_text
        assert "NEW BANK PDF829" not in invoice_text


def test_pdf_money_format_preserves_cents_above_float_precision_limit() -> None:
    assert _money(9_007_199_254_740_993) == "HKD 90,071,992,547,409.93"


def test_issuing_recovery_requires_both_pdfs_and_records_hashes() -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        return_data = _group(client, "RET829", platform_count=1)
        _finalized_settlement(client, return_data, 0, year=2026, quarter=4)
        return_draft = _draft(client, return_data, year=2026, quarter=4).json()
        complete_data = _group(client, "CMP829", platform_count=1)
        _finalized_settlement(client, complete_data, 0, year=2026, quarter=4)
        complete_draft = _draft(client, complete_data, year=2026, quarter=4).json()

        with SessionLocal() as db:
            for invoice_id, number in (
                (return_draft["id"], "CRET829-FRET829-202501-901"),
                (complete_draft["id"], "CCMP829-FCMP829-202501-902"),
            ):
                invoice = db.get(Invoice, invoice_id)
                invoice.invoice_number = number
                invoice.issue_date = date(2027, 1, 5)
                invoice.due_date = date(2027, 1, 19)
                invoice.lifecycle_status = "ISSUING"
                db.add(
                    InvoiceIssueAttempt(
                        invoice_id=invoice_id,
                        invoice_number=number,
                        status="RESERVED",
                        details="simulated process termination",
                    )
                )
            db.commit()

        pdf_root = get_settings().data_root / "output" / "pdf"
        pdf_root.mkdir(parents=True, exist_ok=True)
        return_zh = pdf_root / "CRET829-FRET829-202501-901_zh.pdf"
        return_zh.write_bytes(b"%PDF-1.4\npartial\n%%EOF")
        complete_paths = [
            pdf_root / "CCMP829-FCMP829-202501-902_zh.pdf",
            pdf_root / "CCMP829-FCMP829-202501-902_en.pdf",
        ]
        for path in complete_paths:
            path.write_bytes(b"%PDF-1.4\ncomplete\n%%EOF")

        invoices = {item["id"]: item for item in client.get("/api/invoices").json()}
        assert invoices[return_draft["id"]]["issue_recovery"] == {
            "files_complete": False,
            "can_complete": False,
            "can_return_to_draft": True,
            "invoice_number": "CRET829-FRET829-202501-901",
            "attempt_id": invoices[return_draft["id"]]["issue_recovery"]["attempt_id"],
            "attempt_status": "RESERVED",
        }
        assert invoices[complete_draft["id"]]["issue_recovery"]["files_complete"] is True
        assert client.post(
            f"/api/invoices/{return_draft['id']}/recover-issuing", json={"action": "COMPLETE"}
        ).status_code == 409
        returned = client.post(
            f"/api/invoices/{return_draft['id']}/recover-issuing",
            json={"action": "RETURN_TO_DRAFT"},
        )
        assert returned.status_code == 200, returned.text
        assert returned.json()["lifecycle_status"] == "DRAFT"
        assert returned.json()["invoice_number"] is None
        assert not return_zh.exists()

        completed = client.post(
            f"/api/invoices/{complete_draft['id']}/recover-issuing", json={"action": "COMPLETE"}
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["lifecycle_status"] == "ISSUED"
        assert completed.json()["issue_recovery"] is None
        with SessionLocal() as db:
            exports = db.scalars(
                select(ExportRecord).where(
                    ExportRecord.entity_type == "INVOICE",
                    ExportRecord.entity_id == complete_draft["id"],
                )
            ).all()
            assert {record.language for record in exports} == {"zh", "en"}
            assert all(len(record.sha256) == 64 for record in exports)
            statuses = db.scalars(
                select(InvoiceIssueAttempt.status).where(
                    InvoiceIssueAttempt.invoice_id.in_([return_draft["id"], complete_draft["id"]])
                )
            ).all()
            assert set(statuses) == {"RECOVERED_TO_DRAFT", "COMPLETED"}


def test_recover_complete_maps_late_source_trigger_to_409_and_keeps_files(monkeypatch) -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "RTO829")
        _finalized_settlement(client, data, 0, year=2026, quarter=4)
        draft = _draft(client, data, year=2026, quarter=4).json()
        invoice_number = "CRTO829-FRTO829-202501-888"
        with SessionLocal() as db:
            invoice = db.get(Invoice, draft["id"])
            invoice.invoice_number = invoice_number
            invoice.issue_date = date(2027, 1, 5)
            invoice.due_date = date(2027, 1, 19)
            invoice.lifecycle_status = "ISSUING"
            db.add(
                InvoiceIssueAttempt(
                    invoice_id=invoice.id,
                    invoice_number=invoice_number,
                    status="RESERVED",
                    details="reserved before late settlement finalized",
                )
            )
            db.commit()
        _finalized_settlement(client, data, 1, year=2026, quarter=4)
        pdf_root = get_settings().data_root / "output" / "pdf"
        paths = [
            pdf_root / f"{invoice_number}_zh.pdf",
            pdf_root / f"{invoice_number}_en.pdf",
        ]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"%PDF-1.4\ncomplete before late source\n%%EOF")

        monkeypatch.setattr(invoices_module, "_validate_issue_sources", lambda _db, _invoice: None)
        response = client.post(
            f"/api/invoices/{draft['id']}/recover-issuing", json={"action": "COMPLETE"}
        )
        assert response.status_code == 409
        assert "仍保留ISSUING及双PDF" in response.json()["detail"]
        with SessionLocal() as db:
            assert db.get(Invoice, draft["id"]).lifecycle_status == "ISSUING"
        assert all(path.is_file() for path in paths)
        returned = client.post(
            f"/api/invoices/{draft['id']}/recover-issuing", json={"action": "RETURN_TO_DRAFT"}
        )
        assert returned.status_code == 200, returned.text
        assert not any(path.exists() for path in paths)


def test_failed_issue_attempt_consumes_number_and_is_auditable(monkeypatch) -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "FAIL829", platform_count=1)
        _finalized_settlement(client, data, 0, year=2026, quarter=1)
        draft = _draft(client, data, year=2026, quarter=1).json()

        def fail_pdf(*, output_path, **_kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF-1.4\npartial customer data")
            raise RuntimeError("simulated renderer failure")

        monkeypatch.setattr(invoices_module, "generate_invoice_pdf", fail_pdf)
        failed = client.post(
            f"/api/invoices/{draft['id']}/issue",
            json={"issue_date": "2026-04-05", "language": "zh"},
        )
        assert failed.status_code == 500
        after_failure = next(item for item in client.get("/api/invoices").json() if item["id"] == draft["id"])
        assert after_failure["lifecycle_status"] == "DRAFT"
        assert after_failure["invoice_number"] is None
        with SessionLocal() as db:
            failed_attempt = db.scalar(
                select(InvoiceIssueAttempt).where(InvoiceIssueAttempt.invoice_id == draft["id"])
            )
            assert failed_attempt.status == "FAILED"
            assert failed_attempt.invoice_number.endswith("-001")
            assert failed_attempt.completed_at is not None
            failed_number = failed_attempt.invoice_number
        pdf_root = get_settings().data_root / "output" / "pdf"
        assert not list(pdf_root.glob(f".{failed_number}_*.pdf.*.tmp"))
        assert not list(pdf_root.glob(f"{failed_number}_*.pdf"))

        def write_pdf(*, output_path, **_kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF-1.4\nretry\n%%EOF")
            return output_path

        monkeypatch.setattr(invoices_module, "generate_invoice_pdf", write_pdf)
        retried = client.post(
            f"/api/invoices/{draft['id']}/issue",
            json={"issue_date": "2026-04-06", "language": "en"},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["invoice_number"].endswith("-002")


def test_legacy_missing_pdf_uses_atomic_single_writer_fallback(monkeypatch) -> None:
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "LEG829", platform_count=1)
        _finalized_settlement(client, data, 0, year=2026, quarter=2)
        draft = _draft(client, data, year=2026, quarter=2).json()
        invoice_number = "CLEG829-FLEG829-202501-777"
        with SessionLocal() as db:
            invoice = db.get(Invoice, draft["id"])
            invoice.invoice_number = invoice_number
            invoice.issue_date = date(2026, 7, 5)
            invoice.due_date = date(2026, 7, 19)
            invoice.lifecycle_status = "ISSUING"
            db.commit()
            db.expire_all()
            invoice = db.get(Invoice, draft["id"])
            invoice.lifecycle_status = "ISSUED"
            invoice.issued_at = invoice.updated_at
            db.add(
                InvoiceIssueAttempt(
                    invoice_id=invoice.id,
                    invoice_number=invoice_number,
                    status="COMPLETED",
                    completed_at=invoice.updated_at,
                    details="simulated legacy issued invoice without archived language",
                )
            )
            db.commit()

        def write_partial_then_fail(*, output_path, **_kwargs):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF-1.4\npartial legacy customer data")
            raise RuntimeError("legacy renderer failure")

        monkeypatch.setattr(invoices_module, "generate_invoice_pdf", write_partial_then_fail)
        failed = client.post(f"/api/invoices/{draft['id']}/pdf?language=en")
        assert failed.status_code == 500
        pdf_root = get_settings().data_root / "output" / "pdf"
        final_path = pdf_root / f"{invoice_number}_en.pdf"
        assert not final_path.exists()
        assert not list(pdf_root.glob(f".{invoice_number}_en.pdf.*.tmp"))

        render_calls: list[str] = []

        def write_complete(*, output_path, **_kwargs):
            render_calls.append(str(output_path))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"%PDF-1.4\ncomplete legacy archive\n%%EOF")
            return output_path

        monkeypatch.setattr(invoices_module, "generate_invoice_pdf", write_complete)

        def download() -> int:
            return client.post(f"/api/invoices/{draft['id']}/pdf?language=en").status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _index: download(), range(2)))
        assert statuses == [200, 200]
        assert len(render_calls) == 1
        assert final_path.is_file()
        with SessionLocal() as db:
            exports = db.scalars(
                select(ExportRecord).where(
                    ExportRecord.entity_type == "INVOICE",
                    ExportRecord.entity_id == draft["id"],
                    ExportRecord.language == "en",
                )
            ).all()
            assert len(exports) == 1


def test_archived_pdf_download_rejects_tampering_and_missing_audit_record(monkeypatch) -> None:
    render_calls: list[str] = []

    def write_pdf(*, output_path, **_kwargs):
        render_calls.append(str(output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"%PDF-1.4\nimmutable invoice archive\n%%EOF")
        return output_path

    monkeypatch.setattr(invoices_module, "generate_invoice_pdf", write_pdf)
    with TestClient(app, headers=WRITE_HEADERS) as client:
        data = _group(client, "HASH829", platform_count=1)
        _finalized_settlement(client, data, 0, year=2026, quarter=2)
        draft = _draft(client, data, year=2026, quarter=2).json()
        issued = client.post(
            f"/api/invoices/{draft['id']}/issue",
            json={"issue_date": "2026-07-05", "language": "zh"},
        )
        assert issued.status_code == 200, issued.text
        assert len(render_calls) == 2
        assert client.post(f"/api/invoices/{draft['id']}/pdf?language=zh").status_code == 200

        with SessionLocal() as db:
            invoice = db.get(Invoice, draft["id"])
            archive_path = get_settings().data_root / "output" / "pdf" / f"{invoice.invoice_number}_zh.pdf"
        original_content = archive_path.read_bytes()
        archive_path.write_bytes(b"%PDF-1.4\ntampered invoice\n%%EOF")
        tampered = client.post(f"/api/invoices/{draft['id']}/pdf?language=zh")
        assert tampered.status_code == 409
        assert "哈希校验失败" in tampered.json()["detail"]
        assert len(render_calls) == 2

        archive_path.write_bytes(original_content)
        voided = client.post(f"/api/invoices/{draft['id']}/void", json={"reason": "test shared archive guard"})
        assert voided.status_code == 200, voided.text
        with SessionLocal() as db:
            record = db.scalar(
                select(ExportRecord).where(
                    ExportRecord.entity_type == "INVOICE",
                    ExportRecord.entity_id == draft["id"],
                    ExportRecord.language == "zh",
                )
            )
            db.delete(record)
            db.commit()
        missing_audit = client.post(f"/api/invoices/{draft['id']}/pdf?language=zh")
        assert missing_audit.status_code == 409
        assert "归档审计记录缺失" in missing_audit.json()["detail"]
        assert archive_path.read_bytes() == original_content
        assert len(render_calls) == 2
