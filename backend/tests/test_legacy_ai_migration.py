from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, inspect, text

import app.config as config_module
import app.database as database_module
from app import models as _models  # noqa: F401 - register all metadata tables
from app.config import Settings


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
        assert revision == "e91f7c6a2b40"

    settlement_columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("quarterly_settlements")
    }
    assert {"company_id", "fc_id", "previous_settlement_id"}.issubset(settlement_columns)
    with legacy_engine.connect() as connection:
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert "trg_transactions_block_finalized_period" in triggers
    assert "trg_settlement_validate_finalize" in triggers
    legacy_engine.dispose()


def test_unstamped_current_shape_database_gains_missing_financial_triggers(
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

    database_module.init_db()

    with legacy_engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            )
        }
    assert revision == "e91f7c6a2b40"
    assert {
        "trg_transactions_block_finalized_period",
        "trg_settlement_block_out_of_order_insert",
        "trg_settlement_validate_finalize",
        "trg_settlement_validate_void",
    }.issubset(triggers)
    legacy_engine.dispose()
