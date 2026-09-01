from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AppSetting,
    AuditEvent,
    BalanceSnapshot,
    Client,
    StatementImport,
    SubAccount,
)


CLIENT_ID_HIGH_WATER_KEY = "id_high_water.clients"
SUB_ACCOUNT_ID_HIGH_WATER_KEY = "id_high_water.sub_accounts"
STATEMENT_IMPORT_ID_HIGH_WATER_KEY = "id_high_water.statement_imports"
BALANCE_SNAPSHOT_ID_HIGH_WATER_KEY = "id_high_water.balance_snapshots"

_LEGACY_REUSE_MARKER = "legacy_entity_id_reused_before_0_2_15"
_LEGACY_REUSE_REVISION_FIELD = "legacy_reuse_disambiguation_revision"
_LEGACY_REUSE_CORRECTION_FIELD = "legacy_reuse_correction_audit_id"
_LEGACY_REUSE_REVISION = "c1a7d5e9b402"
_LEGACY_REUSE_CORRECTION_ACTION = "LEGACY_STATEMENT_DELETE_ID_REUSE_DISAMBIGUATED"
_LEGACY_REUSE_PROOF = "historical_delete_created_before_reused_statement"

_SQLITE_MAX_ROW_ID = (1 << 63) - 1
_NON_NEGATIVE_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")
_MODEL_KEYS = {
    Client: CLIENT_ID_HIGH_WATER_KEY,
    SubAccount: SUB_ACCOUNT_ID_HIGH_WATER_KEY,
    StatementImport: STATEMENT_IMPORT_ID_HIGH_WATER_KEY,
    BalanceSnapshot: BALANCE_SNAPSHOT_ID_HIGH_WATER_KEY,
}


class EntityIdAllocationError(RuntimeError):
    """Raised when a persisted entity ID high-water mark cannot be trusted."""


def _stored_high_water(raw_value: object, *, key: str) -> int:
    if not isinstance(raw_value, str) or _NON_NEGATIVE_INTEGER.fullmatch(raw_value) is None:
        raise EntityIdAllocationError(f"ID high-water setting {key!r} is malformed")
    value = int(raw_value)
    if value > _SQLITE_MAX_ROW_ID:
        raise EntityIdAllocationError(f"ID high-water setting {key!r} exceeds SQLite rowid")
    return value


