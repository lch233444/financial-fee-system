from __future__ import annotations

import sqlite3
from pathlib import Path
from queue import Queue
from threading import Event, Thread

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import app.config as config_module
import app.database as database_module
from app import models as _models  # noqa: F401 - register all metadata tables
from app.config import Settings


HEAD_REVISION = "c4b7f1d92e60"
PREVIOUS_REVISION = "a6d1f4c28b73"


def _alembic_config(settings: Settings) -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    return alembic_config


def _settings(tmp_path: Path, name: str, monkeypatch) -> Settings:
    settings = Settings(data_root=tmp_path / name)
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    return settings


def test_empty_database_upgrades_to_invoice_aggregation_head(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, "empty", monkeypatch)
    alembic_config = _alembic_config(settings)

    command.upgrade(alembic_config, "head")

    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    invoice_columns = {column["name"]: column for column in inspector.get_columns("invoices")}
    assert {"client_id", "year", "quarter", "fee_plan_id"}.issubset(invoice_columns)
    assert all(
        invoice_columns[column_name]["nullable"] is False
        for column_name in ("client_id", "year", "quarter", "fee_plan_id")
    )
    assert {"invoice_sources", "invoice_lines", "invoice_issue_attempts"}.issubset(
        inspector.get_table_names()
    )
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        schema_objects = {
            row.name: row.sql or ""
            for row in connection.execute(
                text(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type IN ('trigger', 'index')"
                )
            )
        }
    assert revision == HEAD_REVISION
    assert "WHERE active = 1" in schema_objects["uq_invoice_sources_active_settlement"]
    assert "lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')" in schema_objects[
        "uq_invoices_active_client_period_plan"
    ]
    assert "invoice_sources" in schema_objects["trg_settlement_validate_void"]
    assert "invoice_lines" in schema_objects["trg_invoice_validate_issue"]
    engine.dispose()


