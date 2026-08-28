from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import (
    Attachment,
    AuditEvent,
    Company,
    ExportRecord,
    FC,
    FeePlan,
    Invoice,
    InvoiceLine,
    InvoiceSequence,
    InvoiceSource,
    Platform,
    QuarterlySettlement,
)
from app.routes.master import _delete_master_data


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


def _suffix() -> str:
    return uuid4().hex[:8].upper()


def _create_company(client: TestClient, suffix: str) -> dict:
    response = client.post(
        "/api/companies",
        json={"name": f"Delete Test Company {suffix}", "code": f"C{suffix}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_unused_master_data_can_be_deleted_and_missing_records_return_404() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        standalone_company = _create_company(client, suffix)
        owner = _create_company(client, f"O{suffix}")
        fc = client.post(
            "/api/fcs",
            json={"company_id": owner["id"], "name": f"Delete FC {suffix}", "code": f"F{suffix}"},
        ).json()
        platform = client.post(
            "/api/platforms",
            json={"name": f"Delete Platform {suffix}", "code": f"P{suffix}"},
        ).json()
        fee_plan = client.post(
            "/api/fee-plans",
            json={
                "company_id": owner["id"],
                "name": f"Delete Fee Plan {suffix}",
                "code": f"FP{suffix}",
                "fee_rate_percent": "20.00",
            },
        ).json()

        targets = (
            (f"/api/companies/{standalone_company['id']}", standalone_company["id"]),
            (f"/api/fcs/{fc['id']}", fc["id"]),
            (f"/api/platforms/{platform['id']}", platform["id"]),
            (f"/api/fee-plans/{fee_plan['id']}", fee_plan["id"]),
        )
        with SessionLocal() as db:
            audit_count_before = db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.action == "MASTER_DATA_DELETED"
                )
            ) or 0

        for path, item_id in targets:
            deleted = client.delete(path)
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"status": "deleted", "id": item_id}
            missing = client.delete(path)
            assert missing.status_code == 404
            assert "不存在" in missing.json()["detail"]

    with SessionLocal() as db:
        audit_count_after = db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.action == "MASTER_DATA_DELETED"
            )
        ) or 0
        assert audit_count_after == audit_count_before + 4
        deletion_events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "MASTER_DATA_DELETED")
            .order_by(AuditEvent.id.desc())
            .limit(4)
        ).all()
        assert {event.details_json["deleted_entity_id"] for event in deletion_events} == {
            item_id for _, item_id in targets
        }
        assert all(event.details_json["name"] for event in deletion_events)
        assert all(event.details_json["code"] for event in deletion_events)


def test_referenced_master_data_returns_explicit_409_without_deleting() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        company = _create_company(client, suffix)
        fc_response = client.post(
            "/api/fcs",
            json={"company_id": company["id"], "name": f"Used FC {suffix}", "code": f"F{suffix}"},
        )
        assert fc_response.status_code == 201, fc_response.text
        fc = fc_response.json()
        platform_response = client.post(
            "/api/platforms",
            json={"name": f"Used Platform {suffix}", "code": f"P{suffix}"},
        )
        assert platform_response.status_code == 201, platform_response.text
        platform = platform_response.json()
        plan_response = client.post(
            "/api/fee-plans",
            json={
                "company_id": company["id"],
                "name": f"Used Fee Plan {suffix}",
                "code": f"FP{suffix}",
                "fee_rate_percent": "20.00",
            },
        )
        assert plan_response.status_code == 201, plan_response.text
        fee_plan = plan_response.json()
        client_response = client.post(
            "/api/clients",
            json={
                "company_id": company["id"],
                "fc_id": fc["id"],
                "name": f"Used Client {suffix}",
                "status": "DRAFT",
            },
        )
        assert client_response.status_code == 201, client_response.text
        business_client = client_response.json()
        account_response = client.post(
            "/api/accounts",
            json={
                "client_id": business_client["id"],
                "platform_id": platform["id"],
                "fee_plan_id": fee_plan["id"],
                "account_number": f"ACC-{suffix}",
                "status": "DRAFT",
            },
        )
        assert account_response.status_code == 201, account_response.text

        cases = (
            (f"/api/companies/{company['id']}", ("FC", "Fee Plan", "Client")),
            (f"/api/fcs/{fc['id']}", ("Client",)),
            (f"/api/platforms/{platform['id']}", ("Sub Account",)),
            (f"/api/fee-plans/{fee_plan['id']}", ("Sub Account",)),
        )
        for path, expected_references in cases:
            response = client.delete(path)
            assert response.status_code == 409, response.text
            detail = response.json()["detail"]
            assert "不能删除" in detail
            for expected in expected_references:
                assert expected in detail