def _audit_id(value: object, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise EntityIdAllocationError(f"Deleted entity ID in {context} is malformed")
    if value > _SQLITE_MAX_ROW_ID:
        raise EntityIdAllocationError(f"Deleted entity ID in {context} exceeds SQLite rowid")
    return value


def _statement_deleted_id(db: Session, audit: AuditEvent) -> tuple[int, bool]:
    details = audit.details_json
    if not isinstance(details, dict):
        raise EntityIdAllocationError(f"Statement deletion audit #{audit.id} has malformed details")
    detail_value = None
    if "deleted_import_id" in details:
        detail_value = _audit_id(
            details.get("deleted_import_id"),
            context=f"statement deletion audit #{audit.id}",
        )
    entity_value = None
    if audit.entity_id is not None:
        entity_value = _audit_id(
            audit.entity_id,
            context=f"statement deletion audit #{audit.id}",
        )
    if detail_value is None and entity_value is None:
        raise EntityIdAllocationError(f"Statement deletion audit #{audit.id} has no deleted ID")
    if detail_value is not None and entity_value is not None and detail_value != entity_value:
        raise EntityIdAllocationError(
            f"Statement deletion audit #{audit.id} has conflicting deleted IDs"
        )
    deleted_id = detail_value if detail_value is not None else entity_value
    marker_present = any(
        field in details
        for field in (
            _LEGACY_REUSE_MARKER,
            _LEGACY_REUSE_REVISION_FIELD,
            _LEGACY_REUSE_CORRECTION_FIELD,
        )
    )
    if not marker_present:
        return deleted_id, False  # type: ignore[return-value]
    if (
        audit.action != "STATEMENT_IMPORT_DELETED"
        or audit.entity_id is not None
        or details.get(_LEGACY_REUSE_MARKER) is not True
        or details.get(_LEGACY_REUSE_REVISION_FIELD) != _LEGACY_REUSE_REVISION
    ):
        raise EntityIdAllocationError(
            f"Statement deletion audit #{audit.id} has a malformed legacy reuse marker"
        )
    correction_id = _audit_id(
        details.get(_LEGACY_REUSE_CORRECTION_FIELD),
        context=f"statement deletion audit #{audit.id}",
    )
    correction = db.get(AuditEvent, correction_id)
    correction_details = correction.details_json if correction is not None else None
    if (
        correction is None
        or correction.action != _LEGACY_REUSE_CORRECTION_ACTION
        or correction.entity_type != "AUDIT_EVENT"
        or correction.entity_id != audit.id
        or not isinstance(correction_details, dict)
        or correction_details.get("migration_revision") != _LEGACY_REUSE_REVISION
        or correction_details.get("proof") != _LEGACY_REUSE_PROOF
        or correction_details.get("historical_deletion_audit_id") != audit.id
        or correction_details.get("reused_statement_import_id") != deleted_id
        or correction_details.get("original_audit_entity_id_cleared") is not True
        or not isinstance(correction_details.get("historical_delete_created_at"), str)
        or not correction_details.get("historical_delete_created_at")
        or not isinstance(correction_details.get("reused_statement_created_at"), str)
        or not correction_details.get("reused_statement_created_at")
    ):
        raise EntityIdAllocationError(
            f"Statement deletion audit #{audit.id} has invalid legacy reuse evidence"
        )
    return deleted_id, True  # type: ignore[return-value]


def _master_deleted_id(audit: AuditEvent) -> tuple[str, int]:
    details = audit.details_json
    if not isinstance(details, dict):
        raise EntityIdAllocationError(f"Master-data deletion audit #{audit.id} has malformed details")
    entity_type = details.get("deleted_entity_type")
    if not isinstance(entity_type, str) or not entity_type.strip():
        raise EntityIdAllocationError(f"Master-data deletion audit #{audit.id} has no entity type")
    deleted_id = _audit_id(
        details.get("deleted_entity_id"),
        context=f"master-data deletion audit #{audit.id}",
    )
    if audit.entity_id is not None:
        event_id = _audit_id(
            audit.entity_id,
            context=f"master-data deletion audit #{audit.id}",
        )
        if event_id != deleted_id:
            raise EntityIdAllocationError(
                f"Master-data deletion audit #{audit.id} has conflicting deleted IDs"
            )
    normalized_type = entity_type.strip().upper().replace("-", "_").replace(" ", "_")
    return normalized_type, deleted_id


def _deleted_entity_id_sets(db: Session, model: type) -> tuple[set[int], set[int]]:
    deleted_ids: set[int] = set()
    strict_deleted_ids: set[int] = set()
    if model in {Client, SubAccount}:
        target_type = "CLIENT" if model is Client else "SUB_ACCOUNT"
        audits = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "MASTER_DATA_DELETED")
        ).all()
        for audit in audits:
            entity_type, deleted_id = _master_deleted_id(audit)
            if entity_type == target_type:
                deleted_ids.add(deleted_id)
                strict_deleted_ids.add(deleted_id)
        return deleted_ids, strict_deleted_ids

    statement_audits = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action.in_(
                (
                    "STATEMENT_IMPORT_DELETED",
                    "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED",
                )
            )
        )
    ).all()
    for audit in statement_audits:
        if model is StatementImport:
            deleted_id, legacy_reuse_disambiguated = _statement_deleted_id(db, audit)
            deleted_ids.add(deleted_id)
            if not legacy_reuse_disambiguated:
                strict_deleted_ids.add(deleted_id)
        elif model is BalanceSnapshot and audit.action == (
            "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED"
        ):
            details = audit.details_json
            if not isinstance(details, dict):
                raise EntityIdAllocationError(
                    f"Statement deletion audit #{audit.id} has malformed details"
                )
            deleted_snapshot_id = _audit_id(
                details.get("deleted_snapshot_id"),
                context=f"statement deletion audit #{audit.id}",
            )
            deleted_ids.add(deleted_snapshot_id)
            strict_deleted_ids.add(deleted_snapshot_id)
    if model not in {StatementImport, BalanceSnapshot}:
        raise EntityIdAllocationError(f"No deletion-audit contract is registered for {model!r}")
    return deleted_ids, strict_deleted_ids


