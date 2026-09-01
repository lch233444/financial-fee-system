"""Add database guards for protected client, account, and statement deletion.

Revision ID: c1a7d5e9b402
Revises: 7f3c2a91b6e4
"""

from __future__ import annotations

import json
import re

from alembic import op


revision = "c1a7d5e9b402"
down_revision = "7f3c2a91b6e4"
branch_labels = None
depends_on = None


PREVIOUS_TRIGGER_NAMES = frozenset(
    {
        "trg_transactions_block_finalized_period",
        "trg_transactions_update_block_frozen_period",
        "trg_transactions_delete_block_frozen_period",
        "trg_snapshot_update_block_frozen_reference",
        "trg_snapshot_delete_block_frozen_reference",
        "trg_attachment_update_block_finalized_evidence",
        "trg_attachment_delete_block_finalized_evidence",
        "trg_statement_import_update_block_finalized_evidence",
        "trg_statement_import_delete_block_finalized_evidence",
        "trg_settlement_block_out_of_order_insert",
        "trg_settlement_account_line_order",
        "trg_settlement_account_line_update_order",
        "trg_settlement_validate_finalize",
        "trg_settlement_validate_void",
        "trg_settlement_insert_draft_only",
        "trg_settlement_lifecycle_transition",
        "trg_settlement_delete_non_draft",
        "trg_settlement_parent_financial_lock",
        "trg_settlement_line_insert_draft_only",
        "trg_settlement_line_update_draft_only",
        "trg_settlement_line_delete_draft_only",
        "trg_invoice_source_insert_draft_only",
        "trg_invoice_source_update_draft_only",
        "trg_invoice_source_delete_draft_only",
        "trg_invoice_line_insert_draft_only",
        "trg_invoice_line_update_draft_only",
        "trg_invoice_line_delete_draft_only",
        "trg_invoice_validate_issue",
        "trg_invoice_financial_header_update_lock",
        "trg_invoice_insert_draft_only",
        "trg_invoice_lifecycle_transition",
        "trg_invoice_issue_metadata_guard",
        "trg_payment_validate_insert",
        "trg_invoice_block_void_with_payment",
        "trg_invoice_sources_deactivate_on_void",
        "trg_payment_claim_proof",
        "trg_payment_update_immutable",
        "trg_payment_delete_immutable",
        "trg_invoice_correction_validate_insert",
        "trg_invoice_correction_validate_update",
        "trg_invoice_correction_delete_immutable",
        "trg_payment_allocation_validate_insert",
        "trg_payment_allocation_update_immutable",
        "trg_payment_allocation_delete_immutable",
        "trg_payment_refund_validate_insert",
        "trg_payment_refund_claim_proof",
        "trg_payment_refund_update_immutable",
        "trg_payment_refund_delete_immutable",
        "trg_invoice_adjustment_validate_insert",
        "trg_invoice_adjustment_update_immutable",
        "trg_invoice_adjustment_delete_immutable",
        "trg_attachment_update_block_payment_evidence",
        "trg_attachment_delete_block_payment_evidence",
    }
)

DELETE_GUARD_TRIGGER_NAMES = frozenset(
    {
        "trg_client_delete_no_cascade",
        "trg_account_delete_no_cascade",
        "trg_statement_import_delete_no_snapshot",
        "trg_snapshot_delete_no_confirmed_import",
    }
)

TARGET_TRIGGER_NAMES = PREVIOUS_TRIGGER_NAMES | DELETE_GUARD_TRIGGER_NAMES

ID_HIGH_WATER_TABLES = (
    ("id_high_water.clients", "clients"),
    ("id_high_water.sub_accounts", "sub_accounts"),
    ("id_high_water.statement_imports", "statement_imports"),
    ("id_high_water.balance_snapshots", "balance_snapshots"),
)
_NON_NEGATIVE_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")
_SQLITE_MAX_ROW_ID = (1 << 63) - 1
_LEGACY_REUSE_MARKER = "legacy_entity_id_reused_before_0_2_15"
_LEGACY_REUSE_REVISION_FIELD = "legacy_reuse_disambiguation_revision"
_LEGACY_REUSE_CORRECTION_FIELD = "legacy_reuse_correction_audit_id"
_LEGACY_REUSE_CORRECTION_ACTION = "LEGACY_STATEMENT_DELETE_ID_REUSE_DISAMBIGUATED"
_LEGACY_REUSE_PROOF = "historical_delete_created_before_reused_statement"


