from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine

import app.config as config_module
import app.database as database_module
from app.config import Settings
from app.services.backup import _validate_sqlite_database
from app.services.settlement_boundary_contract import (
    settlement_boundary_trigger_sql_is_current,
)


PREVIOUS_REVISION = "7f3c2a91b6e4"
DELETE_GUARD_REVISION = "c1a7d5e9b402"
HEAD_REVISION = "b7e2d9a41c60"
DELETE_GUARD_TRIGGERS = {
    "trg_client_delete_no_cascade",
    "trg_account_delete_no_cascade",
    "trg_statement_import_delete_no_snapshot",
    "trg_snapshot_delete_no_confirmed_import",
}
ID_HIGH_WATER_KEYS = {
    "id_high_water.clients",
    "id_high_water.sub_accounts",
    "id_high_water.statement_imports",
    "id_high_water.balance_snapshots",
}


def _settings(tmp_path: Path, name: str, monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(data_root=tmp_path / name)
    settings.ensure_directories()
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    return settings


def _alembic_config(settings: Settings) -> Config:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def _trigger_sql(database_path: Path) -> dict[str, str]:
    with sqlite3.connect(database_path) as connection:
        return {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            ).fetchall()
        }


def test_7f_head_upgrades_to_delete_guard_head_with_exact_trigger_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "upgrade", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS_REVISION)

    previous_triggers = _trigger_sql(settings.database_path)
    assert len(previous_triggers) == 53
    assert not (set(previous_triggers) & DELETE_GUARD_TRIGGERS)

    command.upgrade(config, "head")

    triggers = _trigger_sql(settings.database_path)
    assert set(triggers) == set(previous_triggers) | DELETE_GUARD_TRIGGERS
    assert len(triggers) == 57
    assert all(sql.strip() for sql in triggers.values())
    assert settlement_boundary_trigger_sql_is_current(triggers)
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert dict(
            connection.execute(
                "SELECT key, value FROM app_settings WHERE key LIKE 'id_high_water.%'"
            ).fetchall()
        ) == {key: "0" for key in ID_HIGH_WATER_KEYS}


