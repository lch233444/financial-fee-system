"""Upgrade real d4 schemas with synthetic history, including an old skipped quarter."""
from datetime import date
import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import app.database as database_module
from app.models import (
    Attachment, BalanceSnapshot, Client, Company, FC, FeePlan, Platform,
    QuarterlySettlement, SettlementAccountLine, SubAccount, TransactionRecord, utcnow,
)
from app.services.calculation import calculate_account_settlement
from app.services.entity_ids import allocate_entity_id
from app.services.settlement_period import transaction_locked_settlement_id
from app.services.workflow_guard_contract import workflow_trigger_sql_is_current
from test_deletion_guard_migration import _settings, _alembic_config, _trigger_sql

PREVIOUS = "d4f8a1c73b29"
HEAD = "e8b2c6d91a04"


def _seed_history(settings, *, finalize_q3=True):
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        def add(model, **values):
            row = model(**values)
            if model in (Client, SubAccount, BalanceSnapshot):
                row.id = allocate_entity_id(db, model)
            db.add(row)
            db.flush()
            return row

        company = add(Company, name="Synthetic Company", code="TEST")
        fc = add(FC, company_id=company.id, name="Synthetic FC", code="FC")
        platform = add(Platform, name="Synthetic Platform", code="PL")
        plan = add(FeePlan, company_id=company.id, name="20 percent", code="PS")
        client = add(Client, company_id=company.id, fc_id=fc.id, name="Synthetic Client",
                     management_start_date=date(2026, 1, 1), status="ACTIVE")
        account = add(SubAccount, client_id=client.id, platform_id=platform.id,
                      fee_plan_id=plan.id, account_number="SYNTHETIC", start_date=date(2026, 1, 1), status="ACTIVE")

        def proof(kind, row):
            add(Attachment, entity_type=kind, entity_id=row.id, original_name="synthetic.pdf",
                stored_path=f"synthetic/{kind}-{row.id}.pdf", sha256="0" * 64, size_bytes=1)

        def snapshot(day, cents):
            row = add(BalanceSnapshot, account_id=account.id, as_of_date=day,
                      total_balance_cents=cents, eligible_for_closing=True)
            proof("SNAPSHOT", row)
            return row

        opening = snapshot(date(2026, 1, 1), 100_000)
        q1_closing = snapshot(date(2026, 3, 31), 160_000)
        q3_closing = snapshot(date(2026, 9, 30), 200_000)

        def settlement(quarter, beginning, closing, contribution=0, previous=None):
            values = calculate_account_settlement(
                start_date=date(2026, quarter * 3 - 2, 1), closing_date=closing.as_of_date,
                beginning_cents=beginning.total_balance_cents, closing_cents=closing.total_balance_cents,
                contribution_cents=contribution, withdrawal_cents=0,
                original_hwm_cents=beginning.total_balance_cents, fee_rate_bps=2000,
            ).to_dict()
            row = add(QuarterlySettlement, client_id=client.id, platform_id=platform.id,
                      fee_plan_id=plan.id, company_id=company.id, fc_id=fc.id,
                      year=2026, quarter=quarter,
                      previous_settlement_id=previous.settlement_id if previous else None, **values)
            line = add(SettlementAccountLine, settlement_id=row.id, account_id=account.id,
                       beginning_snapshot_id=beginning.id, closing_snapshot_id=closing.id,
                       previous_line_id=previous.id if previous else None, **values)
            if quarter == 1 or finalize_q3:
                row.status = "FINALIZED"
                row.finalized_at = utcnow()
                db.flush()
            return row, line

        q1, q1_line = settlement(1, opening, q1_closing)
        transaction = add(TransactionRecord, account_id=account.id, transaction_date=date(2026, 5, 1),
                          transaction_type="CONTRIBUTION", amount_cents=10_000)
        proof("TRANSACTION", transaction)
        q3, _ = settlement(3, q1_closing, q3_closing, 10_000, q1_line)
        result = account.id, transaction.id, q3.id
        db.commit()
    engine.dispose()
    return result


