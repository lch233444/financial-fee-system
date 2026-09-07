from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

import app.config as config_module
import app.database as database_module
from app import models as _models  # noqa: F401 - register all metadata tables
from app.config import Settings


HEAD_REVISION = "e8b2c6d91a04"


def test_e91_database_keeps_legacy_settlement_values_when_upgraded(tmp_path, monkeypatch) -> None:
    settings = Settings(data_root=tmp_path / "e91-data")
    settings.ensure_directories()
    database_path = settings.database_path
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_config, "e91f7c6a2b40")

    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            INSERT INTO companies
                (id, name, code, payment_terms_days, active, created_at, updated_at)
            VALUES (1, 'Legacy Company', 'LEG', 14, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO fcs
                (id, company_id, name, code, active, created_at, updated_at)
            VALUES (1, 1, 'Legacy FC', 'LFC', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO platforms
                (id, name, code, active, created_at, updated_at)
            VALUES (1, 'Legacy Platform', 'LP', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO fee_plans
                (id, company_id, name, code, fee_rate_bps, calculation_method, active, created_at, updated_at)
            VALUES (1, 1, 'Legacy Plan', 'L20', 2000, 'HWM', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO clients
                (id, company_id, fc_id, name, status, created_at, updated_at)
            VALUES (1, 1, 1, 'Legacy Client', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO sub_accounts
                (id, client_id, platform_id, fee_plan_id, account_number, currency, status, created_at, updated_at)
            VALUES (1, 1, 1, 1, 'LEG-001', 'HKD', 'ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO quarterly_settlements
                (id, client_id, platform_id, fee_plan_id, company_id, fc_id, year, quarter,
                 start_date, closing_date, days, beginning_cents, contribution_cents,
                 withdrawal_cents, net_contribution_cents, closing_cents, gain_loss_cents,
                 period_rate_ppm, original_hwm_cents, adjusted_hwm_cents,
                 watermark_difference_cents, chargeable_above_hwm_cents, service_fee_cents,
                 next_hwm_cents, fee_rate_bps, formula_version, status, finalized_at,
                 created_at, updated_at)
            VALUES
                (1, 1, 1, 1, 1, 1, 2025, 4, '2025-10-01', '2025-12-31', 92,
                 100000, 0, 0, 0, 110000, 10000, 100000, 100000, 100000,
                 10000, 10000, 2000, 110000, 2000, 'HWM-1.0', 'FINALIZED',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO settlement_account_lines
                (id, settlement_id, account_id, beginning_cents, closing_cents, created_at, updated_at)
            VALUES (1, 1, 1, 100000, 110000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            """
        )
        connection.commit()

    command.upgrade(alembic_config, "head")
    with sqlite3.connect(database_path) as connection:
        settlement = connection.execute(
            "SELECT calculation_mode, service_fee_cents, next_hwm_cents FROM quarterly_settlements WHERE id = 1"
        ).fetchone()
        line = connection.execute(
            "SELECT beginning_cents, closing_cents, beginning_snapshot_id, service_fee_cents, "
            "start_date, closing_date, days FROM settlement_account_lines WHERE id = 1"
        ).fetchone()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert settlement == ("LEGACY_GROUP_HWM", 2000, 110000)
    assert line == (100000, 110000, None, None, "2025-10-01", "2025-12-31", 92)
    assert revision == HEAD_REVISION


def test_legacy_unstamped_database_gains_ai_columns_without_losing_records(
    tmp_path, monkeypatch
) -> None:
    data_root = tmp_path / "legacy-data"
    database_path = data_root / "database" / "financial_system.sqlite3"
    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE statement_imports (
                id INTEGER PRIMARY KEY,
                original_name VARCHAR(255) NOT NULL,
                stored_path VARCHAR(600) NOT NULL,
                sha256 VARCHAR(64) NOT NULL UNIQUE,
                mime_type VARCHAR(120) NOT NULL,
                parser_name VARCHAR(80) NOT NULL DEFAULT 'EMPF_ACCOUNT_PAGE',
                parser_version VARCHAR(30) NOT NULL DEFAULT '1.1',
                status VARCHAR(30) NOT NULL DEFAULT 'NEEDS_REVIEW',
                raw_text TEXT,
                extracted_json JSON,
                reviewed_json JSON,
                revision_log_json JSON,
                confidence_json JSON,
                warnings_json JSON,
                duplicate_of_id INTEGER,
                confirmed_account_id INTEGER,
                confirmed_snapshot_id INTEGER,
                confirmed_at DATETIME,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            INSERT INTO statement_imports
                (id, original_name, stored_path, sha256, mime_type, parser_name,
                 parser_version, status, extracted_json)
            VALUES
                (7, 'legacy.jpg', 'legacy.jpg', ?, 'image/jpeg',
                 'EMPF_ACCOUNT_PAGE', '1.1', 'NEEDS_REVIEW', ?)
            """,
            ("a" * 64, '{"account_number":"24681357"}'),
        )
        connection.commit()

    settings = Settings(data_root=data_root)
    settings.ensure_directories()
    legacy_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(database_module, "settings", settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)

    database_module.init_db()

    columns = {column["name"] for column in inspect(legacy_engine).get_columns("statement_imports")}
    assert {
        "ai_recognition_json",
        "ai_status",
        "ai_model",
        "ai_recognized_at",
    }.issubset(columns)
    with legacy_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, original_name, extracted_json, ai_recognition_json "
                "FROM statement_imports WHERE id = 7"
            )
        ).one()
        assert row.id == 7
        assert row.original_name == "legacy.jpg"
        assert "24681357" in row.extracted_json
        assert row.ai_recognition_json is None
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert revision == HEAD_REVISION

    settlement_columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("quarterly_settlements")
    }
    assert {"company_id", "fc_id", "previous_settlement_id"}.issubset(settlement_columns)
    assert "calculation_mode" in settlement_columns
    line_columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("settlement_account_lines")
    }
    assert {"previous_line_id", "beginning_snapshot_id", "service_fee_cents", "next_hwm_cents", "start_date", "closing_date", "days"}.issubset(
        line_columns
    )
    invoice_columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("invoices")
    }
    assert {"client_id", "year", "quarter", "fee_plan_id"}.issubset(invoice_columns)
    assert {
        "invoice_sources",
        "invoice_lines",
        "invoice_issue_attempts",
    }.issubset(inspect(legacy_engine).get_table_names())
    with legacy_engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert "trg_transactions_block_finalized_period" in triggers
    assert "trg_settlement_validate_finalize" in triggers
    assert "trg_settlement_account_line_order" in triggers
    legacy_engine.dispose()


def test_unstamped_partial_0_2_14_shape_stops_before_false_stamp(
    tmp_path, monkeypatch
) -> None:
    data_root = tmp_path / "current-shape-data"
    settings = Settings(data_root=data_root)
    settings.ensure_directories()
    legacy_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    database_module.Base.metadata.create_all(bind=legacy_engine)
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(database_module, "settings", settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)

    with pytest.raises(RuntimeError, match="不完整的Settlement版本"):
        database_module.init_db()

    with legacy_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'alembic_version'")
        ).scalar_one() == 0
    legacy_engine.dispose()


def test_unstamped_complete_9d_shape_runs_0_2_14_migration_instead_of_false_head_stamp(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=tmp_path / "unstamped-9d-data")
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_config, "9d2f6a8c4b13")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("DROP TABLE alembic_version")
        connection.commit()

    legacy_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(database_module, "settings", settings)

    database_module.init_db()

    with legacy_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            HEAD_REVISION
        )
        assert connection.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'")
        ).scalar_one() == 57
        settlement_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(quarterly_settlements)"
            ).fetchall()
        }
        assert {"version_no", "replaces_settlement_id"}.issubset(settlement_columns)
    legacy_engine.dispose()


