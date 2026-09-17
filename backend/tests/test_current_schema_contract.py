import sqlite3

from alembic import command
import pytest
from sqlalchemy import create_engine

import app.database as database_module
from app.services.backup import _validate_sqlite_database
from test_deletion_guard_migration import _alembic_config, _settings, _trigger_sql


@pytest.mark.parametrize("trigger", [
    "trg_payment_update_immutable",
    "trg_payment_allocation_delete_immutable",
    "trg_invoice_source_update_draft_only",
])
def test_startup_and_backup_reject_missing_current_guard_without_repair(tmp_path, monkeypatch, trigger):
    settings = _settings(tmp_path, "damaged", monkeypatch)
    command.upgrade(_alembic_config(settings), "head")
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(f'DROP TRIGGER "{trigger}"')
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    damaged = _trigger_sql(settings.database_path)
    with pytest.raises(ValueError, match="结构"):
        _validate_sqlite_database(settings.database_path)
    engine = create_engine(settings.database_url)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "settings", settings)
    try:
        with pytest.raises(RuntimeError, match="结构|Trigger"):
            database_module.init_db()
        assert _trigger_sql(settings.database_path) == damaged
        with sqlite3.connect(settings.database_path) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchall() == revision
    finally:
        engine.dispose()