def test_frozen_settlement_invoice_line_and_sequence_references_are_reported_and_preserved() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        company = _create_company(client, suffix)
        fc = client.post(
            "/api/fcs",
            json={"company_id": company["id"], "name": f"Ledger FC {suffix}", "code": f"F{suffix}"},
        ).json()
        platform = client.post(
            "/api/platforms",
            json={"name": f"Ledger Platform {suffix}", "code": f"P{suffix}"},
        ).json()
        fee_plan = client.post(
            "/api/fee-plans",
            json={
                "company_id": company["id"],
                "name": f"Ledger Fee Plan {suffix}",
                "code": f"FP{suffix}",
                "fee_rate_percent": "20.00",
            },
        ).json()
        business_client = client.post(
            "/api/clients",
            json={
                "company_id": company["id"],
                "fc_id": fc["id"],
                "name": f"Ledger Client {suffix}",
                "status": "DRAFT",
            },
        ).json()

        with SessionLocal() as db:
            settlement = QuarterlySettlement(
                client_id=business_client["id"],
                platform_id=platform["id"],
                fee_plan_id=fee_plan["id"],
                company_id=company["id"],
                fc_id=fc["id"],
                year=2099,
                quarter=1,
                start_date=date(2099, 1, 1),
                closing_date=date(2099, 3, 31),
                days=90,
                beginning_cents=0,
                contribution_cents=0,
                withdrawal_cents=0,
                net_contribution_cents=0,
                closing_cents=0,
                gain_loss_cents=0,
                original_hwm_cents=0,
                adjusted_hwm_cents=0,
                watermark_difference_cents=0,
                chargeable_above_hwm_cents=0,
                service_fee_cents=0,
                next_hwm_cents=0,
                fee_rate_bps=2000,
                calculation_mode="LEGACY_GROUP_HWM",
                status="DRAFT",
            )
            db.add(settlement)
            db.flush()
            sequence = InvoiceSequence(
                company_id=company["id"], fc_id=fc["id"], last_number=1
            )
            invoice = Invoice(
                settlement_id=settlement.id,
                client_id=business_client["id"],
                year=2099,
                quarter=1,
                fee_plan_id=fee_plan["id"],
                company_id=company["id"],
                fc_id=fc["id"],
                amount_cents=0,
                lifecycle_status="DRAFT",
            )
            db.add_all([sequence, invoice])
            db.flush()
            source = InvoiceSource(
                invoice_id=invoice.id,
                settlement_id=settlement.id,
                locked_amount_cents=0,
            )
            db.add(source)
            db.flush()
            line = InvoiceLine(
                invoice_id=invoice.id,
                source_id=source.id,
                source_settlement_id=settlement.id,
                platform_id=platform["id"],
                platform_name_snapshot=f"Ledger Platform {suffix}",
                account_number_snapshot="LEGACY_GROUP_HWM",
                start_date=date(2099, 1, 1),
                closing_date=date(2099, 3, 31),
                service_fee_cents=0,
            )
            db.add(line)
            db.commit()
            settlement_id = settlement.id
            invoice_id = invoice.id
            line_id = line.id
            sequence_id = sequence.id

        cases = (
            (
                f"/api/companies/{company['id']}",
                ("Settlement", "Invoice", "Invoice编号序列"),
            ),
            (
                f"/api/fcs/{fc['id']}",
                ("Settlement", "Invoice", "Invoice编号序列"),
            ),
            (
                f"/api/platforms/{platform['id']}",
                ("Settlement", "Invoice收费行"),
            ),
            (
                f"/api/fee-plans/{fee_plan['id']}",
                ("Settlement", "Invoice"),
            ),
        )
        for path, expected_references in cases:
            response = client.delete(path)
            assert response.status_code == 409, response.text
            for expected in expected_references:
                assert expected in response.json()["detail"]

    with SessionLocal() as db:
        settlement = db.get(QuarterlySettlement, settlement_id)
        invoice = db.get(Invoice, invoice_id)
        line = db.get(InvoiceLine, line_id)
        sequence = db.get(InvoiceSequence, sequence_id)
        assert settlement is not None
        assert invoice is not None
        assert line is not None
        assert sequence is not None
        assert db.get(Company, company["id"]) is not None
        assert db.get(FC, fc["id"]) is not None
        assert db.get(Platform, platform["id"]) is not None
        assert db.get(FeePlan, fee_plan["id"]) is not None
        assert settlement.company_id == company["id"]
        assert settlement.fc_id == fc["id"]
        assert settlement.platform_id == platform["id"]
        assert settlement.fee_plan_id == fee_plan["id"]
        assert invoice.company_id == company["id"]
        assert invoice.fc_id == fc["id"]
        assert invoice.fee_plan_id == fee_plan["id"]
        assert line.platform_id == platform["id"]
        assert sequence.company_id == company["id"]
        assert sequence.fc_id == fc["id"]


