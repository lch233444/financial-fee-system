"""Synthetic read-model fixtures, independent of the shared API test database."""
from datetime import date
from hashlib import sha256
import json
from time import perf_counter

import pytest
from sqlalchemy import create_engine, event, select, delete
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    Attachment, BalanceSnapshot, Client, Company, FC, FeePlan, Invoice,
    InvoiceCorrection, Platform, QuarterlySettlement, SettlementAccountLine,
    StatementImport, SubAccount,
)
from app.routes.invoices import _invoice_query
from app.routes.master import list_balance_snapshots
from app.routes import master
from app.routes.settlements import list_settlements
from app.serializers import invoice_dict, settlement_dict


def _fixture(count, url="sqlite://"):
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        company = Company(name="Synthetic company", code="TEST")
        fc = FC(name="Synthetic FC")
        platform = Platform(name="Synthetic platform", code="TEST")
        plan = FeePlan(name="Synthetic plan", fee_rate_bps=2000)
        source = StatementImport(original_name="synthetic.png", stored_path="synthetic.png",
                                 sha256="0" * 64, mime_type="image/png", status="CONFIRMED")
        db.add_all([company, fc, platform, plan, source])
        db.flush()
        for index in range(count):
            customer = Client(name=f"Client {index}", fc_id=fc.id, status="ACTIVE")
            db.add(customer)
            db.flush()
            accounts = [SubAccount(client_id=customer.id, platform_id=platform.id,
                        fee_plan_id=plan.id, account_number=f"{index}-{j}") for j in range(2)]
            db.add_all(accounts)
            db.flush()
            snapshots = []
            for account in accounts:
                rows = [BalanceSnapshot(account_id=account.id, as_of_date=day,
                         total_balance_cents=10001 + j,
                         statement_import_id=source.id if j == 2 else None)
                        for j, day in enumerate((date(2025,12,31), date(2026,3,31), date(2026,6,30)))]
                db.add_all(rows)
                db.flush()
                for snapshot in rows[:2]:
                    db.add(Attachment(entity_type="SNAPSHOT", entity_id=snapshot.id,
                           original_name="active.png", stored_path=f"active-{snapshot.id}.png",
                           sha256="1" * 64, size_bytes=10, superseded=False))
                snapshots.append(rows)
            settlements = []
            for quarter in (1,2):
                values = {column.name: 0 for column in QuarterlySettlement.__table__.columns
                          if column.name.endswith("_cents")}
                item = QuarterlySettlement(client_id=customer.id, platform_id=platform.id,
                    fee_plan_id=plan.id, company_id=company.id, fc_id=fc.id,
                    year=2026, quarter=quarter, start_date=date(2026, 1 if quarter == 1 else 4, 1),
                    closing_date=date(2026, 3 if quarter == 1 else 6, 31 if quarter == 1 else 30),
                    days=90, fee_rate_bps=2000, **values)
                db.add(item)
                db.flush()
                for account, rows in zip(accounts, snapshots):
                    db.add(SettlementAccountLine(settlement_id=item.id, account_id=account.id,
                        beginning_snapshot_id=rows[quarter-1].id, closing_snapshot_id=rows[quarter].id,
                        start_date=item.start_date, closing_date=item.closing_date, days=90,
                        beginning_cents=10001, closing_cents=10002))
                settlements.append(item)
            # No correction, OPEN, COMPLETED, and a completed -> open chain.
            shape = index % 4
            invoices = []
            for n in range(1 if shape < 2 else 2):
                invoice = Invoice(settlement_id=settlements[0].id, client_id=customer.id,
                    year=2026, quarter=1, fee_plan_id=plan.id, company_id=company.id, fc_id=fc.id,
                    amount_cents=123, lifecycle_status="DRAFT" if shape == 0 else "VOID")
                db.add(invoice)
                db.flush()
                invoices.append(invoice)
            if shape:
                db.add(InvoiceCorrection(original_invoice_id=invoices[0].id,
                    replacement_invoice_id=invoices[1].id if shape >= 2 else None,
                    status="COMPLETED" if shape >= 2 else "OPEN", reason="Synthetic correction"))
            if shape == 3:
                db.add(InvoiceCorrection(original_invoice_id=invoices[1].id,
                    status="OPEN", reason="Synthetic second correction"))
        db.commit()
    return engine