def _business_rows(path):
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version', 'sqlite_sequence') ORDER BY name"
        )]
        return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}


def test_upgrade_preserves_skipped_history_and_locks_every_used_cash_date(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "history", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    account_id, transaction_id, q3_id = _seed_history(settings)
    before = _business_rows(settings.database_path)
    command.upgrade(config, "head")
    assert _business_rows(settings.database_path) == before
    assert len(_trigger_sql(settings.database_path)) == 57
    assert workflow_trigger_sql_is_current(_trigger_sql(settings.database_path))
    engine = create_engine(settings.database_url)
    with Session(engine) as db:
        assert transaction_locked_settlement_id(db, account_id=account_id, transaction_date=date(2026, 5, 1)) == q3_id
    engine.dispose()
    with sqlite3.connect(settings.database_path) as connection:
        for statement, params in [
            ("UPDATE transactions SET amount_cents=20000 WHERE id=?", (transaction_id,)),
            ("UPDATE transactions SET transaction_date='2027-01-01' WHERE id=?", (transaction_id,)),
            ("DELETE FROM transactions WHERE id=?", (transaction_id,)),
            ("INSERT INTO transactions (account_id,transaction_date,transaction_type,amount_cents,created_at,updated_at) VALUES (?,'2026-05-02','CONTRIBUTION',100,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (account_id,)),
        ]:
            with pytest.raises(sqlite3.IntegrityError, match="finalized|frozen"):
                connection.execute(statement, params)
            connection.rollback()
        # Moving currently unlocked cash into a consumed date is also forbidden.
        cursor = connection.execute("INSERT INTO transactions (account_id,transaction_date,transaction_type,amount_cents,created_at,updated_at) VALUES (?,'2027-01-01','CONTRIBUTION',100,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", (account_id,))
        future_id = cursor.lastrowid
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="finalized|frozen"):
            connection.execute("UPDATE transactions SET transaction_date='2026-05-03' WHERE id=?", (future_id,))
        connection.rollback()
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migrated_draft_cannot_finalize_across_missing_quarter_even_by_sql(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "draft", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    _, _, q3_id = _seed_history(settings, finalize_q3=False)
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="settlement_missing_previous_quarter"):
            connection.execute("UPDATE quarterly_settlements SET status='FINALIZED', finalized_at=CURRENT_TIMESTAMP WHERE id=?", (q3_id,))


def test_workflow_migration_failure_rolls_back_all_trigger_changes(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "rollback", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    before = _trigger_sql(settings.database_path)
    creates = 0

    def fail_second_create(conn, cursor, statement, parameters, context, executemany):
        nonlocal creates
        if statement.lstrip().upper().startswith("CREATE TRIGGER"):
            creates += 1
            if creates == 2:
                raise RuntimeError("synthetic interrupted DDL")

    event.listen(Engine, "before_cursor_execute", fail_second_create)
    try:
        with pytest.raises(RuntimeError, match="synthetic interrupted DDL"):
            command.upgrade(config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail_second_create)
    assert creates == 2
    assert _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (PREVIOUS,)


def test_workflow_upgrade_rejects_incomplete_previous_guards(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "incomplete", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("DROP TRIGGER trg_transactions_delete_block_frozen_period")
    before = _trigger_sql(settings.database_path)
    with pytest.raises(RuntimeError, match="缺少完整前序Trigger"):
        command.upgrade(config, "head")
    assert _trigger_sql(settings.database_path) == before


def test_unstamped_d4_database_runs_workflow_migration(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "unstamped", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("DROP TABLE alembic_version")
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "settings", settings)
    try:
        database_module.init_db()
        assert workflow_trigger_sql_is_current(_trigger_sql(settings.database_path))
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (HEAD,)
    finally:
        engine.dispose()