def test_unstamped_complete_0_2_15_shape_runs_quarter_boundary_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "unstamped-0-2-15", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, DELETE_GUARD_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        legacy_triggers = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert not settlement_boundary_trigger_sql_is_current(legacy_triggers)
        connection.execute("DROP TABLE alembic_version")
        connection.commit()

    unstamped_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", unstamped_engine)
    monkeypatch.setattr(database_module, "settings", settings)
    database_module.init_db()

    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )
        upgraded_triggers = {
            str(name): str(sql or "")
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        assert settlement_boundary_trigger_sql_is_current(upgraded_triggers)
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    unstamped_engine.dispose()


def test_delete_guard_migration_seeds_id_high_water_from_live_rows_and_deletion_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "id-high-water-seed", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            INSERT INTO clients (id, name, status, created_at, updated_at)
            VALUES (10, 'Seed Client', 'DRAFT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO sub_accounts
                (id, client_id, account_number, currency, status, created_at, updated_at)
            VALUES (20, 10, 'SEED-20', 'HKD', 'DRAFT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO statement_imports
                (id, original_name, stored_path, sha256, mime_type, parser_name,
                 parser_version, status, created_at, updated_at)
            VALUES
                (30, 'seed.jpg', 'seed.jpg',
                 '3030303030303030303030303030303030303030303030303030303030303030',
                 'image/jpeg', 'TEST', '1.1', 'NEEDS_REVIEW',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO balance_snapshots
                (id, account_id, as_of_date, total_balance_cents, currency,
                 source_type, eligible_for_closing, created_at, updated_at)
            VALUES
                (40, 20, '2026-09-30', 100000, 'HKD', 'MANUAL', 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            """
        )
        audits = (
            ("MASTER_DATA_DELETED", None, {"deleted_entity_type": "CLIENT", "deleted_entity_id": 110}),
            ("MASTER_DATA_DELETED", None, {"deleted_entity_type": "SUB_ACCOUNT", "deleted_entity_id": 120}),
            ("STATEMENT_IMPORT_DELETED", 130, {"sha256": "legacy"}),
            (
                "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED",
                None,
                {"deleted_import_id": 140, "deleted_snapshot_id": 150},
            ),
        )
        connection.executemany(
            """
            INSERT INTO audit_events
                (action, entity_type, entity_id, details_json, created_at)
            VALUES (?, 'TEST', ?, ?, CURRENT_TIMESTAMP)
            """,
            [(action, entity_id, json.dumps(details)) for action, entity_id, details in audits],
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        values = dict(
            connection.execute(
                "SELECT key, value FROM app_settings WHERE key LIKE 'id_high_water.%'"
            ).fetchall()
        )
        assert values == {
            "id_high_water.clients": "110",
            "id_high_water.sub_accounts": "120",
            "id_high_water.statement_imports": "140",
            "id_high_water.balance_snapshots": "150",
        }


def test_delete_guard_migration_rejects_conflicting_statement_deletion_audit_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "id-high-water-conflict", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO audit_events
                (action, entity_type, entity_id, details_json, created_at)
            VALUES (
                'STATEMENT_IMPORT_DELETED', 'STATEMENT_IMPORT', 8, ?, CURRENT_TIMESTAMP
            )
            """,
            (json.dumps({"deleted_import_id": 9}),),
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="原ID冲突"):
        command.upgrade(config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            PREVIOUS_REVISION,
        )
        assert not (set(_trigger_sql(settings.database_path)) & DELETE_GUARD_TRIGGERS)


def test_delete_guard_migration_rejects_live_id_reused_from_deletion_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "id-reuse-migration", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO statement_imports
                (id, original_name, stored_path, sha256, mime_type, parser_name,
                 parser_version, status, created_at, updated_at)
            VALUES
                (77, 'reused.jpg', 'reused.jpg',
                 '7777777777777777777777777777777777777777777777777777777777777777',
                 'image/jpeg', 'TEST', '1.1', 'NEEDS_REVIEW',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (action, entity_type, entity_id, details_json, created_at)
            VALUES
                ('STATEMENT_IMPORT_DELETED', 'STATEMENT_IMPORT', 77, ?, CURRENT_TIMESTAMP)
            """,
            (json.dumps({"legacy": True}),),
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="现存记录ID与历史删除审计重复"):
        command.upgrade(config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            PREVIOUS_REVISION,
        )


def test_delete_guard_migration_disambiguates_proven_legacy_statement_id_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "proven-legacy-id-reuse", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO statement_imports
                (id, original_name, stored_path, sha256, mime_type, parser_name,
                 parser_version, status, created_at, updated_at)
            VALUES
                (77, 'reused.jpg', 'reused.jpg',
                 '7777777777777777777777777777777777777777777777777777777777777777',
                 'image/jpeg', 'TEST', '1.1', 'NEEDS_REVIEW',
                 '2026-09-01 12:00:00', '2026-09-01 12:00:00')
            """
        )
        cursor = connection.execute(
            """
            INSERT INTO audit_events
                (action, entity_type, entity_id, details_json, created_at)
            VALUES
                ('STATEMENT_IMPORT_DELETED', 'STATEMENT_IMPORT', 77, ?,
                 '2026-08-01 12:00:00')
            """,
            (json.dumps({"legacy": True, "sha256": "historical-proof"}),),
        )
        historical_audit_id = int(cursor.lastrowid)
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )
        entity_id, raw_details = connection.execute(
            "SELECT entity_id, details_json FROM audit_events WHERE id = ?",
            (historical_audit_id,),
        ).fetchone()
        details = json.loads(raw_details)
        assert entity_id is None
        assert details["legacy"] is True
        assert details["sha256"] == "historical-proof"
        assert details["deleted_import_id"] == 77
        assert details["legacy_entity_id_reused_before_0_2_15"] is True
        assert details["legacy_reuse_disambiguation_revision"] == DELETE_GUARD_REVISION

        correction_id = details["legacy_reuse_correction_audit_id"]
        action, entity_type, correction_entity_id, raw_correction = connection.execute(
            """
            SELECT action, entity_type, entity_id, details_json
            FROM audit_events WHERE id = ?
            """,
            (correction_id,),
        ).fetchone()
        correction = json.loads(raw_correction)
        assert action == "LEGACY_STATEMENT_DELETE_ID_REUSE_DISAMBIGUATED"
        assert entity_type == "AUDIT_EVENT"
        assert correction_entity_id == historical_audit_id
        assert correction["historical_deletion_audit_id"] == historical_audit_id
        assert correction["reused_statement_import_id"] == 77
        assert correction["proof"] == "historical_delete_created_before_reused_statement"
        assert correction["original_audit_entity_id_cleared"] is True
        assert connection.execute(
            "SELECT value FROM app_settings WHERE key = 'id_high_water.statement_imports'"
        ).fetchone() == ("77",)

    _validate_sqlite_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE audit_events SET action = 'BROKEN_CORRECTION' WHERE id = ?",
            (correction_id,),
        )
        connection.commit()
    with pytest.raises(ValueError, match="历史ID消歧证据异常"):
        _validate_sqlite_database(settings.database_path)


def test_delete_guard_triggers_block_direct_cascade_and_allow_ordered_leaf_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "direct-delete", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")

    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            INSERT INTO clients
                (id, name, status, created_at, updated_at)
            VALUES
                (1, 'Synthetic Client', 'DRAFT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

            INSERT INTO sub_accounts
                (id, client_id, account_number, currency, status, created_at, updated_at)
            VALUES
                (1, 1, 'SYNTHETIC-001', 'HKD', 'DRAFT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

            INSERT INTO statement_imports
                (id, original_name, stored_path, sha256, mime_type, parser_name,
                 parser_version, status, confirmed_account_id, created_at, updated_at)
            VALUES
                (1, 'synthetic.pdf', 'synthetic.pdf',
                 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                 'application/pdf', 'LOCAL_OCR_EMPF_ACCOUNT_PAGE', '1.1',
                 'CONFIRMED', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

            INSERT INTO balance_snapshots
                (id, account_id, as_of_date, total_balance_cents, currency,
                 source_type, statement_import_id, eligible_for_closing,
                 created_at, updated_at)
            VALUES
                (1, 1, '2026-09-30', 100000, 'HKD', 'STATEMENT_IMPORT', 1, 1,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);

            UPDATE statement_imports SET confirmed_snapshot_id = 1 WHERE id = 1;
            """
        )

        for sql, marker in (
            ("DELETE FROM clients WHERE id = 1", "client_delete_would_cascade"),
            ("DELETE FROM sub_accounts WHERE id = 1", "account_delete_would_cascade"),
            (
                "DELETE FROM statement_imports WHERE id = 1",
                "statement_import_delete_has_snapshot",
            ),
            (
                "DELETE FROM balance_snapshots WHERE id = 1",
                "snapshot_delete_has_confirmed_import",
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=marker):
                connection.execute(sql)

        connection.execute(
            "UPDATE statement_imports SET confirmed_snapshot_id = NULL WHERE id = 1"
        )
        connection.execute("DELETE FROM balance_snapshots WHERE id = 1")
        connection.execute("DELETE FROM statement_imports WHERE id = 1")
        connection.execute("DELETE FROM sub_accounts WHERE id = 1")
        connection.execute("DELETE FROM clients WHERE id = 1")
        connection.commit()

        assert connection.execute("SELECT COUNT(*) FROM clients").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM sub_accounts").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM statement_imports").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM balance_snapshots").fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_delete_guards_block_normalized_logical_references_and_duplicate_dependents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "logical-delete-guards", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")

    with sqlite3.connect(settings.database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            INSERT INTO clients
                (id, name, status, created_at, updated_at)
            VALUES
                (20, 'Logical Guard Client', 'DRAFT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO sub_accounts
                (id, client_id, account_number, currency, status, created_at, updated_at)
            VALUES
                (20, 20, 'LOGICAL-GUARD-20', 'HKD', 'DRAFT', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO statement_imports
                (id, original_name, stored_path, sha256, mime_type, parser_name,
                 parser_version, status, created_at, updated_at)
            VALUES
                (30, 'parent.jpg', 'parent.jpg',
                 '3030303030303030303030303030303030303030303030303030303030303030',
                 'image/jpeg', 'TEST', '1.1', 'NEEDS_REVIEW', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (31, 'dependent.jpg', 'dependent.jpg',
                 '3131313131313131313131313131313131313131313131313131313131313131',
                 'image/jpeg', 'TEST', '1.1', 'NEEDS_REVIEW', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            UPDATE statement_imports SET duplicate_of_id = 30 WHERE id = 31;
            INSERT INTO balance_snapshots
                (id, account_id, as_of_date, total_balance_cents, currency,
                 source_type, eligible_for_closing, created_at, updated_at)
            VALUES
                (40, 20, '2026-09-30', 100000, 'HKD', 'MANUAL', 0,
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO attachments
                (id, entity_type, entity_id, original_name, stored_path, sha256,
                 mime_type, size_bytes, created_at, updated_at)
            VALUES
                (20, ' client ', 20, 'client.pdf', 'F:/synthetic/logical-client.pdf',
                 '2020202020202020202020202020202020202020202020202020202020202020',
                 'application/pdf', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (21, 'snapshot', 40, 'snapshot.pdf', 'F:/synthetic/logical-snapshot.pdf',
                 '2121212121212121212121212121212121212121212121212121212121212121',
                 'application/pdf', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                (22, 'statement import', 30, 'statement.pdf', 'F:/synthetic/logical-statement.pdf',
                 '2222222222222222222222222222222222222222222222222222222222222222',
                 'application/pdf', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            INSERT INTO export_records
                (id, export_type, entity_type, entity_id, stored_path, sha256,
                 created_at, updated_at)
            VALUES
                (20, 'TEST', 'sub-account', 20, 'F:/synthetic/logical-account.xlsx',
                 '2323232323232323232323232323232323232323232323232323232323232323',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
            """
        )

        for sql, marker in (
            ("DELETE FROM clients WHERE id = 20", "client_delete_would_cascade"),
            ("DELETE FROM sub_accounts WHERE id = 20", "account_delete_would_cascade"),
            ("DELETE FROM statement_imports WHERE id = 30", "statement_import_delete_has_snapshot"),
            ("DELETE FROM balance_snapshots WHERE id = 40", "snapshot_delete_has_confirmed_import"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=marker):
                connection.execute(sql)

        connection.execute("DELETE FROM statement_imports WHERE id = 31")
        with pytest.raises(sqlite3.IntegrityError, match="statement_import_delete_has_snapshot"):
            connection.execute("DELETE FROM statement_imports WHERE id = 30")
        connection.execute("DELETE FROM attachments WHERE id = 22")
        connection.execute("DELETE FROM statement_imports WHERE id = 30")

        connection.execute("DELETE FROM attachments WHERE id = 21")
        connection.execute("DELETE FROM balance_snapshots WHERE id = 40")
        connection.execute("DELETE FROM export_records WHERE id = 20")
        connection.execute("DELETE FROM sub_accounts WHERE id = 20")
        connection.execute("DELETE FROM attachments WHERE id = 20")
        connection.execute("DELETE FROM clients WHERE id = 20")
        connection.commit()

        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("damage", ["missing-old-trigger", "half-new-trigger"])
def test_upgrade_rejects_nonexact_7f_trigger_set_before_creating_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    settings = _settings(tmp_path, f"damaged-{damage}", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, PREVIOUS_REVISION)
    with sqlite3.connect(settings.database_path) as connection:
        if damage == "missing-old-trigger":
            connection.execute("DROP TRIGGER trg_payment_refund_validate_insert")
        else:
            connection.execute(
                """
                CREATE TRIGGER trg_client_delete_no_cascade
                BEFORE DELETE ON clients
                BEGIN
                    SELECT RAISE(ABORT, 'half_migrated_delete_guard');
                END
                """
            )
        connection.commit()
    before = _trigger_sql(settings.database_path)

    with pytest.raises(RuntimeError, match="完整53-Trigger"):
        command.upgrade(config, "head")

    assert _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            PREVIOUS_REVISION,
        )


def test_delete_guard_migration_refuses_in_place_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "downgrade", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "head")
    before = _trigger_sql(settings.database_path)

    with pytest.raises(RuntimeError, match="原地降级"):
        command.downgrade(config, PREVIOUS_REVISION)

    assert _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            HEAD_REVISION,
        )


def test_existing_head_startup_rejects_missing_id_high_water_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "startup-missing-high-water", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "DELETE FROM app_settings WHERE key = 'id_high_water.statement_imports'"
        )
        connection.commit()
    startup_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", startup_engine)
    monkeypatch.setattr(database_module, "settings", settings)
    try:
        with pytest.raises(RuntimeError, match="ID高水位完整性"):
            database_module.init_db()
    finally:
        startup_engine.dispose()


def test_existing_head_startup_rejects_same_name_weak_delete_guard_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "startup-weak-trigger", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript(
            """
            DROP TRIGGER trg_statement_import_delete_no_snapshot;
            CREATE TRIGGER trg_statement_import_delete_no_snapshot
            BEFORE DELETE ON statement_imports
            BEGIN
                SELECT RAISE(ABORT, 'statement_import_delete_has_snapshot');
            END;
            """
        )
        connection.commit()
    startup_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", startup_engine)
    monkeypatch.setattr(database_module, "settings", settings)
    try:
        with pytest.raises(RuntimeError, match="Trigger语义不完整"):
            database_module.init_db()
    finally:
        startup_engine.dispose()


def test_existing_head_startup_rejects_live_id_reused_from_deletion_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "startup-id-reuse", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO statement_imports
                (id, original_name, stored_path, sha256, mime_type, parser_name,
                 parser_version, status, created_at, updated_at)
            VALUES
                (88, 'reused.jpg', 'reused.jpg',
                 '8888888888888888888888888888888888888888888888888888888888888888',
                 'image/jpeg', 'TEST', '1.1', 'NEEDS_REVIEW',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (action, entity_type, entity_id, details_json, created_at)
            VALUES
                ('STATEMENT_IMPORT_DELETED', 'STATEMENT_IMPORT', 88, ?, CURRENT_TIMESTAMP)
            """,
            (json.dumps({"legacy": True}),),
        )
        connection.execute(
            "UPDATE app_settings SET value = '88' "
            "WHERE key = 'id_high_water.statement_imports'"
        )
        connection.commit()
    startup_engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database_module, "engine", startup_engine)
    monkeypatch.setattr(database_module, "settings", settings)
    try:
        with pytest.raises(RuntimeError, match="ID高水位完整性"):
            database_module.init_db()
    finally:
        startup_engine.dispose()


@pytest.mark.parametrize("damage", ["missing", "noncanonical", "below-live"])
def test_backup_validation_rejects_invalid_current_head_high_water(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    settings = _settings(tmp_path, f"backup-high-water-{damage}", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")
    with sqlite3.connect(settings.database_path) as connection:
        if damage == "missing":
            connection.execute(
                "DELETE FROM app_settings WHERE key = 'id_high_water.clients'"
            )
        elif damage == "noncanonical":
            connection.execute(
                "UPDATE app_settings SET value = '01' "
                "WHERE key = 'id_high_water.clients'"
            )
        else:
            connection.execute(
                """
                INSERT INTO clients (id, name, status, created_at, updated_at)
                VALUES (9, 'Synthetic Backup Client', 'DRAFT',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
            connection.execute(
                "UPDATE app_settings SET value = '8' "
                "WHERE key = 'id_high_water.clients'"
            )
        connection.commit()

    with pytest.raises(ValueError, match="ID高水位"):
        _validate_sqlite_database(settings.database_path)


def test_backup_validation_rejects_live_id_reused_from_deletion_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "backup-id-reuse", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            """
            INSERT INTO clients (id, name, status, created_at, updated_at)
            VALUES (99, 'Synthetic Reused Client', 'DRAFT',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
        connection.execute(
            """
            INSERT INTO audit_events
                (action, entity_type, entity_id, details_json, created_at)
            VALUES ('MASTER_DATA_DELETED', 'MASTER_DATA', NULL, ?, CURRENT_TIMESTAMP)
            """,
            (
                json.dumps(
                    {"deleted_entity_type": "CLIENT", "deleted_entity_id": 99}
                ),
            ),
        )
        connection.execute(
            "UPDATE app_settings SET value = '99' WHERE key = 'id_high_water.clients'"
        )
        connection.commit()

    with pytest.raises(ValueError, match="现存记录ID与历史删除审计重复"):
        _validate_sqlite_database(settings.database_path)


def test_backup_validation_rejects_same_name_weak_delete_guard_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, "backup-weak-trigger", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript(
            """
            DROP TRIGGER trg_statement_import_delete_no_snapshot;
            CREATE TRIGGER trg_statement_import_delete_no_snapshot
            BEFORE DELETE ON statement_imports
            BEGIN
                SELECT RAISE(ABORT, 'statement_import_delete_has_snapshot');
            END;
            """
        )
        connection.commit()

    with pytest.raises(ValueError, match="结构不兼容"):
        _validate_sqlite_database(settings.database_path)