def _measure(engine, operation):
    queries = []
    def record(_conn, _cursor, statement, *_args):
        queries.append(statement)
    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as db:
            start = perf_counter()
            result = operation(db)
            elapsed = (perf_counter() - start) * 1000
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return result, len(queries), elapsed


def _invoices(db):
    return [invoice_dict(item) for item in db.scalars(_invoice_query().order_by(Invoice.id)).all()]


@pytest.mark.parametrize("operation", [list_balance_snapshots, list_settlements, _invoices])
def test_list_query_growth_is_bounded_and_payload_preserved(operation):
    measurements = []
    for count in (4, 16):
        engine = _fixture(count)
        try:
            payload, queries, elapsed = _measure(engine, lambda db: operation(db=db))
            digest = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            print(f"{operation.__name__}: clients={count} SQL={queries} ms={elapsed:.2f} JSON={digest}")
            measurements.append(queries)
            assert payload
            if operation is list_balance_snapshots:
                assert len(payload) == count * 6
                assert all(row["evidence_count"] == 1 for row in payload)
                assert all(row["total_balance"] in ("100.01", "100.02", "100.03") for row in payload)
            elif operation is list_settlements:
                assert len(payload) == count * 2
                with Session(engine) as db:
                    expected = [settlement_dict(db.get(QuarterlySettlement, row["id"]), db=db) for row in payload]
                assert payload == expected
                assert all(line["beginning_evidence_count"] == line["closing_evidence_count"] == 1
                           for row in payload for line in row["account_lines"])
            else:
                with Session(engine) as db:
                    expected = [invoice_dict(item) for item in db.scalars(select(Invoice).order_by(Invoice.id)).all()]
                assert payload == expected
                assert any(row["original_invoice_id"] for row in payload)
                assert any(row["replacement_invoice_id"] for row in payload)
        finally:
            engine.dispose()
    assert measurements[1] <= measurements[0] + 5


def test_settlement_evidence_ignores_superseded_and_preserves_missing_semantics():
    engine = _fixture(1)
    try:
        with Session(engine) as db:
            attachment = db.scalar(select(Attachment))
            attachment.superseded = True
            db.commit()
            item = db.scalar(select(QuarterlySettlement).where(QuarterlySettlement.quarter == 1))
            payload = settlement_dict(item, db=db)
            assert payload["account_lines"][0]["beginning_evidence_count"] == 0
            # No source ID and missing referenced source must not be counted as proof.
            item.account_lines[0].beginning_snapshot_id = None
            item.account_lines[1].closing_snapshot_id = 999999
            db.flush()
            payload = settlement_dict(item, db=db)
            assert payload["account_lines"][0]["beginning_evidence_count"] is None
            assert payload["account_lines"][1]["closing_evidence_count"] == 0
            assert settlement_dict(item)["account_lines"][0]["closing_evidence_count"] is None
    finally:
        engine.dispose()


def test_snapshot_list_tolerates_concurrent_removal_of_unreferenced_import(tmp_path, monkeypatch):
    engine = _fixture(1, f"sqlite:///{tmp_path / 'read.db'}")
    try:
        with Session(engine) as db:
            source = StatementImport(original_name="unused.pdf", stored_path="unused.pdf",
                                     sha256="f" * 64, mime_type="application/pdf", status="CONFIRMED")
            db.add(source)
            db.flush()
            snapshot = BalanceSnapshot(account_id=1, as_of_date=date(2026,9,30),
                total_balance_cents=10003, statement_import_id=source.id)
            db.add(snapshot)
            db.flush()
            snapshot_id, source_id = snapshot.id, source.id
            db.commit()
        original = master.snapshot_evidence_counts
        def delete_between_queries(db, ids):
            ids = list(ids)
            assert snapshot_id in ids
            with Session(engine) as writer:
                writer.execute(delete(BalanceSnapshot).where(BalanceSnapshot.id == snapshot_id))
                writer.execute(delete(StatementImport).where(StatementImport.id == source_id))
                writer.commit()
            return original(db, ids)
        monkeypatch.setattr(master, "snapshot_evidence_counts", delete_between_queries)
        with Session(engine) as reader:
            result = list_balance_snapshots(db=reader)
        assert next(row for row in result if row["id"] == snapshot_id)["evidence_count"] == 0
    finally:
        engine.dispose()