def _trigger_sql() -> dict[str, str]:
    rows = op.get_bind().exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
    ).fetchall()
    return {str(name): str(sql or "") for name, sql in rows}


def _validate_database_health(stage: str) -> None:
    connection = op.get_bind()
    integrity = [
        tuple(row) for row in connection.exec_driver_sql("PRAGMA integrity_check").fetchall()
    ]
    if integrity != [("ok",)]:
        raise RuntimeError(f"0.2.15{stage}SQLite完整性检查失败，已停止迁移")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        affected = ", ".join(sorted({str(row[0]) for row in violations}))
        raise RuntimeError(f"0.2.15{stage}存在外键违规（涉及{affected}），已停止迁移")


def _validate_pre_upgrade() -> None:
    connection = op.get_bind()
    revision_rows = [
        tuple(row)
        for row in connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
    ]
    if revision_rows != [(down_revision,)]:
        raise RuntimeError("数据库不是完整的7f3c2a91b6e4旧head，已停止0.2.15迁移")

    _validate_database_health("迁移前")
    triggers = _trigger_sql()
    if set(triggers) != PREVIOUS_TRIGGER_NAMES or any(
        not triggers[name].strip() for name in PREVIOUS_TRIGGER_NAMES
    ):
        missing = sorted(PREVIOUS_TRIGGER_NAMES - set(triggers))
        extra = sorted(set(triggers) - PREVIOUS_TRIGGER_NAMES)
        raise RuntimeError(
            "数据库不是完整53-Trigger的7f3c2a91b6e4结构；"
            f"缺少={missing or '无'}，额外={extra or '无'}"
        )


def _parse_high_water(raw_value: object, *, key: str) -> int:
    if not isinstance(raw_value, str) or _NON_NEGATIVE_INTEGER.fullmatch(raw_value) is None:
        raise RuntimeError(f"0.2.15迁移发现ID高水位设置异常：{key}")
    value = int(raw_value)
    if value > _SQLITE_MAX_ROW_ID:
        raise RuntimeError(f"0.2.15迁移发现ID高水位超出SQLite范围：{key}")
    return value


def _deleted_audit_id(value: object, *, audit_id: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise RuntimeError(f"0.2.15迁移发现删除审计#{audit_id}的{field}异常")
    if value > _SQLITE_MAX_ROW_ID:
        raise RuntimeError(f"0.2.15迁移发现删除审计#{audit_id}的{field}超出SQLite范围")
    return value


def _audit_details(raw_value: object, *, audit_id: int) -> dict:
    try:
        details = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"0.2.15迁移发现删除审计#{audit_id}的JSON异常") from exc
    if not isinstance(details, dict):
        raise RuntimeError(f"0.2.15迁移发现删除审计#{audit_id}的JSON异常")
    return details


def _validated_legacy_reuse_deleted_id(
    *,
    audit_id: int,
    action: str,
    event_entity_id: object,
    details: dict,
) -> int | None:
    marker_present = any(
        field in details
        for field in (
            _LEGACY_REUSE_MARKER,
            _LEGACY_REUSE_REVISION_FIELD,
            _LEGACY_REUSE_CORRECTION_FIELD,
        )
    )
    if not marker_present:
        return None
    if (
        action != "STATEMENT_IMPORT_DELETED"
        or event_entity_id is not None
        or details.get(_LEGACY_REUSE_MARKER) is not True
        or details.get(_LEGACY_REUSE_REVISION_FIELD) != revision
    ):
        raise RuntimeError(f"0.2.15迁移发现账单删除审计#{audit_id}的历史ID消歧标记异常")
    deleted_id = _deleted_audit_id(
        details.get("deleted_import_id"),
        audit_id=audit_id,
        field="deleted_import_id",
    )
    correction_id = _deleted_audit_id(
        details.get(_LEGACY_REUSE_CORRECTION_FIELD),
        audit_id=audit_id,
        field=_LEGACY_REUSE_CORRECTION_FIELD,
    )
    correction_rows = op.get_bind().exec_driver_sql(
        """
        SELECT action, entity_type, entity_id, details_json
        FROM audit_events
        WHERE id = ?
        """,
        (correction_id,),
    ).fetchall()
    if len(correction_rows) != 1:
        raise RuntimeError(f"0.2.15迁移发现账单删除审计#{audit_id}缺少历史ID消歧审计")
    correction_action, correction_type, correction_entity_id, raw_correction_details = (
        correction_rows[0]
    )
    correction_details = _audit_details(raw_correction_details, audit_id=correction_id)
    if (
        correction_action != _LEGACY_REUSE_CORRECTION_ACTION
        or correction_type != "AUDIT_EVENT"
        or correction_entity_id != audit_id
        or correction_details.get("migration_revision") != revision
        or correction_details.get("proof") != _LEGACY_REUSE_PROOF
        or correction_details.get("historical_deletion_audit_id") != audit_id
        or correction_details.get("reused_statement_import_id") != deleted_id
        or correction_details.get("original_audit_entity_id_cleared") is not True
        or not isinstance(correction_details.get("historical_delete_created_at"), str)
        or not correction_details.get("historical_delete_created_at")
        or not isinstance(correction_details.get("reused_statement_created_at"), str)
        or not correction_details.get("reused_statement_created_at")
    ):
        raise RuntimeError(f"0.2.15迁移发现账单删除审计#{audit_id}的历史ID消歧证据异常")
    return deleted_id