@pytest.mark.parametrize(
    ("path", "history_entity_type"),
    (
        ("companies", " company "),
        ("fcs", "fc"),
        ("platforms", "Platform"),
        ("fee-plans", "Fee Plan"),
    ),
)
def test_audit_history_reference_blocks_master_data_deletion(
    path: str, history_entity_type: str
) -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        owner = _create_company(client, f"O{suffix}")
        if path == "companies":
            item = owner
        elif path == "fcs":
            response = client.post(
                "/api/fcs",
                json={"company_id": owner["id"], "name": f"History FC {suffix}", "code": f"F{suffix}"},
            )
            assert response.status_code == 201, response.text
            item = response.json()
        elif path == "platforms":
            response = client.post(
                "/api/platforms",
                json={"name": f"History Platform {suffix}", "code": f"P{suffix}"},
            )
            assert response.status_code == 201, response.text
            item = response.json()
        else:
            response = client.post(
                "/api/fee-plans",
                json={
                    "company_id": owner["id"],
                    "name": f"History Fee Plan {suffix}",
                    "code": f"FP{suffix}",
                    "fee_rate_percent": "20.00",
                },
            )
            assert response.status_code == 201, response.text
            item = response.json()

        with SessionLocal() as db:
            db.add(
                AuditEvent(
                    action="LEGACY_MASTER_HISTORY",
                    entity_type=history_entity_type,
                    entity_id=item["id"],
                )
            )
            db.commit()

        response = client.delete(f"/api/{path}/{item['id']}")
        assert response.status_code == 409, response.text
        assert "审计记录" in response.json()["detail"]


def test_attachment_and_export_history_references_block_deletion() -> None:
    suffix = _suffix()
    with TestClient(app, headers=WRITE_HEADERS) as client:
        attachment_company = _create_company(client, f"A{suffix}")
        export_company = _create_company(client, f"E{suffix}")
        with SessionLocal() as db:
            db.add(
                Attachment(
                    entity_type=" company ",
                    entity_id=attachment_company["id"],
                    original_name="legacy.pdf",
                    stored_path=f"legacy-master-{suffix}.pdf",
                    sha256="a" * 64,
                    size_bytes=1,
                )
            )
            db.add(
                ExportRecord(
                    export_type="LEGACY",
                    entity_type="Company",
                    entity_id=export_company["id"],
                    stored_path=f"legacy-export-{suffix}.pdf",
                    sha256="b" * 64,
                )
            )
            db.commit()

        attachment_response = client.delete(
            f"/api/companies/{attachment_company['id']}"
        )
        assert attachment_response.status_code == 409, attachment_response.text
        assert "附件记录" in attachment_response.json()["detail"]

        export_response = client.delete(f"/api/companies/{export_company['id']}")
        assert export_response.status_code == 409, export_response.text
        assert "导出记录" in export_response.json()["detail"]


def test_master_delete_requires_local_write_marker() -> None:
    suffix = _suffix()
    with TestClient(app) as client:
        created = client.post(
            "/api/platforms",
            headers=WRITE_HEADERS,
            json={"name": f"Protected Platform {suffix}", "code": f"P{suffix}"},
        )
        assert created.status_code == 201, created.text

        rejected = client.delete(f"/api/platforms/{created.json()['id']}")
        assert rejected.status_code == 403
        assert rejected.json()["detail"] == "缺少本地系统请求标记"


def test_database_foreign_key_failure_is_mapped_to_stable_409() -> None:
    suffix = _suffix()
    with TestClient(app):
        with SessionLocal() as db:
            company = Company(name=f"FK Company {suffix}", code=f"C{suffix}")
            db.add(company)
            db.flush()
            db.add(FC(company_id=company.id, name=f"FK FC {suffix}", code=f"F{suffix}"))
            db.commit()

            with pytest.raises(HTTPException) as exc_info:
                _delete_master_data(
                    db,
                    item=company,
                    entity_name="Company",
                    entity_type="COMPANY",
                    reference_queries=[],
                )

            assert exc_info.value.status_code == 409
            assert exc_info.value.detail == "Company已被其他资料或历史记录引用，不能删除"
            assert db.get(Company, company.id) is not None