def deleted_entity_ids(db: Session, model: type) -> set[int]:
    return _deleted_entity_id_sets(db, model)[0]


def deleted_id_high_water(db: Session, model: type) -> int:
    return max(deleted_entity_ids(db, model), default=0)


def entity_id_trusted_lower_bound(db: Session, model: type) -> int:
    current_ids = {int(value) for value in db.scalars(select(model.id)).all()}
    current_max = max(current_ids, default=0)
    if current_max < 0 or current_max > _SQLITE_MAX_ROW_ID:
        raise EntityIdAllocationError(f"Current maximum ID for {model.__name__} is invalid")
    deleted_ids, strict_deleted_ids = _deleted_entity_id_sets(db, model)
    reused_ids = current_ids & strict_deleted_ids
    if reused_ids:
        raise EntityIdAllocationError(
            f"Live {model.__name__} IDs overlap historical deletion audits: "
            f"conflict_count={len(reused_ids)}"
        )
    return max(current_max, max(deleted_ids, default=0))


def validate_entity_id_high_water_settings(db: Session) -> dict[str, int]:
    validated: dict[str, int] = {}
    for model, key in _MODEL_KEYS.items():
        setting = db.get(AppSetting, key)
        if setting is None:
            raise EntityIdAllocationError(f"Required ID high-water setting {key!r} is missing")
        stored_max = _stored_high_water(setting.value, key=key)
        trusted_lower_bound = entity_id_trusted_lower_bound(db, model)
        if stored_max < trusted_lower_bound:
            raise EntityIdAllocationError(
                f"ID high-water setting {key!r} is below the trusted historical maximum"
            )
        validated[key] = stored_max
    return validated


def seed_missing_entity_id_high_water_settings(db: Session) -> dict[str, int]:
    """Seed keys only for the audited unversioned-current-schema bootstrap path."""

    seeded: dict[str, int] = {}
    for model, key in _MODEL_KEYS.items():
        trusted_lower_bound = entity_id_trusted_lower_bound(db, model)
        setting = db.get(AppSetting, key)
        if setting is None:
            setting = AppSetting(key=key, value=str(trusted_lower_bound))
            db.add(setting)
            seeded[key] = trusted_lower_bound
            continue
        stored_max = _stored_high_water(setting.value, key=key)
        if stored_max < trusted_lower_bound:
            raise EntityIdAllocationError(
                f"ID high-water setting {key!r} is below the trusted historical maximum"
            )
    return seeded


def allocate_entity_id(db: Session, model: type) -> int:
    """Allocate a non-reusable integer ID inside a caller-held write transaction.

    Callers must acquire SQLite ``BEGIN IMMEDIATE`` before entering this helper.
    The table maximum is included so a supported API call safely catches up after
    legacy/direct-SQL rows, while the persisted setting prevents reuse after a
    controlled physical deletion.
    """

    key = _MODEL_KEYS.get(model)
    if key is None:
        raise EntityIdAllocationError(f"No ID high-water setting is registered for {model!r}")

    setting = db.get(AppSetting, key)
    if setting is None:
        raise EntityIdAllocationError(f"Required ID high-water setting {key!r} is missing")
    stored_max = _stored_high_water(setting.value, key=key)
    trusted_lower_bound = entity_id_trusted_lower_bound(db, model)
    if stored_max < trusted_lower_bound:
        raise EntityIdAllocationError(
            f"ID high-water setting {key!r} is below the trusted historical maximum"
        )

    next_id = stored_max + 1
    if next_id > _SQLITE_MAX_ROW_ID:
        raise EntityIdAllocationError(f"ID space for {model.__name__} is exhausted")
    setting.value = str(next_id)
    return next_id