def _normalize_proven_legacy_statement_id_reuse() -> None:
    connection = op.get_bind()
    collision_rows = connection.exec_driver_sql(
        """
        SELECT audit.id, audit.action, audit.entity_type, audit.entity_id,
               audit.details_json, audit.created_at, statement.created_at,
               CASE
                   WHEN julianday(audit.created_at) IS NOT NULL
                    AND julianday(statement.created_at) IS NOT NULL
                    AND julianday(audit.created_at) < julianday(statement.created_at)
                   THEN 1 ELSE 0
               END AS chronology_proven
        FROM audit_events AS audit
        JOIN statement_imports AS statement ON statement.id = audit.entity_id
        WHERE audit.action IN (
            'STATEMENT_IMPORT_DELETED',
            'STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED'
        )
        ORDER BY audit.id
        """
    ).fetchall()
    collisions_per_id: dict[int, int] = {}
    for row in collision_rows:
        reused_id = _deleted_audit_id(row[3], audit_id=int(row[0]), field="entity_id")
        collisions_per_id[reused_id] = collisions_per_id.get(reused_id, 0) + 1

    reserved_fields = {
        "deleted_import_id",
        _LEGACY_REUSE_MARKER,
        _LEGACY_REUSE_REVISION_FIELD,
        _LEGACY_REUSE_CORRECTION_FIELD,
    }
    for (
        audit_id_raw,
        action,
        entity_type,
        event_entity_id,
        raw_details,
        historical_created_at,
        reused_created_at,
        chronology_proven,
    ) in collision_rows:
        audit_id = int(audit_id_raw)
        reused_id = _deleted_audit_id(
            event_entity_id,
            audit_id=audit_id,
            field="entity_id",
        )
        details = _audit_details(raw_details, audit_id=audit_id)
        normalized_type = (
            entity_type.strip().upper().replace("-", "_").replace(" ", "_")
            if isinstance(entity_type, str)
            else ""
        )
        if (
            action != "STATEMENT_IMPORT_DELETED"
            or normalized_type != "STATEMENT_IMPORT"
            or collisions_per_id[reused_id] != 1
            or any(field in details for field in reserved_fields)
            or chronology_proven != 1
            or historical_created_at is None
            or reused_created_at is None
        ):
            continue

        correction_details = {
            "migration_revision": revision,
            "proof": _LEGACY_REUSE_PROOF,
            "historical_deletion_audit_id": audit_id,
            "reused_statement_import_id": reused_id,
            "historical_delete_created_at": str(historical_created_at),
            "reused_statement_created_at": str(reused_created_at),
            "original_audit_entity_id_cleared": True,
        }
        correction_result = connection.exec_driver_sql(
            """
            INSERT INTO audit_events
                (action, entity_type, entity_id, details_json, created_at)
            VALUES (?, 'AUDIT_EVENT', ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                _LEGACY_REUSE_CORRECTION_ACTION,
                audit_id,
                json.dumps(correction_details, ensure_ascii=True, sort_keys=True),
            ),
        )
        correction_id = int(correction_result.lastrowid)
        normalized_details = {
            **details,
            "deleted_import_id": reused_id,
            _LEGACY_REUSE_MARKER: True,
            _LEGACY_REUSE_REVISION_FIELD: revision,
            _LEGACY_REUSE_CORRECTION_FIELD: correction_id,
        }
        update_result = connection.exec_driver_sql(
            """
            UPDATE audit_events
            SET entity_id = NULL, details_json = ?
            WHERE id = ? AND action = 'STATEMENT_IMPORT_DELETED' AND entity_id = ?
            """,
            (
                json.dumps(normalized_details, ensure_ascii=True, sort_keys=True),
                audit_id,
                reused_id,
            ),
        )
        if update_result.rowcount != 1:
            raise RuntimeError("0.2.15迁移写入历史ID消歧审计时检测到并发变化")


def _statement_deleted_id(
    *, audit_id: int, event_entity_id: object, details: dict
) -> int:
    detail_id = None
    if "deleted_import_id" in details:
        detail_id = _deleted_audit_id(
            details.get("deleted_import_id"),
            audit_id=audit_id,
            field="deleted_import_id",
        )
    legacy_id = None
    if event_entity_id is not None:
        legacy_id = _deleted_audit_id(
            event_entity_id,
            audit_id=audit_id,
            field="entity_id",
        )
    if detail_id is None and legacy_id is None:
        raise RuntimeError(f"0.2.15迁移发现账单删除审计#{audit_id}缺少原ID")
    if detail_id is not None and legacy_id is not None and detail_id != legacy_id:
        raise RuntimeError(f"0.2.15迁移发现账单删除审计#{audit_id}的原ID冲突")
    return detail_id if detail_id is not None else int(legacy_id)


def _deleted_audit_id_sets() -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    deleted_ids = {key: set() for key, _table_name in ID_HIGH_WATER_TABLES}
    strict_deleted_ids = {key: set() for key, _table_name in ID_HIGH_WATER_TABLES}
    rows = op.get_bind().exec_driver_sql(
        """
        SELECT id, action, entity_id, details_json
        FROM audit_events
        WHERE action IN (
            'MASTER_DATA_DELETED',
            'STATEMENT_IMPORT_DELETED',
            'STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED'
        )
        ORDER BY id
        """
    ).fetchall()
    for audit_id_raw, action, event_entity_id, raw_details in rows:
        audit_id = int(audit_id_raw)
        details = _audit_details(raw_details, audit_id=audit_id)
        if action == "MASTER_DATA_DELETED":
            entity_type = details.get("deleted_entity_type")
            if not isinstance(entity_type, str) or not entity_type.strip():
                raise RuntimeError(f"0.2.15迁移发现主数据删除审计#{audit_id}缺少类型")
            deleted_id = _deleted_audit_id(
                details.get("deleted_entity_id"),
                audit_id=audit_id,
                field="deleted_entity_id",
            )
            if event_entity_id is not None:
                legacy_id = _deleted_audit_id(
                    event_entity_id,
                    audit_id=audit_id,
                    field="entity_id",
                )
                if legacy_id != deleted_id:
                    raise RuntimeError(f"0.2.15迁移发现主数据删除审计#{audit_id}的原ID冲突")
            normalized_type = entity_type.strip().upper().replace("-", "_").replace(" ", "_")
            key = {
                "CLIENT": "id_high_water.clients",
                "SUB_ACCOUNT": "id_high_water.sub_accounts",
            }.get(normalized_type)
            if key is not None:
                deleted_ids[key].add(deleted_id)
                strict_deleted_ids[key].add(deleted_id)
            continue

        legacy_reuse_deleted_id = _validated_legacy_reuse_deleted_id(
            audit_id=audit_id,
            action=str(action),
            event_entity_id=event_entity_id,
            details=details,
        )
        deleted_import_id = _statement_deleted_id(
            audit_id=audit_id,
            event_entity_id=event_entity_id,
            details=details,
        )
        statement_key = "id_high_water.statement_imports"
        deleted_ids[statement_key].add(deleted_import_id)
        if legacy_reuse_deleted_id is None:
            strict_deleted_ids[statement_key].add(deleted_import_id)
        elif legacy_reuse_deleted_id != deleted_import_id:
            raise RuntimeError(f"0.2.15迁移发现账单删除审计#{audit_id}的历史ID消歧原ID冲突")
        if action == "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED":
            deleted_snapshot_id = _deleted_audit_id(
                details.get("deleted_snapshot_id"),
                audit_id=audit_id,
                field="deleted_snapshot_id",
            )
            snapshot_key = "id_high_water.balance_snapshots"
            deleted_ids[snapshot_key].add(deleted_snapshot_id)
            strict_deleted_ids[snapshot_key].add(deleted_snapshot_id)
    return deleted_ids, strict_deleted_ids


def _deleted_audit_high_waters(
    deleted_ids: dict[str, set[int]] | None = None,
) -> dict[str, int]:
    ids_by_key = deleted_ids if deleted_ids is not None else _deleted_audit_id_sets()[0]
    return {key: max(values, default=0) for key, values in ids_by_key.items()}


def _validate_no_deleted_id_reuse(strict_deleted_ids: dict[str, set[int]]) -> None:
    connection = op.get_bind()
    for key, table_name in ID_HIGH_WATER_TABLES:
        live_ids = {
            int(row[0])
            for row in connection.exec_driver_sql(
                f'SELECT id FROM "{table_name}"'
            ).fetchall()
        }
        reused_ids = live_ids & strict_deleted_ids[key]
        if reused_ids:
            raise RuntimeError(
                "0.2.15迁移发现现存记录ID与历史删除审计重复："
                f"{key}，冲突数量={len(reused_ids)}；已停止迁移，请人工处置"
            )


def _seed_id_high_water_settings() -> None:
    connection = op.get_bind()
    deleted_ids, strict_deleted_ids = _deleted_audit_id_sets()
    _validate_no_deleted_id_reuse(strict_deleted_ids)
    audit_maxima = _deleted_audit_high_waters(deleted_ids)
    for key, table_name in ID_HIGH_WATER_TABLES:
        current_max = int(
            connection.exec_driver_sql(
                f'SELECT COALESCE(MAX(id), 0) FROM "{table_name}"'
            ).scalar_one()
        )
        rows = connection.exec_driver_sql(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchall()
        trusted_max = max(current_max, audit_maxima[key])
        if not rows:
            connection.exec_driver_sql(
                """
                INSERT INTO app_settings (key, value, created_at, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (key, str(trusted_max)),
            )
            continue
        if len(rows) != 1:
            raise RuntimeError(f"0.2.15迁移发现重复ID高水位设置：{key}")
        stored_max = _parse_high_water(rows[0][0], key=key)
        safe_max = max(stored_max, trusted_max)
        if safe_max != stored_max:
            connection.exec_driver_sql(
                """
                UPDATE app_settings
                SET value = ?, updated_at = CURRENT_TIMESTAMP
                WHERE key = ?
                """,
                (str(safe_max), key),
            )


def _validate_id_high_water_settings() -> None:
    connection = op.get_bind()
    deleted_ids, strict_deleted_ids = _deleted_audit_id_sets()
    _validate_no_deleted_id_reuse(strict_deleted_ids)
    audit_maxima = _deleted_audit_high_waters(deleted_ids)
    for key, table_name in ID_HIGH_WATER_TABLES:
        rows = connection.exec_driver_sql(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchall()
        if len(rows) != 1:
            raise RuntimeError(f"0.2.15迁移后ID高水位设置不完整：{key}")
        stored_max = _parse_high_water(rows[0][0], key=key)
        current_max = int(
            connection.exec_driver_sql(
                f'SELECT COALESCE(MAX(id), 0) FROM "{table_name}"'
            ).scalar_one()
        )
        if stored_max < max(current_max, audit_maxima[key]):
            raise RuntimeError(f"0.2.15迁移后ID高水位低于可信历史最大ID：{key}")


def _create_delete_guard_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_client_delete_no_cascade
        BEFORE DELETE ON clients
        WHEN EXISTS (
            SELECT 1 FROM sub_accounts AS account WHERE account.client_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM quarterly_settlements AS settlement WHERE settlement.client_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM invoices AS invoice WHERE invoice.client_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM attachments AS attachment
            WHERE REPLACE(REPLACE(UPPER(TRIM(attachment.entity_type)), '-', '_'), ' ', '_') = 'CLIENT'
              AND attachment.entity_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM export_records AS export_record
            WHERE REPLACE(REPLACE(UPPER(TRIM(export_record.entity_type)), '-', '_'), ' ', '_') = 'CLIENT'
              AND export_record.entity_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'client_delete_would_cascade');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_account_delete_no_cascade
        BEFORE DELETE ON sub_accounts
        WHEN EXISTS (
            SELECT 1 FROM transactions AS transaction_record
            WHERE transaction_record.account_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM balance_snapshots AS snapshot WHERE snapshot.account_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM statement_imports AS statement
            WHERE statement.confirmed_account_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM settlement_account_lines AS line WHERE line.account_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM attachments AS attachment
            WHERE REPLACE(REPLACE(UPPER(TRIM(attachment.entity_type)), '-', '_'), ' ', '_')
                  IN ('ACCOUNT', 'SUB_ACCOUNT')
              AND attachment.entity_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM export_records AS export_record
            WHERE REPLACE(REPLACE(UPPER(TRIM(export_record.entity_type)), '-', '_'), ' ', '_')
                  IN ('ACCOUNT', 'SUB_ACCOUNT')
              AND export_record.entity_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'account_delete_would_cascade');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_statement_import_delete_no_snapshot
        BEFORE DELETE ON statement_imports
        WHEN EXISTS (
            SELECT 1 FROM balance_snapshots AS snapshot
            WHERE snapshot.statement_import_id = OLD.id
        ) AND NOT EXISTS (
            SELECT 1
            FROM balance_snapshots AS snapshot
            JOIN settlement_account_lines AS line
              ON snapshot.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE snapshot.statement_import_id = OLD.id
              AND settlement.status = 'FINALIZED'
        ) OR EXISTS (
            SELECT 1 FROM statement_imports AS dependent
            WHERE dependent.duplicate_of_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM attachments AS attachment
            WHERE REPLACE(REPLACE(UPPER(TRIM(attachment.entity_type)), '-', '_'), ' ', '_')
                  = 'STATEMENT_IMPORT'
              AND attachment.entity_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM export_records AS export_record
            WHERE REPLACE(REPLACE(UPPER(TRIM(export_record.entity_type)), '-', '_'), ' ', '_')
                  = 'STATEMENT_IMPORT'
              AND export_record.entity_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'statement_import_delete_has_snapshot');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_snapshot_delete_no_confirmed_import
        BEFORE DELETE ON balance_snapshots
        WHEN EXISTS (
            SELECT 1 FROM statement_imports AS statement
            WHERE statement.confirmed_snapshot_id = OLD.id
        ) AND NOT EXISTS (
            SELECT 1
            FROM settlement_account_lines AS line
            JOIN quarterly_settlements AS settlement ON settlement.id = line.settlement_id
            WHERE OLD.id IN (line.beginning_snapshot_id, line.closing_snapshot_id)
              AND settlement.status = 'FINALIZED'
        ) OR EXISTS (
            SELECT 1 FROM attachments AS attachment
            WHERE REPLACE(REPLACE(UPPER(TRIM(attachment.entity_type)), '-', '_'), ' ', '_')
                  = 'SNAPSHOT'
              AND attachment.entity_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM export_records AS export_record
            WHERE REPLACE(REPLACE(UPPER(TRIM(export_record.entity_type)), '-', '_'), ' ', '_')
                  = 'SNAPSHOT'
              AND export_record.entity_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'snapshot_delete_has_confirmed_import');
        END
        """
    )


def _validate_post_upgrade() -> None:
    _validate_database_health("迁移后")
    _validate_id_high_water_settings()
    triggers = _trigger_sql()
    if set(triggers) != TARGET_TRIGGER_NAMES or any(
        not triggers[name].strip() for name in TARGET_TRIGGER_NAMES
    ):
        raise RuntimeError("0.2.15迁移后Trigger清单不完整，已回滚迁移")


def upgrade() -> None:
    # Acquire SQLite's writer lock before validating the old head so another
    # process cannot alter relationships between preflight and CREATE TRIGGER.
    op.get_bind().exec_driver_sql("BEGIN IMMEDIATE")
    _validate_pre_upgrade()
    _normalize_proven_legacy_statement_id_reuse()
    _seed_id_high_water_settings()
    _create_delete_guard_triggers()
    _validate_post_upgrade()


def downgrade() -> None:
    raise RuntimeError(
        "Client、Sub Account和确认账单的删除保护不允许原地降级；"
        "请使用0.2.15升级前已验证的完整备份回滚"
    )