def test_unstamped_complete_0_2_14_shape_runs_delete_guard_migration(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=tmp_path / "unstamped-0-2-14-data")
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_config, "7f3c2a91b6e4")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'"
        ).fetchone() == (53,)
        connection.execute("DROP TABLE alembic_version")
        connection.commit()

    legacy_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(database_module, "settings", settings)

    database_module.init_db()

    with legacy_engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            HEAD_REVISION
        )
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            )
        }
    assert len(triggers) == 57
    assert {
        "trg_client_delete_no_cascade",
        "trg_account_delete_no_cascade",
        "trg_statement_import_delete_no_snapshot",
        "trg_snapshot_delete_no_confirmed_import",
    }.issubset(triggers)
    legacy_engine.dispose()


def test_unstamped_partial_0_2_15_trigger_shape_stops_before_false_stamp(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=tmp_path / "unstamped-partial-0-2-15-data")
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_config, "7f3c2a91b6e4")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER trg_client_delete_no_cascade
            BEFORE DELETE ON clients
            BEGIN
                SELECT RAISE(ABORT, 'half_migrated_delete_guard');
            END
            """
        )
        connection.execute("DROP TABLE alembic_version")
        connection.commit()

    legacy_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", legacy_engine)
    monkeypatch.setattr(database_module, "settings", settings)

    with pytest.raises(RuntimeError, match="不完整的Settlement版本"):
        database_module.init_db()

    with legacy_engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE name = 'alembic_version'")
        ).scalar_one() == 0
        assert connection.execute(
            text("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'")
        ).scalar_one() == 54
    legacy_engine.dispose()
