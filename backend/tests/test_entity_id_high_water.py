from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AppSetting, AuditEvent, Client, StatementImport
from app.services.entity_ids import (
    BALANCE_SNAPSHOT_ID_HIGH_WATER_KEY,
    CLIENT_ID_HIGH_WATER_KEY,
    STATEMENT_IMPORT_ID_HIGH_WATER_KEY,
    SUB_ACCOUNT_ID_HIGH_WATER_KEY,
    EntityIdAllocationError,
    allocate_entity_id,
    validate_entity_id_high_water_settings,
)


ALL_KEYS = (
    CLIENT_ID_HIGH_WATER_KEY,
    SUB_ACCOUNT_ID_HIGH_WATER_KEY,
    STATEMENT_IMPORT_ID_HIGH_WATER_KEY,
    BALANCE_SNAPSHOT_ID_HIGH_WATER_KEY,
)


def _session() -> tuple[object, Session]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(bind=engine)
    db.add_all(AppSetting(key=key, value="0") for key in ALL_KEYS)
    db.commit()
    return engine, db


@pytest.mark.parametrize("invalid_value", ["", "01", "-1", " 1", "1.0", "abc"])
def test_allocator_rejects_missing_or_noncanonical_high_water(invalid_value: str) -> None:
    engine, db = _session()
    try:
        setting = db.get(AppSetting, CLIENT_ID_HIGH_WATER_KEY)
        setting.value = invalid_value
        db.commit()
        with pytest.raises(EntityIdAllocationError):
            allocate_entity_id(db, Client)

        db.delete(setting)
        db.commit()
        with pytest.raises(EntityIdAllocationError, match="missing"):
            allocate_entity_id(db, Client)
    finally:
        db.close()
        engine.dispose()


def test_allocator_rejects_setting_below_live_or_deleted_audit_history() -> None:
    engine, db = _session()
    try:
        db.add(Client(id=5, name="Synthetic", status="DRAFT"))
        db.commit()
        with pytest.raises(EntityIdAllocationError, match="trusted historical maximum"):
            allocate_entity_id(db, Client)

        db.get(AppSetting, CLIENT_ID_HIGH_WATER_KEY).value = "5"
        db.add(
            AuditEvent(
                action="STATEMENT_IMPORT_DELETED",
                entity_type="STATEMENT_IMPORT",
                entity_id=9,
                details_json={"sha256": "legacy"},
            )
        )
        db.get(AppSetting, STATEMENT_IMPORT_ID_HIGH_WATER_KEY).value = "8"
        db.commit()
        with pytest.raises(EntityIdAllocationError, match="trusted historical maximum"):
            allocate_entity_id(db, StatementImport)
    finally:
        db.close()
        engine.dispose()


def test_validation_rejects_conflicting_old_and_new_statement_audit_ids() -> None:
    engine, db = _session()
    try:
        db.add(
            AuditEvent(
                action="STATEMENT_IMPORT_DELETED",
                entity_type="STATEMENT_IMPORT",
                entity_id=10,
                details_json={"deleted_import_id": 11},
            )
        )
        db.commit()
        with pytest.raises(EntityIdAllocationError, match="conflicting deleted IDs"):
            validate_entity_id_high_water_settings(db)
    finally:
        db.close()
        engine.dispose()


def test_validation_allows_only_proven_legacy_reuse_until_current_row_is_deleted() -> None:
    engine, db = _session()
    try:
        db.add(
            StatementImport(
                id=12,
                original_name="synthetic.jpg",
                stored_path="F:/synthetic/synthetic.jpg",
                sha256="12" * 32,
                mime_type="image/jpeg",
                parser_name="TEST",
                parser_version="1.1",
                status="NEEDS_REVIEW",
            )
        )
        db.add(
            AuditEvent(
                id=100,
                action="STATEMENT_IMPORT_DELETED",
                entity_type="STATEMENT_IMPORT",
                entity_id=None,
                details_json={
                    "deleted_import_id": 12,
                    "legacy_entity_id_reused_before_0_2_15": True,
                    "legacy_reuse_disambiguation_revision": "c1a7d5e9b402",
                    "legacy_reuse_correction_audit_id": 101,
                },
            )
        )
        db.add(
            AuditEvent(
                id=101,
                action="LEGACY_STATEMENT_DELETE_ID_REUSE_DISAMBIGUATED",
                entity_type="AUDIT_EVENT",
                entity_id=100,
                details_json={
                    "migration_revision": "c1a7d5e9b402",
                    "proof": "historical_delete_created_before_reused_statement",
                    "historical_deletion_audit_id": 100,
                    "reused_statement_import_id": 12,
                    "historical_delete_created_at": "2026-08-01 12:00:00",
                    "reused_statement_created_at": "2026-09-01 12:00:00",
                    "original_audit_entity_id_cleared": True,
                },
            )
        )
        db.get(AppSetting, STATEMENT_IMPORT_ID_HIGH_WATER_KEY).value = "12"
        db.commit()

        assert validate_entity_id_high_water_settings(db)[
            STATEMENT_IMPORT_ID_HIGH_WATER_KEY
        ] == 12

        db.add(
            AuditEvent(
                id=102,
                action="STATEMENT_IMPORT_DELETED",
                entity_type="STATEMENT_IMPORT",
                entity_id=None,
                details_json={"deleted_import_id": 12},
            )
        )
        db.commit()
        with pytest.raises(EntityIdAllocationError, match="overlap historical deletion"):
            validate_entity_id_high_water_settings(db)
    finally:
        db.close()
        engine.dispose()