def _seed_0_2_8_invoice_states(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            INSERT INTO companies
                (id, name, code, payment_terms_days, active, created_at, updated_at)
            VALUES (1, 'Migration Test Company', 'MTC', 14, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO fcs
                (id, company_id, name, code, active, created_at, updated_at)
            VALUES (1, 1, 'Migration Test FC', 'MTF', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO platforms
                (id, name, code, active, created_at, updated_at)
            VALUES (1, 'Migration Test Platform', 'MTP', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO fee_plans
                (id, company_id, name, code, fee_rate_bps, calculation_method,
                 active, created_at, updated_at)
            VALUES (1, 1, 'Migration Test Plan', 'MT20', 2000, 'HIGH_WATER_MARK',
                    1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO clients
                (id, company_id, fc_id, name, management_start_date, status, created_at, updated_at)
            VALUES
                (1, 1, 1, 'Migration Client One', '2024-01-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, 1, 1, 'Migration Client Two', '2024-02-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (3, 1, 1, 'Migration Client Three', '2024-03-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (4, 1, 1, 'Migration Client Four', '2024-04-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO sub_accounts
                (id, client_id, platform_id, fee_plan_id, account_number, currency,
                 start_date, status, created_at, updated_at)
            VALUES
                (1, 1, 1, 1, 'MIG-001-A', 'HKD', '2026-01-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, 1, 1, 1, 'MIG-001-B', 'HKD', '2026-01-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (3, 2, 1, 1, 'MIG-002-A', 'HKD', '2026-01-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (4, 3, 1, 1, 'MIG-003-A', 'HKD', '2026-01-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (5, 4, 1, 1, 'MIG-004-A', 'HKD', '2026-01-01', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO quarterly_settlements
                (id, client_id, platform_id, fee_plan_id, company_id, fc_id,
                 year, quarter, start_date, closing_date, days,
                 beginning_cents, contribution_cents, withdrawal_cents,
                 net_contribution_cents, closing_cents, gain_loss_cents,
                 period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                 watermark_difference_cents, chargeable_above_hwm_cents,
                 service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                 calculation_mode, status, finalized_at, created_at, updated_at)
            VALUES
                (1, 1, 1, 1, 1, 1, 2026, 1, '2026-01-01', '2026-03-31', 90,
                 100000, 0, 0, 0, 115000, 15000, 150000, 100000, 100000,
                 15000, 15000, 3000, 115000, 2000, 'HWM-2.0-ACCOUNT',
                 'ACCOUNT_HWM', 'FINALIZED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, 2, 1, 1, 1, 1, 2026, 1, '2026-01-01', '2026-03-31', 90,
                 100000, 0, 0, 0, 120000, 20000, 200000, 100000, 100000,
                 20000, 20000, 4000, 120000, 2000, 'HWM-2.0-ACCOUNT',
                 'ACCOUNT_HWM', 'FINALIZED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (3, 3, 1, 1, 1, 1, 2026, 1, '2026-01-01', '2026-03-31', 90,
                 100000, 0, 0, 0, 125000, 25000, 250000, 100000, 100000,
                 25000, 25000, 5000, 125000, 2000, 'HWM-1.0',
                 'LEGACY_GROUP_HWM', 'FINALIZED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (4, 4, 1, 1, 1, 1, 2026, 1, '2026-01-01', '2026-03-31', 90,
                 100000, 0, 0, 0, 130000, 30000, 300000, 100000, 100000,
                 30000, 30000, 6000, 130000, 2000, 'HWM-1.0',
                 'LEGACY_GROUP_HWM', 'FINALIZED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO settlement_account_lines
                (id, settlement_id, account_id, start_date, closing_date, days,
                 beginning_cents, closing_cents, contribution_cents, withdrawal_cents,
                 net_contribution_cents, gain_loss_cents, period_rate_ppm,
                 original_hwm_cents, adjusted_hwm_cents, watermark_difference_cents,
                 chargeable_above_hwm_cents, service_fee_cents, next_hwm_cents,
                 fee_rate_bps, formula_version, created_at, updated_at)
            VALUES
                (1, 1, 1, '2026-01-01', '2026-03-31', 90,
                 50000, 60000, 0, 0, 0, 10000, 200000, 50000, 50000, 10000,
                 10000, 1000, 60000, 2000, 'HWM-2.0-ACCOUNT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, 1, 2, '2026-01-01', '2026-03-31', 90,
                 50000, 55000, 0, 0, 0, 5000, 100000, 50000, 50000, 5000,
                 5000, 2000, 55000, 2000, 'HWM-2.0-ACCOUNT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (3, 2, 3, '2026-01-01', '2026-03-31', 90,
                 100000, 120000, 0, 0, 0, 20000, 200000, 100000, 100000, 20000,
                 20000, 4000, 120000, 2000, 'HWM-2.0-ACCOUNT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (4, 3, 4, '2026-01-01', '2026-03-31', 90,
                 100000, 125000, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                 NULL, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (5, 4, 5, '2026-01-01', '2026-03-31', 90,
                 100000, 130000, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                 NULL, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO invoices
                (id, settlement_id, company_id, fc_id, invoice_number,
                 lifecycle_status, issue_date, due_date, amount_cents, language,
                 issued_at, voided_at, void_reason, pdf_paths_json, created_at, updated_at)
            VALUES
                (1, 1, 1, 1, NULL, 'DRAFT', NULL, NULL, 3000, 'zh',
                 NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (2, 2, 1, 1, 'MTC-MTF-202402-001', 'ISSUING', '2026-04-01', '2026-04-15',
                 4000, 'zh', NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (3, 3, 1, 1, 'MTC-MTF-202403-002', 'ISSUED', '2026-04-01', '2026-04-15',
                 5000, 'zh', CURRENT_TIMESTAMP, NULL, NULL, '{"zh":"archived-zh.pdf"}',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (4, 4, 1, 1, 'MTC-MTF-202404-003', 'VOID', '2026-04-01', '2026-04-15',
                 6000, 'zh', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'Migration void fixture',
                 '{"zh":"void-archive.pdf"}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO payments
                (id, invoice_id, payment_date, amount_cents, method, remark, created_at, updated_at)
            VALUES (1, 3, '2026-04-10', 1234, 'BANK', 'Legacy partial payment',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            """
        )
        connection.commit()


def test_0_2_8_invoices_backfill_sources_lines_attempts_and_keep_payments(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path, "legacy-invoices", monkeypatch)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, PREVIOUS_REVISION)
    _seed_0_2_8_invoice_states(settings.database_path)

    command.upgrade(alembic_config, "head")
    # A repeated startup/upgrade must not duplicate frozen records.
    command.upgrade(alembic_config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        invoice_rows = connection.execute(
            """
            SELECT id, settlement_id, client_id, year, quarter, fee_plan_id,
                   lifecycle_status, invoice_number, amount_cents, pdf_paths_json
            FROM invoices ORDER BY id
            """
        ).fetchall()
        source_rows = connection.execute(
            """
            SELECT invoice_id, settlement_id, locked_amount_cents, active
            FROM invoice_sources ORDER BY invoice_id
            """
        ).fetchall()
        line_rows = connection.execute(
            """
            SELECT invoice_id, source_account_line_id, platform_name_snapshot,
                   account_number_snapshot, start_date, closing_date,
                   service_fee_cents, display_order
            FROM invoice_lines ORDER BY invoice_id, display_order
            """
        ).fetchall()
        attempt_rows = connection.execute(
            """
            SELECT invoice_id, invoice_number, status, completed_at IS NOT NULL
            FROM invoice_issue_attempts ORDER BY invoice_id
            """
        ).fetchall()
        payment_row = connection.execute(
            "SELECT id, invoice_id, amount_cents, method, remark FROM payments WHERE id = 1"
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

        assert revision == HEAD_REVISION
        assert [row[:7] for row in invoice_rows] == [
            (1, 1, 1, 2026, 1, 1, "DRAFT"),
            (2, 2, 2, 2026, 1, 1, "ISSUING"),
            (3, 3, 3, 2026, 1, 1, "ISSUED"),
            (4, 4, 4, 2026, 1, 1, "VOID"),
        ]
        assert invoice_rows[2][7:] == (
            "MTC-MTF-202403-002",
            5000,
            '{"zh":"archived-zh.pdf"}',
        )
        assert source_rows == [
            (1, 1, 3000, 1),
            (2, 2, 4000, 1),
            (3, 3, 5000, 1),
            (4, 4, 6000, 0),
        ]
        assert line_rows == [
            (1, 1, "Migration Test Platform", "MIG-001-A", "2026-01-01", "2026-03-31", 1000, 0),
            (1, 2, "Migration Test Platform", "MIG-001-B", "2026-01-01", "2026-03-31", 2000, 1),
            (2, 3, "Migration Test Platform", "MIG-002-A", "2026-01-01", "2026-03-31", 4000, 0),
            (3, None, "Migration Test Platform", "LEGACY_GROUP_HWM", "2026-01-01", "2026-03-31", 5000, 0),
            (4, None, "Migration Test Platform", "LEGACY_GROUP_HWM", "2026-01-01", "2026-03-31", 6000, 0),
        ]
        assert attempt_rows == [
            (2, "MTC-MTF-202402-001", "RESERVED", 0),
            (3, "MTC-MTF-202403-002", "COMPLETED", 1),
            (4, "MTC-MTF-202404-003", "COMPLETED", 1),
        ]
        assert payment_row == (1, 3, 1234, "BANK", "Legacy partial payment")

        # A newly-finalized same-group Settlement cannot race past the route's
        # source-set comparison and be omitted from the Invoice.
        connection.execute(
            """
            INSERT INTO platforms
                (id, name, code, active, created_at, updated_at)
            VALUES (2, 'Late Finalized Platform', 'LFP', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
        connection.execute(
            """
            INSERT INTO quarterly_settlements (
                id, client_id, platform_id, fee_plan_id, company_id, fc_id,
                year, quarter, start_date, closing_date, days,
                beginning_cents, contribution_cents, withdrawal_cents,
                net_contribution_cents, closing_cents, gain_loss_cents,
                period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                watermark_difference_cents, chargeable_above_hwm_cents,
                service_fee_cents, next_hwm_cents, fee_rate_bps, formula_version,
                calculation_mode, status, finalized_at, created_at, updated_at
            ) VALUES (
                6, 1, 2, 1, 1, 1, 2026, 1, '2026-01-01', '2026-03-31', 90,
                100000, 0, 0, 0, 105000, 5000, 50000, 100000, 100000,
                5000, 5000, 1000, 105000, 2000, 'HWM-1.0',
                'LEGACY_GROUP_HWM', 'FINALIZED', CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="invoice_source_set_incomplete"):
            connection.execute(
                """
                UPDATE invoices
                SET invoice_number = 'MTC-MTF-202401-010',
                    issue_date = '2026-04-01', due_date = '2026-04-15',
                    lifecycle_status = 'ISSUING'
                WHERE id = 1
                """
            )
        connection.execute("UPDATE quarterly_settlements SET status = 'VOID' WHERE id = 6")

        # NULL frozen ownership cannot pass the database Issue gate.
        connection.execute("UPDATE quarterly_settlements SET company_id = NULL WHERE id = 1")
        with pytest.raises(sqlite3.IntegrityError, match="invoice_source_mismatch"):
            connection.execute(
                """
                UPDATE invoices
                SET invoice_number = 'MTC-MTF-202401-010',
                    issue_date = '2026-04-01', due_date = '2026-04-15',
                    lifecycle_status = 'ISSUING'
                WHERE id = 1
                """
            )
        connection.execute("UPDATE quarterly_settlements SET company_id = 1 WHERE id = 1")

        # Complete frozen data can issue, after which source and line values are immutable.
        connection.execute(
            """
            UPDATE invoices
            SET invoice_number = 'MTC-MTF-202401-010',
                issue_date = '2026-04-01', due_date = '2026-04-15',
                lifecycle_status = 'ISSUING'
            WHERE id = 1
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="invoice_sources_locked"):
            connection.execute(
                "UPDATE invoice_sources SET locked_amount_cents = 2999 WHERE invoice_id = 1"
            )
        with pytest.raises(sqlite3.IntegrityError, match="invoice_lines_locked"):
            connection.execute(
                "UPDATE invoice_lines SET service_fee_cents = 999 WHERE invoice_id = 1"
            )

        # The partial unique Invoice index prevents a second active quarterly bill.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO invoices (
                    settlement_id, client_id, year, quarter, fee_plan_id, company_id, fc_id,
                    lifecycle_status, amount_cents, language, created_at, updated_at
                ) VALUES (1, 1, 2026, 1, 1, 1, 1, 'DRAFT', 3000, 'zh',
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )

        # A source on a different Draft anchor still blocks voiding its Settlement.
        connection.execute(
            """
            INSERT INTO invoices (
                id, settlement_id, client_id, year, quarter, fee_plan_id, company_id, fc_id,
                lifecycle_status, amount_cents, language, created_at, updated_at
            ) VALUES (5, 1, 2, 2027, 1, 1, 1, 1, 'DRAFT', 6000, 'zh',
                      CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
        source_id = connection.execute(
            """
            INSERT INTO invoice_sources (
                invoice_id, settlement_id, locked_amount_cents, active, created_at, updated_at
            ) VALUES (5, 4, 6000, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            RETURNING id
            """
        ).fetchone()[0]
        line_id = connection.execute(
            """
            INSERT INTO invoice_lines (
                invoice_id, source_id, source_settlement_id, source_account_line_id,
                platform_id, platform_name_snapshot, account_number_snapshot,
                start_date, closing_date, service_fee_cents, display_order,
                created_at, updated_at
            ) VALUES (
                5, ?, 4, NULL, 1, 'Migration Test Platform', 'DRAFT-FROZEN-LINE',
                '2026-01-01', '2026-03-31', 6000, 0,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            RETURNING id
            """,
            (source_id,),
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="invoice_sources_locked"):
            connection.execute(
                "UPDATE invoice_sources SET invoice_id = 3 WHERE id = ?", (source_id,)
            )
        with pytest.raises(sqlite3.IntegrityError, match="invoice_lines_locked"):
            connection.execute("UPDATE invoice_lines SET invoice_id = 3 WHERE id = ?", (line_id,))
        issued_source_id = connection.execute(
            "SELECT id FROM invoice_sources WHERE invoice_id = 3"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="invoice_line_source_mismatch"):
            connection.execute(
                """
                INSERT INTO invoice_lines (
                    invoice_id, source_id, source_settlement_id, source_account_line_id,
                    platform_id, platform_name_snapshot, account_number_snapshot,
                    service_fee_cents, display_order, created_at, updated_at
                ) VALUES (5, ?, 3, NULL, 1, 'Migration Test Platform', 'CROSS-INVOICE',
                          1, 99, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (issued_source_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="invoice_financial_header_locked"):
            connection.execute("UPDATE invoices SET amount_cents = 4999 WHERE id = 3")
        with pytest.raises(sqlite3.IntegrityError, match="invoice_issue_metadata_invalid"):
            connection.execute(
                "UPDATE invoices SET invoice_number = 'REWRITTEN-ISSUED-NUMBER' WHERE id = 3"
            )
        with pytest.raises(sqlite3.IntegrityError, match="invoice_issue_metadata_invalid"):
            connection.execute("UPDATE invoices SET due_date = '2027-01-01' WHERE id = 3")
        with pytest.raises(
            sqlite3.IntegrityError,
            match="invoice_(lifecycle_transition|issue_metadata)_invalid",
        ):
            connection.execute("UPDATE invoices SET lifecycle_status = 'DRAFT' WHERE id = 3")
        with pytest.raises(sqlite3.IntegrityError, match="invoice_lifecycle_transition_invalid"):
            connection.execute("UPDATE invoices SET lifecycle_status = 'ISSUING' WHERE id = 3")
        with pytest.raises(
            sqlite3.IntegrityError,
            match="invoice_(lifecycle_transition|issue_metadata)_invalid",
        ):
            connection.execute("UPDATE invoices SET lifecycle_status = 'DRAFT' WHERE id = 4")
        with pytest.raises(sqlite3.IntegrityError, match="invoice_lifecycle_transition_invalid"):
            connection.execute("UPDATE invoices SET lifecycle_status = 'ISSUED' WHERE id = 4")
        with pytest.raises(sqlite3.IntegrityError, match="invoice_issue_metadata_invalid"):
            connection.execute("UPDATE invoices SET invoice_number = 'DRAFT-NUMBER' WHERE id = 5")
        with pytest.raises(sqlite3.IntegrityError, match="invoice_initial_state_invalid"):
            connection.execute(
                """
                INSERT INTO invoices (
                    settlement_id, client_id, year, quarter, fee_plan_id, company_id, fc_id,
                    invoice_number, lifecycle_status, issue_date, due_date,
                    amount_cents, language, created_at, updated_at
                ) VALUES (1, 2, 2028, 1, 1, 1, 1, 'INVALID-DIRECT-ISSUE', 'ISSUED',
                          '2028-04-01', '2028-04-15', 3000, 'zh',
                          CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="settlement_has_active_invoice"):
            connection.execute("UPDATE quarterly_settlements SET status = 'VOID' WHERE id = 4")

        # A second active source for the same Settlement is rejected independently.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO invoice_sources (
                    invoice_id, settlement_id, locked_amount_cents, active, created_at, updated_at
                ) VALUES (5, 1, 3000, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )

        # Removing the cross-source permits the old VOID Invoice's Settlement to void.
        connection.execute("DELETE FROM invoice_lines WHERE id = ?", (line_id,))
        connection.execute("DELETE FROM invoice_sources WHERE id = ?", (source_id,))
        connection.execute("UPDATE quarterly_settlements SET status = 'VOID' WHERE id = 4")
        with pytest.raises(sqlite3.IntegrityError, match="invoice_source_missing"):
            connection.execute(
                """
                UPDATE invoices
                SET invoice_number = 'MTC-MTF-202402-011',
                    issue_date = '2027-04-01', due_date = '2027-04-15',
                    lifecycle_status = 'ISSUING'
                WHERE id = 5
                """
            )

        # Voiding an Invoice atomically retires its active Settlement sources.
        connection.execute("UPDATE invoices SET lifecycle_status = 'ISSUED' WHERE id = 1")
        connection.execute("UPDATE invoices SET lifecycle_status = 'VOID' WHERE id = 1")
        active = connection.execute(
            "SELECT active FROM invoice_sources WHERE invoice_id = 1"
        ).fetchone()[0]
        assert active == 0
        connection.commit()


def test_payment_triggers_serialize_overpayment_and_void_races(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, "payment-trigger-races", monkeypatch)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, PREVIOUS_REVISION)
    _seed_0_2_8_invoice_states(settings.database_path)
    command.upgrade(alembic_config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("UPDATE invoices SET lifecycle_status = 'ISSUED' WHERE id = 2")
        with pytest.raises(sqlite3.IntegrityError, match="payment_amount_invalid"):
            connection.execute(
                """
                INSERT INTO payments (
                    invoice_id, payment_date, amount_cents, method, created_at, updated_at
                ) VALUES (2, '2026-04-10', 0, 'BANK', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="payment_invoice_not_issued"):
            connection.execute(
                """
                INSERT INTO payments (
                    invoice_id, payment_date, amount_cents, method, created_at, updated_at
                ) VALUES (4, '2026-04-10', 100, 'BANK', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        connection.commit()

    def run_waiting_statement(sql: str, started: Event, result: Queue[object]) -> None:
        try:
            with sqlite3.connect(settings.database_path, timeout=5) as waiting_connection:
                waiting_connection.execute("PRAGMA busy_timeout=5000")
                started.set()
                waiting_connection.execute(sql)
                waiting_connection.commit()
            result.put("committed")
        except Exception as exc:  # noqa: BLE001 - the exact SQLite result is asserted below
            result.put(exc)

    # Payment obtains the SQLite writer lock first. The waiting VOID sees that
    # committed Payment and must fail instead of producing VOID + Payment.
    first_connection = sqlite3.connect(settings.database_path, timeout=5)
    first_connection.execute("PRAGMA busy_timeout=5000")
    first_connection.execute("BEGIN IMMEDIATE")
    first_connection.execute(
        """
        INSERT INTO payments (
            invoice_id, payment_date, amount_cents, method, created_at, updated_at
        ) VALUES (2, '2026-04-11', 1000, 'BANK', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )
    void_started = Event()
    void_result: Queue[object] = Queue()
    void_thread = Thread(
        target=run_waiting_statement,
        args=("UPDATE invoices SET lifecycle_status = 'VOID' WHERE id = 2", void_started, void_result),
    )
    void_thread.start()
    assert void_started.wait(timeout=5)
    first_connection.commit()
    first_connection.close()
    void_thread.join(timeout=10)
    assert not void_thread.is_alive()
    void_outcome = void_result.get_nowait()
    assert isinstance(void_outcome, sqlite3.IntegrityError)
    assert "invoice_has_payments" in str(void_outcome)

    # Two otherwise-valid payments serialize. The second rechecks the newly
    # committed total inside its trigger and cannot overpay the Invoice.
    first_connection = sqlite3.connect(settings.database_path, timeout=5)
    first_connection.execute("PRAGMA busy_timeout=5000")
    first_connection.execute("BEGIN IMMEDIATE")
    first_connection.execute(
        """
        INSERT INTO payments (
            invoice_id, payment_date, amount_cents, method, created_at, updated_at
        ) VALUES (2, '2026-04-12', 2000, 'BANK', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """
    )
    payment_started = Event()
    payment_result: Queue[object] = Queue()
    payment_thread = Thread(
        target=run_waiting_statement,
        args=(
            "INSERT INTO payments (invoice_id, payment_date, amount_cents, method, created_at, updated_at) "
            "VALUES (2, '2026-04-13', 2000, 'BANK', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            payment_started,
            payment_result,
        ),
    )
    payment_thread.start()
    assert payment_started.wait(timeout=5)
    first_connection.commit()
    first_connection.close()
    payment_thread.join(timeout=10)
    assert not payment_thread.is_alive()
    payment_outcome = payment_result.get_nowait()
    assert isinstance(payment_outcome, sqlite3.IntegrityError)
    assert "payment_amount_exceeds_invoice" in str(payment_outcome)

    with sqlite3.connect(settings.database_path) as connection:
        total_paid = connection.execute(
            "SELECT SUM(amount_cents) FROM payments WHERE invoice_id = 2"
        ).fetchone()[0]
        lifecycle_status = connection.execute(
            "SELECT lifecycle_status FROM invoices WHERE id = 2"
        ).fetchone()[0]
    assert total_paid == 3000
    assert lifecycle_status == "ISSUED"


def test_unstamped_partial_invoice_columns_are_migrated_not_stamped_as_head(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path, "partial-columns", monkeypatch)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, PREVIOUS_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("ALTER TABLE invoices ADD COLUMN client_id INTEGER")
        connection.execute("DROP TABLE alembic_version")
        connection.commit()

    legacy_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(database_module, "settings", settings)

    database_module.init_db()

    inspector = inspect(legacy_engine)
    invoice_columns = {column["name"]: column for column in inspector.get_columns("invoices")}
    assert all(
        invoice_columns[column_name]["nullable"] is False
        for column_name in ("client_id", "year", "quarter", "fee_plan_id")
    )
    with legacy_engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        trigger_sql = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'trigger' AND name = 'trg_settlement_validate_void'"
            )
        ).scalar_one()
    assert revision == HEAD_REVISION
    assert "invoice_sources" in trigger_sql
    legacy_engine.dispose()


def test_unstamped_incomplete_invoice_table_is_not_stamped_as_head(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path, "partial-table", monkeypatch)
    alembic_config = _alembic_config(settings)
    command.upgrade(alembic_config, PREVIOUS_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "CREATE TABLE invoice_sources (id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL)"
        )
        connection.execute("DROP TABLE alembic_version")
        connection.commit()

    legacy_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(database_module, "settings", settings)

    with pytest.raises(RuntimeError, match="invoice_sources.*不完整"):
        database_module.init_db()

    with legacy_engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
    assert revision != HEAD_REVISION
    legacy_engine.dispose()
