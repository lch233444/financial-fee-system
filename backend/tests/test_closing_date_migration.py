from historical_schema_fixtures import copy_synthetic_rows, project_removed_meeting_fields
from app.services.backup import CURRENT_DATABASE_REVISION
import sqlite3

from alembic import command
import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from test_deletion_guard_migration import _alembic_config, _settings, _trigger_sql


def test_closing_guard_ddl_failure_is_atomic_and_retryable(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "closing-rollback", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "a916c0e2b102")
    before = _trigger_sql(settings.database_path)

    def fail_create(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().startswith("CREATE TRIGGER trg_settlement_validate_finalize"):
            raise RuntimeError("synthetic Closing CREATE failure")

    event.listen(Engine, "before_cursor_execute", fail_create)
    try:
        with pytest.raises(RuntimeError, match="synthetic Closing CREATE failure"):
            command.upgrade(config, "head")
    finally:
        event.remove(Engine, "before_cursor_execute", fail_create)
    assert _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("a916c0e2b102",)
    command.upgrade(config, "head")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (CURRENT_DATABASE_REVISION,)


def test_closing_guard_migration_preserves_even_historical_noncurrent_finalized_dates(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app.database as database_module
    from app.main import app
    from app.services.backup import _validate_sqlite_database
    from app.services.closing_date_contract import closing_date_schema_is_current
    from test_closing_date_guards import _change_end_date, _exit_case, WRITE_HEADERS
    from test_workflow_guard_migration import _business_rows

    with TestClient(app, headers=WRITE_HEADERS) as client:
        account_id, payload = _exit_case(client, "MIGOLDHISTORY")
        calculated = client.post("/api/settlements/calculate", json=payload)
        assert calculated.status_code == 200, calculated.text
        finalized = client.post(f"/api/settlements/{calculated.json()['id']}/finalize")
        assert finalized.status_code == 200, finalized.text
        _change_end_date(account_id, None)
    settings = _settings(tmp_path, "closing-history", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "a916c0e2b102")
    copy_synthetic_rows(database_module.settings.database_path, settings.database_path)
    before = _business_rows(settings.database_path)
    command.upgrade(config, "head")
    after = _business_rows(settings.database_path)
    project_removed_meeting_fields(before, after)
    assert after == before
    _validate_sqlite_database(settings.database_path)
    with sqlite3.connect(settings.database_path) as connection:
        assert closing_date_schema_is_current(connection)
        assert connection.execute("SELECT status FROM quarterly_settlements WHERE id=?", (calculated.json()["id"],)).fetchone() == ("FINALIZED",)


def test_quarter_boundary_second_drop_failure_preserves_original_schema_and_retry(tmp_path, monkeypatch):
    settings = _settings(tmp_path, "boundary-rollback", monkeypatch)
    config = _alembic_config(settings)
    command.upgrade(config, "c1a7d5e9b402")
    before = _trigger_sql(settings.database_path)

    def fail_second_drop(_conn, _cursor, statement, _params, _context, _many):
        if statement.strip() == 'DROP TRIGGER "trg_attachment_update_block_finalized_evidence"':
            raise RuntimeError("synthetic second DROP failure")

    event.listen(Engine, "before_cursor_execute", fail_second_drop)
    try:
        with pytest.raises(RuntimeError, match="synthetic second DROP failure"):
            command.upgrade(config, "d4f8a1c73b29")
    finally:
        event.remove(Engine, "before_cursor_execute", fail_second_drop)
    assert _trigger_sql(settings.database_path) == before
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("c1a7d5e9b402",)
    command.upgrade(config, "d4f8a1c73b29")
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("d4f8a1c73b29",)
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert set(_trigger_sql(settings.database_path)) == set(before)
