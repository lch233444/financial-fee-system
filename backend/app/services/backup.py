from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import warnings
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from ..config import APP_VERSION, get_settings
from .delete_guard_contract import delete_guard_trigger_sql_is_current
from .settlement_boundary_contract import (
    settlement_boundary_trigger_sql_is_current,
    settlement_boundary_trigger_sql_is_legacy,
)
from .storage import sha256_file
from .workflow_guard_contract import workflow_trigger_sql_is_current, workflow_trigger_sql_is_legacy


INCLUDED_DIRECTORIES = ("attachments", "statement_imports", "output")
BACKUP_FORMAT = "financial-fee-system-data-package"
LEGACY_BACKUP_FORMAT = "financial-fee-system-backup"
BACKUP_VERSION = 1
MANIFEST_KEYS = frozenset(
    {"format", "version", "app_version", "snapshot_period", "created_at", "files"}
)
LEGACY_MANIFEST_KEYS = frozenset({"format", "version", "created_at", "files"})
MANIFEST_RECORD_KEYS = frozenset({"path", "sha256"})
SNAPSHOT_PERIOD_KEYS = frozenset({"year", "quarter"})
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
MAX_MANIFEST_SIZE = 16 * 1024 * 1024
RESTORE_COMPONENTS = ("database", *INCLUDED_DIRECTORIES)
RESTORE_PREPARE_PREFIX = "restore_prepare_"
RESTORE_APPLY_PREFIX = "restore_apply_"
RESTORE_APPLY_JOURNAL = "journal.json"
SUPPORTED_DATABASE_REVISIONS = frozenset(
    {
        "b3c4b22cde0d",
        "d8f42c0b7a11",
        "e91f7c6a2b40",
        "f2a8c7d41e90",
        "a6d1f4c28b73",
        "c4b7f1d92e60",
        "9d2f6a8c4b13",
        "7f3c2a91b6e4",
        "c1a7d5e9b402",
        "d4f8a1c73b29",
        "e8b2c6d91a04",
    }
)
OLD_HEAD_TRIGGER_NAMES = frozenset(
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
    }
)
NEW_HEAD_TRIGGER_NAMES = frozenset(
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
LATEST_HEAD_TRIGGER_NAMES = NEW_HEAD_TRIGGER_NAMES | DELETE_GUARD_TRIGGER_NAMES
REQUIRED_DATABASE_COLUMNS = {
    "companies": {"id", "name"},
    "clients": {"id", "company_id", "fc_id", "name"},
    "sub_accounts": {"id", "client_id", "account_number"},
    "quarterly_settlements": {"id", "client_id", "platform_id", "fee_plan_id", "year", "quarter", "status"},
    "app_settings": {"key", "value"},
}
ID_HIGH_WATER_TABLES = (
    ("id_high_water.clients", "clients"),
    ("id_high_water.sub_accounts", "sub_accounts"),
    ("id_high_water.statement_imports", "statement_imports"),
    ("id_high_water.balance_snapshots", "balance_snapshots"),
)
_CANONICAL_NON_NEGATIVE_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")
_SQLITE_MAX_ROW_ID = (1 << 63) - 1
_LEGACY_REUSE_MARKER = "legacy_entity_id_reused_before_0_2_15"
_LEGACY_REUSE_REVISION_FIELD = "legacy_reuse_disambiguation_revision"
_LEGACY_REUSE_CORRECTION_FIELD = "legacy_reuse_correction_audit_id"
_LEGACY_REUSE_REVISION = "c1a7d5e9b402"
_LEGACY_REUSE_CORRECTION_ACTION = "LEGACY_STATEMENT_DELETE_ID_REUSE_DISAMBIGUATED"
_LEGACY_REUSE_PROOF = "historical_delete_created_before_reused_statement"
CURRENT_DATABASE_REVISION = "e8b2c6d91a04"
PATH_REBASE_TRIGGER_NAMES = (
    "trg_attachment_update_block_finalized_evidence",
    "trg_attachment_update_block_payment_evidence",
    "trg_statement_import_update_block_finalized_evidence",
)


def _normalized_index_where(sql: str) -> str:
    normalized = re.sub(r"\s+", "", sql.upper())
    normalized = normalized.replace('"', "").replace("`", "").replace("[", "").replace("]", "")
    return normalized.split("WHERE", 1)[1].rstrip(";") if "WHERE" in normalized else ""


def _require_named_partial_unique_index(
    connection: sqlite3.Connection,
    table_name: str,
    index_name: str,
    columns: tuple[str, ...],
    where_sql: str,
) -> None:
    index_row = next(
        (
            row
            for row in connection.execute(f'PRAGMA index_list("{table_name}")').fetchall()
            if str(row[1]) == index_name
        ),
        None,
    )
    actual_columns = (
        tuple(
            str(row[2])
            for row in connection.execute(f'PRAGMA index_info("{index_name}")').fetchall()
        )
        if index_row is not None
        else ()
    )
    sql_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index_name,),
    ).fetchone()
    if (
        index_row is None
        or len(index_row) < 5
        or not bool(index_row[2])
        or str(index_row[3]) != "c"
        or not bool(index_row[4])
        or actual_columns != columns
        or sql_row is None
        or _normalized_index_where(str(sql_row[0] or ""))
        != _normalized_index_where(f"WHERE {where_sql}")
    ):
        raise ValueError("备份数据库结构不兼容")


def _require_nonpartial_unique_columns(
    connection: sqlite3.Connection,
    table_name: str,
    required_columns: set[tuple[str, ...]],
) -> None:
    actual: set[tuple[str, ...]] = set()
    for row in connection.execute(f'PRAGMA index_list("{table_name}")').fetchall():
        if not bool(row[2]) or (len(row) >= 5 and bool(row[4])):
            continue
        actual.add(
            tuple(
                str(info[2])
                for info in connection.execute(f'PRAGMA index_info("{row[1]}")').fetchall()
            )
        )
    if not required_columns.issubset(actual):
        raise ValueError("备份数据库结构不兼容")


def _stored_path_parts(stored_path: str, label: str = "付款凭证") -> tuple[str, ...]:
    windows_path = PureWindowsPath(stored_path)
    posix_path = PurePosixPath(stored_path)
    if windows_path.is_absolute() or windows_path.drive:
        parts = windows_path.parts
    elif posix_path.is_absolute():
        parts = posix_path.parts
    else:
        raise ValueError(f"备份中的{label}路径无效")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"备份中的{label}路径无效")
    return tuple(str(part) for part in parts)


def _archived_attachment_path(
    stored_path: str,
    records: dict[str, str],
    source_attachments_root: Path | None,
) -> str:
    if source_attachments_root is not None:
        stored = Path(stored_path)
        source_root = source_attachments_root.resolve()
        try:
            relative = stored.resolve().relative_to(source_root)
        except (OSError, ValueError) as exc:
            raise ValueError("付款凭证不在受控attachments目录") from exc
        candidate = _validate_relative_path(
            (PurePosixPath("attachments") / PurePosixPath(relative.as_posix())).as_posix(),
            "付款凭证",
        )
        if candidate not in records:
            raise ValueError("备份缺少数据库引用的付款凭证")
        return candidate

    stored_parts = tuple(part.casefold() for part in _stored_path_parts(stored_path))
    matches: list[str] = []
    for relative_path in records:
        archive_parts = PurePosixPath(relative_path).parts
        if not archive_parts or archive_parts[0].casefold() != "attachments":
            continue
        folded = tuple(part.casefold() for part in archive_parts)
        if len(stored_parts) >= len(folded) and stored_parts[-len(folded) :] == folded:
            matches.append(relative_path)
    if len(matches) != 1:
        raise ValueError("备份中的付款凭证路径无法唯一映射到attachments")
    return matches[0]


def _archived_statement_path(
    stored_path: str,
    records: dict[str, str],
    source_statement_root: Path | None,
) -> str:
    if source_statement_root is not None:
        stored = Path(stored_path)
        source_root = source_statement_root.resolve()
        try:
            relative = stored.resolve().relative_to(source_root)
        except (OSError, ValueError) as exc:
            raise ValueError("账单原件不在受控statement_imports目录") from exc
        candidate = _validate_relative_path(
            (PurePosixPath("statement_imports") / PurePosixPath(relative.as_posix())).as_posix(),
            "账单原件",
        )
        if candidate not in records:
            raise ValueError("备份缺少数据库引用的账单原件")
        return candidate

    stored_parts = tuple(
        part.casefold() for part in _stored_path_parts(stored_path, "账单原件")
    )
    matches: list[str] = []
    for relative_path in records:
        archive_parts = PurePosixPath(relative_path).parts
        if not archive_parts or archive_parts[0].casefold() != "statement_imports":
            continue
        folded = tuple(part.casefold() for part in archive_parts)
        if len(stored_parts) >= len(folded) and stored_parts[-len(folded) :] == folded:
            matches.append(relative_path)
    if len(matches) != 1:
        raise ValueError("备份中的账单原件路径无法唯一映射到statement_imports")
    return matches[0]


def _archived_output_path(stored_path: str, records: dict[str, str]) -> str:
    stored_parts = tuple(
        part.casefold() for part in _stored_path_parts(stored_path, "导出文件")
    )
    matches: list[str] = []
    for relative_path in records:
        archive_parts = PurePosixPath(relative_path).parts
        if not archive_parts or archive_parts[0].casefold() != "output":
            continue
        folded = tuple(part.casefold() for part in archive_parts)
        if len(stored_parts) >= len(folded) and stored_parts[-len(folded) :] == folded:
            matches.append(relative_path)
    if len(matches) != 1:
        raise ValueError("数据包中的导出文件路径无法唯一映射到output")
    return matches[0]


def _validate_statement_import_archive(
    database_path: Path,
    archive_root: Path,
    records: dict[str, str],
    *,
    source_statement_root: Path | None = None,
) -> None:
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    try:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'statement_imports'"
        ).fetchone()
        rows = (
            connection.execute(
                "SELECT id, stored_path, sha256 FROM statement_imports ORDER BY id"
            ).fetchall()
            if table_exists is not None
            else []
        )
    finally:
        connection.close()

    archived_statement_paths = {
        relative_path
        for relative_path in records
        if PurePosixPath(relative_path).parts
        and PurePosixPath(relative_path).parts[0].casefold() == "statement_imports"
    }
    referenced_paths: set[str] = set()
    for import_id, stored, sha in rows:
        if (
            not isinstance(import_id, int)
            or not isinstance(stored, str)
            or not isinstance(sha, str)
            or SHA256_PATTERN.fullmatch(sha.casefold()) is None
        ):
            raise ValueError("备份中的账单原件关系或哈希元数据无效")
        relative_path = _archived_statement_path(
            stored,
            records,
            source_statement_root,
        )
        if relative_path in referenced_paths:
            raise ValueError("备份中的多条账单记录共用同一原件路径")
        referenced_paths.add(relative_path)
        archived_path = archive_root.joinpath(*PurePosixPath(relative_path).parts)
        _require_within_root(archived_path, archive_root, "账单原件")
        if (
            archived_path.is_symlink()
            or archived_path.is_junction()
            or not archived_path.is_file()
        ):
            raise ValueError("备份缺少有效的账单原件文件")
        try:
            actual_size = archived_path.stat().st_size
            actual_sha = sha256_file(archived_path)
        except OSError as exc:
            raise ValueError("备份中的账单原件无法读取") from exc
        if (
            actual_size <= 0
            or actual_sha.casefold() != sha.casefold()
            or records.get(relative_path, "").casefold() != sha.casefold()
        ):
            raise ValueError("备份中的账单原件校验失败")

    if referenced_paths != archived_statement_paths:
        raise ValueError("备份中的statement_imports文件与数据库记录不一致")


def _validate_payment_proof_archive(
    database_path: Path,
    archive_root: Path,
    records: dict[str, str],
    *,
    source_attachments_root: Path | None = None,
) -> None:
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    try:
        table_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        proof_rows: list[
            tuple[str, int, object, object, object, object, object, object]
        ] = []
        if {"payments", "attachments"}.issubset(table_names):
            proof_rows.extend(
                (
                    "PAYMENT",
                    int(row[0]),
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                )
                for row in connection.execute(
                    """
                    SELECT payment.id, proof.id, proof.entity_type, proof.entity_id,
                           proof.stored_path, proof.size_bytes, proof.sha256
                    FROM payments AS payment
                    LEFT JOIN attachments AS proof
                      ON proof.id = payment.proof_attachment_id
                    ORDER BY payment.id
                    """
                ).fetchall()
            )
        if {"payment_refunds", "attachments"}.issubset(table_names):
            proof_rows.extend(
                (
                    "PAYMENT_REFUND",
                    int(row[0]),
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[6],
                )
                for row in connection.execute(
                    """
                    SELECT refund.id, proof.id, proof.entity_type, proof.entity_id,
                           proof.stored_path, proof.size_bytes, proof.sha256
                    FROM payment_refunds AS refund
                    LEFT JOIN attachments AS proof
                      ON proof.id = refund.proof_attachment_id
                    ORDER BY refund.id
                    """
                ).fetchall()
            )
    finally:
        connection.close()

    for (
        expected_type,
        entity_id,
        proof_id,
        entity_type,
        proof_entity_id,
        stored,
        size,
        sha,
    ) in proof_rows:
        if (
            proof_id is None
            or entity_type != expected_type
            or proof_entity_id != entity_id
            or not isinstance(stored, str)
            or not isinstance(size, int)
            or size <= 0
            or not isinstance(sha, str)
            or SHA256_PATTERN.fullmatch(sha.casefold()) is None
        ):
            raise ValueError("备份中的付款凭证关系或元数据无效")
        relative_path = _archived_attachment_path(
            stored, records, source_attachments_root
        )
        archived_path = archive_root.joinpath(*PurePosixPath(relative_path).parts)
        _require_within_root(archived_path, archive_root, "付款凭证")
        if (
            archived_path.is_symlink()
            or archived_path.is_junction()
            or not archived_path.is_file()
        ):
            raise ValueError("备份缺少有效的付款凭证文件")
        try:
            actual_size = archived_path.stat().st_size
            actual_sha = sha256_file(archived_path)
        except OSError as exc:
            raise ValueError("备份中的付款凭证文件无法读取") from exc
        if (
            actual_size != size
            or actual_sha.casefold() != sha.casefold()
            or records.get(relative_path, "").casefold() != sha.casefold()
        ):
            raise ValueError("备份中的付款凭证文件校验失败")


def _validate_data_package_file_references(
    database_path: Path,
    archive_root: Path,
    records: dict[str, str],
) -> None:
    """Require every persisted file path to be portable and physically complete."""

    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
    )
    try:
        attachment_rows = connection.execute(
            "SELECT id, stored_path, size_bytes, sha256 FROM attachments ORDER BY id"
        ).fetchall()
        export_rows = connection.execute(
            "SELECT id, stored_path, sha256 FROM export_records ORDER BY id"
        ).fetchall()
        invoice_rows = connection.execute(
            "SELECT id, pdf_paths_json FROM invoices WHERE pdf_paths_json IS NOT NULL ORDER BY id"
        ).fetchall()
    finally:
        connection.close()

    attachment_paths: set[str] = set()
    for attachment_id, stored, size, sha in attachment_rows:
        if (
            not isinstance(attachment_id, int)
            or not isinstance(stored, str)
            or not isinstance(size, int)
            or size <= 0
            or not isinstance(sha, str)
            or SHA256_PATTERN.fullmatch(sha.casefold()) is None
        ):
            raise ValueError("数据包中的附件路径或哈希元数据无效")
        relative_path = _archived_attachment_path(stored, records, None)
        if relative_path in attachment_paths:
            raise ValueError("数据包中的多条附件记录共用同一文件路径")
        attachment_paths.add(relative_path)
        archived_path = archive_root.joinpath(*PurePosixPath(relative_path).parts)
        _require_within_root(archived_path, archive_root, "附件")
        if (
            archived_path.is_symlink()
            or archived_path.is_junction()
            or not archived_path.is_file()
        ):
            raise ValueError("数据包缺少有效的附件文件")
        try:
            actual_size = archived_path.stat().st_size
            actual_sha = sha256_file(archived_path)
        except OSError as exc:
            raise ValueError("数据包中的附件文件无法读取") from exc
        if (
            actual_size != size
            or actual_sha.casefold() != sha.casefold()
            or records.get(relative_path, "").casefold() != sha.casefold()
        ):
            raise ValueError("数据包中的附件文件校验失败")

    for export_id, stored, sha in export_rows:
        if (
            not isinstance(export_id, int)
            or not isinstance(stored, str)
            or not isinstance(sha, str)
            or SHA256_PATTERN.fullmatch(sha.casefold()) is None
        ):
            raise ValueError("数据包中的导出文件路径或哈希元数据无效")
        relative_path = _archived_output_path(stored, records)
        archived_path = archive_root.joinpath(*PurePosixPath(relative_path).parts)
        _require_within_root(archived_path, archive_root, "导出文件")
        if (
            archived_path.is_symlink()
            or archived_path.is_junction()
            or not archived_path.is_file()
        ):
            raise ValueError("数据包缺少有效的导出文件")
        try:
            actual_sha = sha256_file(archived_path)
        except OSError as exc:
            raise ValueError("数据包中的导出文件无法读取") from exc
        if (
            actual_sha.casefold() != sha.casefold()
            or records.get(relative_path, "").casefold() != sha.casefold()
        ):
            raise ValueError("数据包中的导出文件校验失败")

    for invoice_id, raw_paths in invoice_rows:
        try:
            paths = json.loads(raw_paths, object_pairs_hook=_json_object_with_unique_keys)
        except (TypeError, ValueError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
            raise ValueError(f"数据包中的Invoice #{invoice_id} PDF路径无效") from exc
        if not isinstance(paths, dict) or any(
            not isinstance(language, str)
            or not language
            or not isinstance(stored, str)
            or not stored
            for language, stored in paths.items()
        ):
            raise ValueError(f"数据包中的Invoice #{invoice_id} PDF路径无效")
        for stored in paths.values():
            relative_path = _archived_output_path(stored, records)
            archived_path = archive_root.joinpath(*PurePosixPath(relative_path).parts)
            _require_within_root(archived_path, archive_root, "Invoice PDF")
            if (
                archived_path.is_symlink()
                or archived_path.is_junction()
                or not archived_path.is_file()
            ):
                raise ValueError(f"数据包缺少Invoice #{invoice_id} PDF文件")


class _DuplicateJsonKeyError(ValueError):
    pass


@dataclass(frozen=True)
class RestoreStageResult:
    marker: Path
    cleanup_warning: str | None = None


def create_backup(snapshot_year: int | None = None, snapshot_quarter: int | None = None) -> Path:
    settings = get_settings()
    now = datetime.now()
    snapshot_year = now.year if snapshot_year is None else snapshot_year
    snapshot_quarter = ((now.month - 1) // 3 + 1) if snapshot_quarter is None else snapshot_quarter
    if (
        isinstance(snapshot_year, bool)
        or not isinstance(snapshot_year, int)
        or not 2000 <= snapshot_year <= 2100
        or isinstance(snapshot_quarter, bool)
        or not isinstance(snapshot_quarter, int)
        or not 1 <= snapshot_quarter <= 4
    ):
        raise ValueError("数据包检查批次必须是2000至2100年及Q1至Q4")
    database_directory = settings.database_path.parent
    if (
        database_directory.is_symlink()
        or database_directory.is_junction()
        or settings.database_path.is_symlink()
        or settings.database_path.is_junction()
    ):
        raise ValueError("数据库备份来源不能是链接或联接目录")
    if not settings.database_path.is_file():
        raise ValueError("数据库文件不存在，无法创建备份")
    backup_directory = settings.data_root / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%Y%m%d_%H%M%S_%f")
    with tempfile.NamedTemporaryFile(
        dir=backup_directory,
        prefix=f".financial_system_data_package_{snapshot_year}_Q{snapshot_quarter}_{timestamp}_",
        suffix=".zip.tmp",
        delete=False,
    ) as temporary_file:
        temporary_output_path = Path(temporary_file.name)
    output_path = backup_directory / temporary_output_path.name[1:].removesuffix(".tmp")

    try:
        with tempfile.TemporaryDirectory(dir=settings.data_root / "tmp") as tmp_name:
            tmp = Path(tmp_name)
            db_copy = tmp / "database" / settings.database_path.name
            db_copy.parent.mkdir(parents=True, exist_ok=True)
            source_connection = sqlite3.connect(settings.database_path)
            target_connection = sqlite3.connect(db_copy)
            try:
                source_connection.backup(target_connection)
            finally:
                target_connection.close()
                source_connection.close()
            _validate_sqlite_database(db_copy)

            files: list[dict] = []
            for file_path in [db_copy]:
                files.append(
                    {
                        "path": file_path.relative_to(tmp).as_posix(),
                        "sha256": sha256_file(file_path),
                    }
                )
            for directory_name in INCLUDED_DIRECTORIES:
                directory = settings.data_root / directory_name
                if directory.is_symlink() or directory.is_junction():
                    raise ValueError(f"备份来源目录包含链接：{directory_name}")
                if not directory.exists():
                    continue
                for file_path in directory.rglob("*"):
                    if file_path.is_symlink() or file_path.is_junction():
                        raise ValueError(
                            f"备份来源目录包含链接：{file_path.relative_to(settings.data_root)}"
                        )
                    if file_path.is_file():
                        relative = file_path.relative_to(settings.data_root)
                        destination = tmp / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(file_path, destination)
                        files.append(
                            {"path": relative.as_posix(), "sha256": sha256_file(destination)}
                        )

            archive_records = {str(record["path"]): str(record["sha256"]) for record in files}
            _validate_payment_proof_archive(
                db_copy,
                tmp,
                archive_records,
                source_attachments_root=settings.data_root / "attachments",
            )
            _validate_statement_import_archive(
                db_copy,
                tmp,
                archive_records,
                source_statement_root=settings.data_root / "statement_imports",
            )

            manifest = {
                "format": BACKUP_FORMAT,
                "version": BACKUP_VERSION,
                "app_version": APP_VERSION,
                "snapshot_period": {
                    "year": snapshot_year,
                    "quarter": snapshot_quarter,
                },
                "created_at": now.isoformat(),
                "files": files,
            }
            (tmp / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with zipfile.ZipFile(
                temporary_output_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for file_path in tmp.rglob("*"):
                    if file_path.is_file():
                        archive.write(file_path, file_path.relative_to(tmp).as_posix())

            # Do not publish a backup filename until the exact ZIP which will
            # be returned has passed the same strict restore validation.
            validate_backup(temporary_output_path, tmp / "validation")

        # The source and destination are in the same directory.  ``rename`` is
        # atomic and, unlike ``replace``, refuses to overwrite an unexpected
        # collision on Windows.
        temporary_output_path.rename(output_path)
        return output_path
    finally:
        temporary_output_path.unlink(missing_ok=True)


def validate_backup(backup_path: Path, extract_root: Path) -> dict:
    """Safely extract and validate a backup archive into an empty directory."""

    if extract_root.is_symlink() or extract_root.is_junction():
        raise ValueError("备份解压目录不安全")
    extract_root = extract_root.resolve()
    if extract_root.exists():
        if not extract_root.is_dir():
            raise ValueError("备份解压目录不安全")
        if any(extract_root.iterdir()):
            raise ValueError("备份解压目录必须为空")
    else:
        extract_root.mkdir(parents=True)

    try:
        with zipfile.ZipFile(backup_path) as archive:
            members: list[tuple[zipfile.ZipInfo, str, bool]] = []
            member_kinds: dict[str, bool] = {}
            for member in archive.infolist():
                is_directory = member.is_dir()
                raw_path = member.filename
                if is_directory:
                    if not raw_path.endswith("/") or raw_path[:-1].endswith("/"):
                        raise ValueError("备份文件包含不规范路径")
                    raw_path = raw_path[:-1]
                relative_path = _validate_relative_path(raw_path, "备份文件")
                identity = relative_path.casefold()
                if identity in member_kinds:
                    raise ValueError(f"备份文件包含重复路径：{relative_path}")
                _validate_zip_member_type(member, is_directory)
                member_kinds[identity] = is_directory
                members.append((member, relative_path, is_directory))

            archive_files = {path: member for member, path, is_directory in members if not is_directory}
            file_path_identities = {path.casefold() for path in archive_files}
            for _, path, is_directory in members:
                if is_directory and path.casefold() in file_path_identities:
                    raise ValueError(f"备份文件路径类型冲突：{path}")
                parent = PurePosixPath(path).parent
                while parent != PurePosixPath("."):
                    if parent.as_posix().casefold() in file_path_identities:
                        raise ValueError(f"备份文件路径类型冲突：{path}")
                    parent = parent.parent

            manifest_member = archive_files.get("manifest.json")
            if manifest_member is None:
                raise ValueError("备份缺少manifest.json")
            if manifest_member.file_size > MAX_MANIFEST_SIZE:
                raise ValueError("备份manifest.json过大")
            manifest = _parse_manifest_bytes(archive.read(manifest_member))
            records = _validate_manifest(manifest)
            archive_payload_paths = set(archive_files) - {"manifest.json"}
            if archive_payload_paths != set(records):
                raise ValueError("备份文件集合与清单不一致")

            for member, relative_path, is_directory in members:
                destination = extract_root.joinpath(*PurePosixPath(relative_path).parts)
                _require_within_root(destination, extract_root, "备份文件")
                if is_directory:
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with archive.open(member) as source, destination.open("xb") as target:
                        shutil.copyfileobj(source, target)
                except (OSError, RuntimeError) as exc:
                    raise ValueError(f"无法读取备份文件：{relative_path}") from exc
    except zipfile.BadZipFile as exc:
        raise ValueError("备份文件不是有效的ZIP归档") from exc
    return validate_backup_archive_root(extract_root)


def validate_backup_archive_root(root: Path) -> dict:
    """Validate one extracted backup tree and return its manifest."""

    if root.is_symlink() or root.is_junction():
        raise ValueError("备份目录包含链接")
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("备份目录不存在")
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("备份缺少manifest.json")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise ValueError("备份manifest.json无效") from exc
    if len(manifest_bytes) > MAX_MANIFEST_SIZE:
        raise ValueError("备份manifest.json过大")
    manifest = _parse_manifest_bytes(manifest_bytes)

    records = _validate_manifest(manifest)
    actual_files: dict[str, str] = {}
    for entry in root.rglob("*"):
        if entry.is_symlink() or entry.is_junction():
            raise ValueError("备份目录包含链接")
        if entry.is_dir():
            continue
        if not entry.is_file():
            raise ValueError("备份目录包含非普通文件")
        relative_path = entry.relative_to(root).as_posix()
        if relative_path == "manifest.json":
            continue
        identity = relative_path.casefold()
        if identity in actual_files:
            raise ValueError(f"备份目录包含重复路径：{relative_path}")
        actual_files[identity] = relative_path

    expected_file_paths = set(records)
    actual_file_paths = set(actual_files.values())
    if actual_file_paths != expected_file_paths:
        missing = sorted(expected_file_paths - actual_file_paths)
        extra = sorted(actual_file_paths - expected_file_paths)
        details: list[str] = []
        if missing:
            details.append(f"缺少文件：{', '.join(missing)}")
        if extra:
            details.append(f"未列入清单：{', '.join(extra)}")
        raise ValueError(f"备份文件集合与清单不一致（{'；'.join(details)}）")

    for relative_path, expected_hash in records.items():
        file_path = root.joinpath(*PurePosixPath(relative_path).parts)
        _require_within_root(file_path, root, "备份清单")
        if sha256_file(file_path) != expected_hash:
            raise ValueError(f"备份校验失败：{relative_path}")

    settings = get_settings()
    database_relative_path = f"database/{settings.database_path.name}"
    database_path = root.joinpath(*PurePosixPath(database_relative_path).parts)
    _validate_sqlite_database(database_path)
    _validate_payment_proof_archive(database_path, root, records)
    _validate_statement_import_archive(database_path, root, records)
    if manifest["format"] == BACKUP_FORMAT:
        if _sqlite_database_revision(database_path) != CURRENT_DATABASE_REVISION:
            raise ValueError("数据包数据库版本与当前系统不一致")
        _validate_data_package_file_references(database_path, root, records)
    return manifest


def stage_restore(backup_path: Path) -> RestoreStageResult:
    settings = get_settings()
    staging_base = _validated_restore_staging_base(settings.data_root)
    marker = settings.data_root / "pending_restore.json"
    previous_pending_root = _read_existing_pending_restore_root(marker, staging_base)
    pending_root = Path(tempfile.mkdtemp(prefix="pending_restore_", dir=staging_base))
    try:
        validate_backup(backup_path, pending_root)
        _write_marker_atomically(
            marker,
            {"path": str(pending_root), "staged_at": datetime.now().isoformat()},
        )
    except Exception:
        shutil.rmtree(pending_root, ignore_errors=True)
        raise
    cleanup_warning = None
    if previous_pending_root is not None and previous_pending_root != pending_root.resolve():
        # The new marker is already durable, so an obsolete-tree cleanup error
        # must be reported as a warning rather than turning a successful stage
        # into the contradictory state "request failed, restore scheduled".
        try:
            _remove_obsolete_pending_root(previous_pending_root)
        except OSError as exc:
            cleanup_warning = f"新备份已安全暂存；旧暂存目录清理失败：{previous_pending_root}（{exc}）"
    return RestoreStageResult(marker=marker, cleanup_warning=cleanup_warning)


def _rebased_live_path(data_root: Path, relative_path: str, label: str) -> str:
    validated_relative = _validate_relative_path(relative_path, label)
    target = data_root.joinpath(*PurePosixPath(validated_relative).parts)
    _require_within_root(target, data_root, label)
    value = str(target)
    if len(value) > 600:
        raise ValueError(f"数据包导入后的{label}路径过长")
    return value


def _rebase_data_package_database(
    archive_root: Path,
    data_root: Path,
    manifest: dict,
) -> None:
    """Rewrite only physical file locations for the receiving computer."""

    if manifest.get("format") != BACKUP_FORMAT:
        return
    records = _validate_manifest(manifest)
    settings = get_settings()
    database_relative_path = f"database/{settings.database_path.name}"
    database_path = archive_root.joinpath(*PurePosixPath(database_relative_path).parts)
    if _sqlite_database_revision(database_path) != CURRENT_DATABASE_REVISION:
        raise ValueError("数据包数据库版本与当前系统不一致")

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        statement_updates: list[tuple[str, int]] = []
        for row_id, stored in connection.execute(
            "SELECT id, stored_path FROM statement_imports ORDER BY id"
        ).fetchall():
            new_path = _rebased_live_path(
                data_root,
                _archived_statement_path(str(stored), records, None),
                "账单原件",
            )
            if new_path != stored:
                statement_updates.append((new_path, int(row_id)))

        attachment_updates: list[tuple[str, int]] = []
        for row_id, stored in connection.execute(
            "SELECT id, stored_path FROM attachments ORDER BY id"
        ).fetchall():
            new_path = _rebased_live_path(
                data_root,
                _archived_attachment_path(str(stored), records, None),
                "附件",
            )
            if new_path != stored:
                attachment_updates.append((new_path, int(row_id)))

        export_updates: list[tuple[str, int]] = []
        for row_id, stored in connection.execute(
            "SELECT id, stored_path FROM export_records ORDER BY id"
        ).fetchall():
            new_path = _rebased_live_path(
                data_root,
                _archived_output_path(str(stored), records),
                "导出文件",
            )
            if new_path != stored:
                export_updates.append((new_path, int(row_id)))
        invoice_updates: list[tuple[str, int]] = []
        for invoice_id, raw_paths in connection.execute(
            "SELECT id, pdf_paths_json FROM invoices WHERE pdf_paths_json IS NOT NULL ORDER BY id"
        ).fetchall():
            try:
                paths = json.loads(raw_paths, object_pairs_hook=_json_object_with_unique_keys)
            except (TypeError, ValueError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
                raise ValueError(f"数据包中的Invoice #{invoice_id} PDF路径无效") from exc
            if not isinstance(paths, dict) or any(
                not isinstance(language, str)
                or not language
                or not isinstance(stored, str)
                or not stored
                for language, stored in paths.items()
            ):
                raise ValueError(f"数据包中的Invoice #{invoice_id} PDF路径无效")
            rebased_paths = {
                language: _rebased_live_path(
                    data_root,
                    _archived_output_path(stored, records),
                    "Invoice PDF",
                )
                for language, stored in paths.items()
            }
            if rebased_paths != paths:
                invoice_updates.append(
                    (json.dumps(rebased_paths, ensure_ascii=False), int(invoice_id))
                )

        needs_protected_update = bool(statement_updates or attachment_updates)
        trigger_sql: dict[str, str] = {}
        if needs_protected_update:
            trigger_sql = {
                str(name): str(sql or "")
                for name, sql in connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    f"AND name IN ({','.join('?' for _ in PATH_REBASE_TRIGGER_NAMES)})",
                    PATH_REBASE_TRIGGER_NAMES,
                ).fetchall()
            }
            if set(trigger_sql) != set(PATH_REBASE_TRIGGER_NAMES) or any(
                not trigger_sql[name].strip() for name in PATH_REBASE_TRIGGER_NAMES
            ):
                raise ValueError("数据包数据库缺少路径重定向所需的完整保护规则")

        connection.execute("BEGIN IMMEDIATE")
        try:
            for name in PATH_REBASE_TRIGGER_NAMES if needs_protected_update else ():
                connection.execute(f'DROP TRIGGER "{name}"')
            connection.executemany(
                "UPDATE statement_imports SET stored_path = ? WHERE id = ?",
                statement_updates,
            )
            connection.executemany(
                "UPDATE attachments SET stored_path = ? WHERE id = ?",
                attachment_updates,
            )
            connection.executemany(
                "UPDATE export_records SET stored_path = ? WHERE id = ?",
                export_updates,
            )
            connection.executemany(
                "UPDATE invoices SET pdf_paths_json = ? WHERE id = ?",
                invoice_updates,
            )
            for name in PATH_REBASE_TRIGGER_NAMES if needs_protected_update else ():
                connection.execute(trigger_sql[name])
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()

    database_record = next(
        record for record in manifest["files"] if record["path"] == database_relative_path
    )
    database_record["sha256"] = sha256_file(database_path)
    (archive_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def apply_pending_restore() -> bool:
    settings = get_settings()
    _recover_interrupted_restore_transactions(settings.data_root)
    _cleanup_unreferenced_restore_artifacts(settings.data_root)
    marker = settings.data_root / "pending_restore.json"
    if not marker.exists():
        return False
    payload = _read_pending_restore_payload(marker)
    pending_root = _validate_pending_restore_root(Path(payload["path"]), settings.data_root / "tmp")
    manifest = validate_backup_archive_root(pending_root)
    transaction_root = _prepare_restore_transaction(
        pending_root, settings.data_root, manifest
    )
    try:
        for component in RESTORE_COMPONENTS:
            _activate_restore_component(component, settings.data_root, transaction_root)
        _write_restore_transaction_journal(transaction_root, settings.data_root, phase="committed")
    except Exception:
        try:
            _write_restore_transaction_journal(transaction_root, settings.data_root, phase="rolling_back")
            _rollback_restore_transaction(transaction_root, settings.data_root)
            _remove_restore_transaction_root(transaction_root)
        except Exception as rollback_exc:
            raise RuntimeError(
                "恢复切换失败且自动回滚未完成；待恢复标记与事务目录已保留，请勿手工删除"
            ) from rollback_exc
        raise

    try:
        _finish_committed_restore_transaction(transaction_root, settings.data_root, marker)
    except OSError as exc:
        warnings.warn(
            f"备份已完整恢复，但提交后的临时文件尚未清理（下次启动会重试）：{exc}",
            RuntimeWarning,
            stacklevel=2,
        )
    return True


def _prepare_restore_transaction(
    pending_root: Path,
    data_root: Path,
    manifest: dict,
) -> Path:
    staging_base = _validated_restore_staging_base(data_root)
    transaction_root = Path(tempfile.mkdtemp(prefix=RESTORE_PREPARE_PREFIX, dir=staging_base))
    try:
        new_root = transaction_root / "new"
        (transaction_root / "old").mkdir()
        (transaction_root / "discard").mkdir()
        _write_restore_transaction_journal(
            transaction_root,
            data_root,
            phase="preparing",
            pending_root=pending_root,
        )
        for component in RESTORE_COMPONENTS:
            source = pending_root / component
            destination = new_root / component
            if source.exists():
                shutil.copytree(source, destination)
            else:
                destination.mkdir(parents=True)
        shutil.copy2(pending_root / "manifest.json", new_root / "manifest.json")
        _rebase_data_package_database(new_root, data_root, manifest)
        validate_backup_archive_root(new_root)
        apply_root = staging_base / transaction_root.name.replace(
            RESTORE_PREPARE_PREFIX,
            RESTORE_APPLY_PREFIX,
            1,
        )
        transaction_root.replace(apply_root)
        transaction_root = apply_root
        _write_restore_transaction_journal(transaction_root, data_root, phase="applying")
    except Exception:
        shutil.rmtree(transaction_root, ignore_errors=True)
        raise
    return transaction_root


def _write_restore_transaction_journal(
    transaction_root: Path,
    data_root: Path,
    *,
    phase: str,
    pending_root: Path | None = None,
) -> None:
    if phase not in {"preparing", "applying", "rolling_back", "committed"}:
        raise ValueError("恢复事务阶段无效")
    journal_path = transaction_root / RESTORE_APPLY_JOURNAL
    if journal_path.exists():
        existing = _read_restore_transaction_journal(transaction_root, data_root)
        original_exists = existing["original_exists"]
        validated_pending_root = existing["pending_root"]
    else:
        if pending_root is None:
            raise ValueError("恢复事务缺少待恢复目录")
        original_exists = {
            component: (data_root / component).exists()
            for component in RESTORE_COMPONENTS
        }
        validated_pending_root = str(
            _validate_pending_restore_root(pending_root, data_root / "tmp")
        )
    payload = {
        "phase": phase,
        "original_exists": original_exists,
        "pending_root": validated_pending_root,
    }
    _write_marker_atomically(journal_path, payload)


def _activate_restore_component(component: str, data_root: Path, transaction_root: Path) -> None:
    live = data_root / component
    replacement = transaction_root / "new" / component
    previous = transaction_root / "old" / component
    _validate_live_restore_component(live)
    if live.exists():
        _replace_restore_directory(live, previous)
    try:
        _replace_restore_directory(replacement, live)
    except Exception:
        if previous.exists() and not live.exists():
            _replace_restore_directory(previous, live)
        raise


def _rollback_restore_transaction(transaction_root: Path, data_root: Path) -> None:
    journal = _read_restore_transaction_journal(transaction_root, data_root)
    original_exists = journal["original_exists"]
    discard_root = transaction_root / "discard"
    discard_root.mkdir(exist_ok=True)
    for component in reversed(RESTORE_COMPONENTS):
        live = data_root / component
        replacement = transaction_root / "new" / component
        previous = transaction_root / "old" / component
        discard = discard_root / component
        if previous.exists():
            if live.exists():
                if discard.exists():
                    shutil.rmtree(discard)
                _replace_restore_directory(live, discard)
            _replace_restore_directory(previous, live)
        elif not original_exists[component] and live.exists() and not replacement.exists():
            if discard.exists():
                shutil.rmtree(discard)
            _replace_restore_directory(live, discard)


def _recover_interrupted_restore_transactions(data_root: Path) -> None:
    staging_base = _validated_restore_staging_base(data_root)
    for prepare_root in sorted(staging_base.glob(f"{RESTORE_PREPARE_PREFIX}*")):
        _validate_restore_transaction_root(prepare_root, staging_base)
        # A prepare-prefixed directory is never allowed to mutate live data.
        shutil.rmtree(prepare_root)
    for transaction_root in sorted(staging_base.glob(f"{RESTORE_APPLY_PREFIX}*")):
        resolved_root = _validate_restore_transaction_root(transaction_root, staging_base)
        journal_path = resolved_root / RESTORE_APPLY_JOURNAL
        if not journal_path.exists():
            if any(resolved_root.iterdir()):
                raise RuntimeError("恢复事务日志缺失，系统已停止启动")
            resolved_root.rmdir()
            continue
        journal = _read_restore_transaction_journal(resolved_root, data_root)
        if journal["phase"] == "preparing":
            _remove_restore_transaction_root(resolved_root)
            continue
        if journal["phase"] in {"applying", "rolling_back"}:
            _write_restore_transaction_journal(resolved_root, data_root, phase="rolling_back")
            _rollback_restore_transaction(resolved_root, data_root)
            _remove_restore_transaction_root(resolved_root)
            continue
        try:
            _finish_committed_restore_transaction(
                resolved_root,
                data_root,
                data_root / "pending_restore.json",
            )
        except OSError as exc:
            warnings.warn(
                f"已恢复提交状态，但临时文件清理仍待下次启动重试：{exc}",
                RuntimeWarning,
                stacklevel=2,
            )


def _read_restore_transaction_journal(transaction_root: Path, data_root: Path) -> dict:
    journal_path = transaction_root / RESTORE_APPLY_JOURNAL
    try:
        payload = json.loads(
            journal_path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_with_unique_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        raise RuntimeError("恢复事务日志无效，系统已停止启动") from exc
    if not isinstance(payload, dict) or set(payload) != {"phase", "original_exists", "pending_root"}:
        raise RuntimeError("恢复事务日志结构无效，系统已停止启动")
    original_exists = payload["original_exists"]
    if (
        payload["phase"] not in {"preparing", "applying", "rolling_back", "committed"}
        or not isinstance(original_exists, dict)
        or set(original_exists) != set(RESTORE_COMPONENTS)
        or any(type(value) is not bool for value in original_exists.values())
        or not isinstance(payload["pending_root"], str)
    ):
        raise RuntimeError("恢复事务日志结构无效，系统已停止启动")
    payload["pending_root"] = str(
        _validate_pending_restore_root(Path(payload["pending_root"]), data_root / "tmp")
    )
    return payload


def _finish_committed_restore_transaction(transaction_root: Path, data_root: Path, marker: Path) -> None:
    journal = _read_restore_transaction_journal(transaction_root, data_root)
    if journal["phase"] != "committed":
        raise RuntimeError("恢复事务尚未提交，不能清理")
    pending_root = Path(journal["pending_root"])
    if marker.exists():
        marker_payload = _read_pending_restore_payload(marker)
        marked_root = _validate_pending_restore_root(Path(marker_payload["path"]), data_root / "tmp")
        if marked_root == pending_root:
            marker.unlink()
    if pending_root.exists():
        shutil.rmtree(pending_root)
    _remove_restore_transaction_root(transaction_root)


def _replace_restore_directory(source: Path, destination: Path) -> None:
    source.replace(destination)


def _remove_restore_transaction_root(transaction_root: Path) -> None:
    journal_path = transaction_root / RESTORE_APPLY_JOURNAL
    for child in tuple(transaction_root.iterdir()):
        if child == journal_path:
            continue
        if child.is_symlink() or child.is_junction():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    journal_path.unlink(missing_ok=True)
    transaction_root.rmdir()


def _validated_restore_staging_base(data_root: Path) -> Path:
    staging_base = data_root / "tmp"
    if data_root.is_symlink() or data_root.is_junction():
        raise RuntimeError("数据根目录不能是链接或联接目录")
    if staging_base.is_symlink() or staging_base.is_junction():
        raise RuntimeError("恢复暂存目录不能是链接或联接目录")
    staging_base.mkdir(parents=True, exist_ok=True)
    if not staging_base.is_dir() or os.stat(staging_base).st_dev != os.stat(data_root).st_dev:
        raise RuntimeError("恢复暂存目录必须与数据目录位于同一磁盘")
    return staging_base.resolve()


def _cleanup_unreferenced_restore_artifacts(data_root: Path) -> None:
    staging_base = _validated_restore_staging_base(data_root)
    referenced_pending_roots: set[Path] = set()
    marker = data_root / "pending_restore.json"
    if marker.is_file():
        try:
            marker_payload = _read_pending_restore_payload(marker)
            referenced_pending_roots.add(
                _validate_pending_restore_root(Path(marker_payload["path"]), staging_base)
            )
        except ValueError:
            # An invalid marker must fail explicitly later; do not guess which
            # pending tree is safe to delete while that recovery evidence exists.
            return
    for transaction_root in staging_base.glob(f"{RESTORE_APPLY_PREFIX}*"):
        journal_path = transaction_root / RESTORE_APPLY_JOURNAL
        if journal_path.is_file():
            journal = _read_restore_transaction_journal(transaction_root.resolve(), data_root)
            referenced_pending_roots.add(Path(journal["pending_root"]))

    cleanup_errors: list[str] = []
    for pending_root in staging_base.glob("pending_restore_*"):
        resolved_root = _validate_pending_restore_root(pending_root, staging_base)
        if resolved_root in referenced_pending_roots:
            continue
        try:
            shutil.rmtree(resolved_root)
        except OSError as exc:
            cleanup_errors.append(f"{resolved_root}（{exc}）")
    for upload_path in staging_base.glob("restore_*.zip"):
        if upload_path.is_symlink() or upload_path.is_junction() or not upload_path.is_file():
            continue
        try:
            upload_path.unlink()
        except OSError as exc:
            cleanup_errors.append(f"{upload_path}（{exc}）")
    if cleanup_errors:
        warnings.warn(
            "恢复临时副本仍待后续启动重试清理：" + "；".join(cleanup_errors),
            RuntimeWarning,
            stacklevel=2,
        )


def _validate_restore_transaction_root(transaction_root: Path, staging_base: Path) -> Path:
    resolved_root = transaction_root.resolve()
    if (
        transaction_root.is_symlink()
        or transaction_root.is_junction()
        or not transaction_root.is_dir()
        or resolved_root.parent != staging_base.resolve()
    ):
        raise RuntimeError("发现不安全的恢复事务目录，系统已停止启动")
    return resolved_root


def _validate_live_restore_component(path: Path) -> None:
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir()):
        raise RuntimeError(f"恢复目标目录不安全：{path}")


def _validate_relative_path(value: object, context: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError(f"{context}包含不安全路径")
    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or value != posix_path.as_posix()
        or any(
            part in {"", ".", ".."}
            or ":" in part
            or part.rstrip(" .") != part
            or PureWindowsPath(part).is_reserved()
            or any(ord(character) < 32 or character in '<>"|?*' for character in part)
            for part in posix_path.parts
        )
    ):
        raise ValueError(f"{context}包含不安全路径")
    return posix_path.as_posix()


def _require_within_root(path: Path, root: Path, context: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{context}包含不安全路径") from exc


def _validate_zip_member_type(member: zipfile.ZipInfo, is_directory: bool) -> None:
    unix_mode = (member.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    allowed_types = {0, stat.S_IFDIR if is_directory else stat.S_IFREG}
    if file_type not in allowed_types:
        raise ValueError(f"备份文件包含非普通文件：{member.filename}")


def _validate_manifest(manifest: object) -> dict[str, str]:
    if not isinstance(manifest, dict):
        raise ValueError("备份清单结构无效")

    manifest_format = manifest.get("format")
    if manifest_format == BACKUP_FORMAT:
        if set(manifest) != MANIFEST_KEYS:
            raise ValueError("数据包清单结构无效")
        if manifest.get("app_version") != APP_VERSION:
            raise ValueError(
                f"数据包系统版本不一致：数据包为{manifest.get('app_version')}，当前系统为{APP_VERSION}"
            )
        snapshot_period = manifest.get("snapshot_period")
        if (
            not isinstance(snapshot_period, dict)
            or set(snapshot_period) != SNAPSHOT_PERIOD_KEYS
            or type(snapshot_period.get("year")) is not int
            or not 2000 <= snapshot_period["year"] <= 2100
            or type(snapshot_period.get("quarter")) is not int
            or not 1 <= snapshot_period["quarter"] <= 4
        ):
            raise ValueError("数据包检查批次无效")
    elif manifest_format == LEGACY_BACKUP_FORMAT:
        if set(manifest) != LEGACY_MANIFEST_KEYS:
            raise ValueError("备份清单结构无效")
    else:
        raise ValueError("不支持的数据包或备份格式")

    if (
        type(manifest.get("version")) is not int
        or manifest["version"] != BACKUP_VERSION
    ):
        raise ValueError("不支持的备份格式")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise ValueError("备份清单结构无效")
    try:
        datetime.fromisoformat(created_at)
    except ValueError as exc:
        raise ValueError("备份清单结构无效") from exc
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("备份清单必须包含文件")

    settings = get_settings()
    expected_database_path = f"database/{settings.database_path.name}"
    records: dict[str, str] = {}
    identities: set[str] = set()
    database_paths: list[str] = []
    allowed_roots = frozenset({"database", *INCLUDED_DIRECTORIES})
    for record in files:
        if not isinstance(record, dict) or set(record) != MANIFEST_RECORD_KEYS:
            raise ValueError("备份清单记录结构无效")
        relative_path = _validate_relative_path(record.get("path"), "备份清单")
        sha256 = record.get("sha256")
        if not isinstance(sha256, str) or SHA256_PATTERN.fullmatch(sha256) is None:
            raise ValueError("备份清单记录结构无效")
        path_parts = PurePosixPath(relative_path).parts
        if len(path_parts) < 2 or path_parts[0] not in allowed_roots or relative_path == "manifest.json":
            raise ValueError(f"备份清单包含不支持的路径：{relative_path}")
        identity = relative_path.casefold()
        if identity in identities:
            raise ValueError(f"备份清单包含重复路径：{relative_path}")
        identities.add(identity)
        records[relative_path] = sha256
        if path_parts[0] == "database":
            database_paths.append(relative_path)

    if database_paths != [expected_database_path]:
        raise ValueError(f"备份必须且只能包含数据库文件：{expected_database_path}")
    return records


def _backup_deleted_id(value: object, *, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"备份数据库删除审计ID异常：{context}")
    if value > _SQLITE_MAX_ROW_ID:
        raise ValueError(f"备份数据库删除审计ID超出SQLite范围：{context}")
    return value


def _backup_audit_details(raw_value: object, *, audit_id: int) -> dict:
    try:
        details = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"备份数据库删除审计JSON异常：#{audit_id}") from exc
    if not isinstance(details, dict):
        raise ValueError(f"备份数据库删除审计JSON异常：#{audit_id}")
    return details


def _backup_statement_deleted_id(
    *, audit_id: int, event_entity_id: object, details: dict
) -> int:
    detail_id = None
    if "deleted_import_id" in details:
        detail_id = _backup_deleted_id(
            details.get("deleted_import_id"),
            context=f"#{audit_id}.deleted_import_id",
        )
    legacy_id = None
    if event_entity_id is not None:
        legacy_id = _backup_deleted_id(
            event_entity_id,
            context=f"#{audit_id}.entity_id",
        )
    if detail_id is None and legacy_id is None:
        raise ValueError(f"备份数据库账单删除审计缺少原ID：#{audit_id}")
    if detail_id is not None and legacy_id is not None and detail_id != legacy_id:
        raise ValueError(f"备份数据库账单删除审计原ID冲突：#{audit_id}")
    return detail_id if detail_id is not None else int(legacy_id)


def _backup_legacy_reuse_disambiguated(
    connection: sqlite3.Connection,
    *,
    audit_id: int,
    action: str,
    event_entity_id: object,
    details: dict,
    deleted_id: int,
) -> bool:
    marker_present = any(
        field in details
        for field in (
            _LEGACY_REUSE_MARKER,
            _LEGACY_REUSE_REVISION_FIELD,
            _LEGACY_REUSE_CORRECTION_FIELD,
        )
    )
    if not marker_present:
        return False
    if (
        action != "STATEMENT_IMPORT_DELETED"
        or event_entity_id is not None
        or details.get(_LEGACY_REUSE_MARKER) is not True
        or details.get(_LEGACY_REUSE_REVISION_FIELD) != _LEGACY_REUSE_REVISION
    ):
        raise ValueError(f"备份数据库账单删除审计历史ID消歧标记异常：#{audit_id}")
    correction_id = _backup_deleted_id(
        details.get(_LEGACY_REUSE_CORRECTION_FIELD),
        context=f"#{audit_id}.{_LEGACY_REUSE_CORRECTION_FIELD}",
    )
    correction_rows = connection.execute(
        """
        SELECT action, entity_type, entity_id, details_json
        FROM audit_events
        WHERE id = ?
        """,
        (correction_id,),
    ).fetchall()
    if len(correction_rows) != 1:
        raise ValueError(f"备份数据库账单删除审计缺少历史ID消歧审计：#{audit_id}")
    correction_action, correction_type, correction_entity_id, raw_correction_details = (
        correction_rows[0]
    )
    correction_details = _backup_audit_details(
        raw_correction_details,
        audit_id=correction_id,
    )
    if (
        correction_action != _LEGACY_REUSE_CORRECTION_ACTION
        or correction_type != "AUDIT_EVENT"
        or correction_entity_id != audit_id
        or correction_details.get("migration_revision") != _LEGACY_REUSE_REVISION
        or correction_details.get("proof") != _LEGACY_REUSE_PROOF
        or correction_details.get("historical_deletion_audit_id") != audit_id
        or correction_details.get("reused_statement_import_id") != deleted_id
        or correction_details.get("original_audit_entity_id_cleared") is not True
        or not isinstance(correction_details.get("historical_delete_created_at"), str)
        or not correction_details.get("historical_delete_created_at")
        or not isinstance(correction_details.get("reused_statement_created_at"), str)
        or not correction_details.get("reused_statement_created_at")
    ):
        raise ValueError(f"备份数据库账单删除审计历史ID消歧证据异常：#{audit_id}")
    return True


def _backup_deleted_audit_id_sets(
    connection: sqlite3.Connection,
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    deleted_ids = {key: set() for key, _table_name in ID_HIGH_WATER_TABLES}
    strict_deleted_ids = {key: set() for key, _table_name in ID_HIGH_WATER_TABLES}
    rows = connection.execute(
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
        details = _backup_audit_details(raw_details, audit_id=audit_id)
        if action == "MASTER_DATA_DELETED":
            entity_type = details.get("deleted_entity_type")
            if not isinstance(entity_type, str) or not entity_type.strip():
                raise ValueError(f"备份数据库主数据删除审计缺少类型：#{audit_id}")
            deleted_id = _backup_deleted_id(
                details.get("deleted_entity_id"),
                context=f"#{audit_id}.deleted_entity_id",
            )
            if event_entity_id is not None:
                legacy_id = _backup_deleted_id(
                    event_entity_id,
                    context=f"#{audit_id}.entity_id",
                )
                if legacy_id != deleted_id:
                    raise ValueError(f"备份数据库主数据删除审计原ID冲突：#{audit_id}")
            normalized_type = entity_type.strip().upper().replace("-", "_").replace(" ", "_")
            key = {
                "CLIENT": "id_high_water.clients",
                "SUB_ACCOUNT": "id_high_water.sub_accounts",
            }.get(normalized_type)
            if key is not None:
                deleted_ids[key].add(deleted_id)
                strict_deleted_ids[key].add(deleted_id)
            continue

        deleted_import_id = _backup_statement_deleted_id(
            audit_id=audit_id,
            event_entity_id=event_entity_id,
            details=details,
        )
        statement_key = "id_high_water.statement_imports"
        deleted_ids[statement_key].add(deleted_import_id)
        if not _backup_legacy_reuse_disambiguated(
            connection,
            audit_id=audit_id,
            action=str(action),
            event_entity_id=event_entity_id,
            details=details,
            deleted_id=deleted_import_id,
        ):
            strict_deleted_ids[statement_key].add(deleted_import_id)
        if action == "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED":
            deleted_snapshot_id = _backup_deleted_id(
                details.get("deleted_snapshot_id"),
                context=f"#{audit_id}.deleted_snapshot_id",
            )
            snapshot_key = "id_high_water.balance_snapshots"
            deleted_ids[snapshot_key].add(deleted_snapshot_id)
            strict_deleted_ids[snapshot_key].add(deleted_snapshot_id)
    return deleted_ids, strict_deleted_ids


def _backup_deleted_audit_high_waters(
    connection: sqlite3.Connection,
    deleted_ids: dict[str, set[int]] | None = None,
) -> dict[str, int]:
    ids_by_key = (
        deleted_ids
        if deleted_ids is not None
        else _backup_deleted_audit_id_sets(connection)[0]
    )
    return {key: max(values, default=0) for key, values in ids_by_key.items()}


def _validate_backup_id_high_water_settings(connection: sqlite3.Connection) -> None:
    deleted_ids, strict_deleted_ids = _backup_deleted_audit_id_sets(connection)
    audit_maxima = _backup_deleted_audit_high_waters(connection, deleted_ids)
    for key, table_name in ID_HIGH_WATER_TABLES:
        live_ids = {
            int(row[0])
            for row in connection.execute(
                f'SELECT id FROM "{table_name}"'
            ).fetchall()
        }
        reused_ids = live_ids & strict_deleted_ids[key]
        if reused_ids:
            raise ValueError(
                "备份数据库现存记录ID与历史删除审计重复："
                f"{key}，冲突数量={len(reused_ids)}"
            )
        rows = connection.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(f"备份数据库缺少ID高水位设置：{key}")
        raw_value = rows[0][0]
        if (
            not isinstance(raw_value, str)
            or _CANONICAL_NON_NEGATIVE_INTEGER.fullmatch(raw_value) is None
        ):
            raise ValueError(f"备份数据库ID高水位设置异常：{key}")
        stored_max = int(raw_value)
        if stored_max > _SQLITE_MAX_ROW_ID:
            raise ValueError(f"备份数据库ID高水位设置超出SQLite范围：{key}")
        table_max = int(
            connection.execute(
                f'SELECT COALESCE(MAX(id), 0) FROM "{table_name}"'
            ).fetchone()[0]
        )
        if stored_max < max(table_max, audit_maxima[key]):
            raise ValueError(f"备份数据库ID高水位低于可信历史最大ID：{key}")


def _sqlite_database_revision(database_path: Path) -> str | None:
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True
        )
        try:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
            ).fetchone()
            if table_exists is None:
                return None
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ValueError("数据包数据库无法读取版本") from exc
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise ValueError("数据包数据库迁移版本无效")
    return rows[0][0]


def _validate_sqlite_database(database_path: Path) -> None:
    try:
        # ``immutable=1`` prevents a WAL-mode backup from creating -wal/-shm
        # sidecar files inside the already validated archive tree.
        connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            for table_name, required_columns in REQUIRED_DATABASE_COLUMNS.items():
                if table_name not in table_names:
                    raise ValueError("备份数据库不是金融计划收费系统数据库")
                actual_columns = {
                    row[1]
                    for row in connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                }
                if not required_columns.issubset(actual_columns):
                    raise ValueError("备份数据库结构不兼容")
            if "alembic_version" in table_names:
                revision_rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
                if (
                    len(revision_rows) != 1
                    or not isinstance(revision_rows[0][0], str)
                    or revision_rows[0][0] not in SUPPORTED_DATABASE_REVISIONS
                ):
                    raise ValueError("备份数据库迁移版本不受当前系统支持")
                database_revision = revision_rows[0][0]
                if database_revision in {
                    "c4b7f1d92e60",
                    "9d2f6a8c4b13",
                    "7f3c2a91b6e4",
                    "c1a7d5e9b402",
                    "d4f8a1c73b29",
                    "e8b2c6d91a04",
                }:
                    _require_named_partial_unique_index(
                        connection,
                        "invoices",
                        "uq_invoices_active_client_period_plan",
                        ("client_id", "year", "quarter", "fee_plan_id"),
                        "lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')",
                    )
                    _require_named_partial_unique_index(
                        connection,
                        "invoice_sources",
                        "uq_invoice_sources_active_settlement",
                        ("settlement_id",),
                        "active = 1",
                    )
                if database_revision == "9d2f6a8c4b13":
                    trigger_sql = {
                        str(row[0]): str(row[1] or "")
                        for row in connection.execute(
                            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
                        ).fetchall()
                    }
                    if set(trigger_sql) != OLD_HEAD_TRIGGER_NAMES or any(
                        not trigger_sql[name].strip() for name in OLD_HEAD_TRIGGER_NAMES
                    ):
                        raise ValueError("备份数据库结构不兼容")
                if database_revision in {
                    "7f3c2a91b6e4",
                    "c1a7d5e9b402",
                    "d4f8a1c73b29",
                    "e8b2c6d91a04",
                }:
                    settlement_columns = {
                        row[1]
                        for row in connection.execute(
                            'PRAGMA table_info("quarterly_settlements")'
                        ).fetchall()
                    }
                    required_ledger_columns = {
                        "invoice_corrections": {
                            "original_invoice_id", "replacement_invoice_id", "status", "reason"
                        },
                        "payment_allocations": {
                            "payment_id", "invoice_id", "amount_cents", "entry_type",
                            "reverses_allocation_id", "correction_id"
                        },
                        "payment_refunds": {
                            "payment_id", "correction_id", "amount_cents", "proof_attachment_id"
                        },
                        "invoice_adjustments": {
                            "invoice_id", "correction_id", "payment_id",
                            "adjustment_type", "amount_cents"
                        },
                    }
                    if not {"version_no", "replaces_settlement_id"}.issubset(
                        settlement_columns
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    for table_name, required_columns in required_ledger_columns.items():
                        if table_name not in table_names:
                            raise ValueError("备份数据库结构不兼容")
                        actual_columns = {
                            row[1]
                            for row in connection.execute(
                                f'PRAGMA table_info("{table_name}")'
                            ).fetchall()
                        }
                        if not required_columns.issubset(actual_columns):
                            raise ValueError("备份数据库结构不兼容")
                    payment_info = {
                        row[1]: row
                        for row in connection.execute('PRAGMA table_info("payments")').fetchall()
                    }
                    if (
                        not payment_info.get("proof_attachment_id")
                        or payment_info["proof_attachment_id"][3] != 1
                        or not payment_info.get("company_difference_cents")
                        or payment_info["company_difference_cents"][3] != 1
                        or "difference_reason" not in payment_info
                        or str(payment_info["company_difference_cents"][4] or "").strip(
                            "()'\" "
                        ) != "0"
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    _require_named_partial_unique_index(
                        connection,
                        "quarterly_settlements",
                        "uq_settlement_group_period_active",
                        ("client_id", "platform_id", "fee_plan_id", "year", "quarter"),
                        "status != 'VOID'",
                    )

                    def foreign_keys(table_name: str) -> set[tuple[str, str, str, str]]:
                        return {
                            (str(row[3]), str(row[2]), str(row[4]), str(row[6]).upper())
                            for row in connection.execute(
                                f'PRAGMA foreign_key_list("{table_name}")'
                            ).fetchall()
                        }

                    required_foreign_keys = {
                        "quarterly_settlements": {
                            ("previous_settlement_id", "quarterly_settlements", "id", "RESTRICT"),
                            ("replaces_settlement_id", "quarterly_settlements", "id", "RESTRICT"),
                        },
                        "payments": {
                            ("invoice_id", "invoices", "id", "RESTRICT"),
                            ("proof_attachment_id", "attachments", "id", "RESTRICT"),
                        },
                        "invoice_corrections": {
                            ("original_invoice_id", "invoices", "id", "RESTRICT"),
                            ("replacement_invoice_id", "invoices", "id", "RESTRICT"),
                        },
                        "payment_allocations": {
                            ("payment_id", "payments", "id", "RESTRICT"),
                            ("invoice_id", "invoices", "id", "RESTRICT"),
                            ("reverses_allocation_id", "payment_allocations", "id", "RESTRICT"),
                            ("correction_id", "invoice_corrections", "id", "RESTRICT"),
                        },
                        "payment_refunds": {
                            ("payment_id", "payments", "id", "RESTRICT"),
                            ("correction_id", "invoice_corrections", "id", "RESTRICT"),
                            ("proof_attachment_id", "attachments", "id", "RESTRICT"),
                        },
                        "invoice_adjustments": {
                            ("invoice_id", "invoices", "id", "RESTRICT"),
                            ("correction_id", "invoice_corrections", "id", "RESTRICT"),
                            ("payment_id", "payments", "id", "RESTRICT"),
                        },
                    }
                    if any(
                        not required.issubset(foreign_keys(table_name))
                        for table_name, required in required_foreign_keys.items()
                    ):
                        raise ValueError("备份数据库结构不兼容")

                    required_unique_columns = {
                        "quarterly_settlements": {
                            (
                                "client_id", "platform_id", "fee_plan_id",
                                "year", "quarter", "version_no",
                            ),
                            ("replaces_settlement_id",),
                        },
                        "payments": {("proof_attachment_id",)},
                        "invoice_corrections": {
                            ("original_invoice_id",),
                            ("replacement_invoice_id",),
                        },
                        "payment_allocations": {("reverses_allocation_id",)},
                        "payment_refunds": {("proof_attachment_id",)},
                        "invoice_adjustments": {("payment_id",)},
                    }
                    for table_name, required_columns in required_unique_columns.items():
                        _require_nonpartial_unique_columns(
                            connection, table_name, required_columns
                        )

                    table_sql = {
                        table_name: str(
                            connection.execute(
                                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                                (table_name,),
                            ).fetchone()[0]
                            or ""
                        )
                        for table_name in ("payments", "invoice_adjustments")
                    }
                    if not all(
                        marker in table_sql["payments"]
                        for marker in (
                            "ck_payment_company_difference_nonnegative",
                            "ck_payment_difference_reason",
                        )
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if "ck_invoice_adjustment_owner_shape" not in table_sql[
                        "invoice_adjustments"
                    ]:
                        raise ValueError("备份数据库结构不兼容")
                    trigger_sql = {
                        row[0]: str(row[1] or "")
                        for row in connection.execute(
                            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
                        ).fetchall()
                    }
                    expected_trigger_names = (
                        NEW_HEAD_TRIGGER_NAMES
                        if database_revision == "7f3c2a91b6e4"
                        else LATEST_HEAD_TRIGGER_NAMES
                    )
                    if set(trigger_sql) != expected_trigger_names or any(
                        not trigger_sql[name].strip() for name in expected_trigger_names
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    required_trigger_markers = {
                        "trg_settlement_validate_finalize": "settlement_container_previous_changed",
                        "trg_settlement_insert_draft_only": "settlement_replacement_invalid",
                        "trg_settlement_parent_financial_lock": "settlement_replacement_identity_immutable",
                        "trg_invoice_lifecycle_transition": "invoice_void_metadata_invalid",
                        "trg_payment_validate_insert": "payment_proof_invalid",
                        "trg_payment_allocation_validate_insert": "ordinary_payment_must_settle_invoice",
                        "trg_invoice_block_void_with_payment": "invoice_payment_allocation_not_reversed",
                        "trg_invoice_correction_validate_insert": "invoice_correction_group_already_open",
                        "trg_invoice_correction_validate_update": "invoice_correction_source_lineage_invalid",
                        "trg_invoice_adjustment_validate_insert": "payment.company_difference_cents",
                    }
                    if any(
                        marker not in trigger_sql.get(trigger_name, "")
                        for trigger_name, marker in required_trigger_markers.items()
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if "invoice_void_metadata_immutable" not in trigger_sql.get(
                        "trg_invoice_lifecycle_transition", ""
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if "payment_invoice_group_has_open_correction" not in trigger_sql.get(
                        "trg_payment_validate_insert", ""
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if not all(
                        marker in trigger_sql.get("trg_payment_validate_insert", "")
                        for marker in (
                            "payment_invoice_ledger_not_empty",
                            "payment_must_settle_invoice",
                            "payment_difference_invalid",
                            "payment_method_invalid",
                        )
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if "payment_refund_method_invalid" not in trigger_sql.get(
                        "trg_payment_refund_validate_insert", ""
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if "invoice_correction_blank_replacement_ledger_required" not in trigger_sql.get(
                        "trg_invoice_correction_validate_update", ""
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if "settlement_replacement_invalid_at_finalize" not in trigger_sql.get(
                        "trg_settlement_parent_financial_lock", ""
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if "invoice_open_correction_replacement_cannot_void" not in trigger_sql.get(
                        "trg_invoice_block_void_with_payment", ""
                    ):
                        raise ValueError("备份数据库结构不兼容")
                    if database_revision in {"c1a7d5e9b402", "d4f8a1c73b29", "e8b2c6d91a04"}:
                        _validate_backup_id_high_water_settings(connection)
                        if not delete_guard_trigger_sql_is_current(trigger_sql):
                            raise ValueError("备份数据库结构不兼容")
                    if database_revision in {"7f3c2a91b6e4", "c1a7d5e9b402"}:
                        if not settlement_boundary_trigger_sql_is_legacy(trigger_sql):
                            raise ValueError("备份数据库结构不兼容")
                    elif database_revision in {"d4f8a1c73b29", "e8b2c6d91a04"}:
                        if not settlement_boundary_trigger_sql_is_current(trigger_sql):
                            raise ValueError("备份数据库结构不兼容")
                        workflow_valid = (
                            workflow_trigger_sql_is_current(trigger_sql)
                            if database_revision == "e8b2c6d91a04"
                            else workflow_trigger_sql_is_legacy(trigger_sql)
                        )
                        if not workflow_valid:
                            raise ValueError("备份数据库财务流程保护结构不兼容")
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ValueError("备份数据库无法打开") from exc
    if integrity_rows != [("ok",)]:
        raise ValueError("备份数据库完整性校验失败")
    if foreign_key_rows:
        raise ValueError("备份数据库外键完整性校验失败")


def _remove_obsolete_pending_root(path: Path) -> None:
    shutil.rmtree(path)


def _write_marker_atomically(marker: Path, payload: dict[str, str]) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=marker.parent,
            prefix=".pending_restore_",
            suffix=".json.tmp",
            delete=False,
        ) as temporary:
            json.dump(payload, temporary, ensure_ascii=False)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.replace(marker)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _validate_pending_restore_root(pending_root: Path, staging_base: Path) -> Path:
    if pending_root.is_symlink() or pending_root.is_junction():
        raise ValueError("待恢复目录不安全")
    resolved_base = staging_base.resolve()
    resolved_root = pending_root.resolve()
    if (
        resolved_root.parent != resolved_base
        or not (
            resolved_root.name == "pending_restore"
            or resolved_root.name.startswith("pending_restore_")
        )
        or resolved_root == resolved_base
    ):
        raise ValueError("待恢复目录不安全")
    return resolved_root


def _read_existing_pending_restore_root(marker: Path, staging_base: Path) -> Path | None:
    if not marker.is_file():
        return None
    try:
        payload = _read_pending_restore_payload(marker)
        pending_root = _validate_pending_restore_root(Path(payload["path"]), staging_base)
    except (OSError, ValueError):
        return None
    if not pending_root.is_dir():
        return None
    return pending_root


def _read_pending_restore_payload(marker: Path) -> dict[str, str]:
    try:
        payload = json.loads(
            marker.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_with_unique_keys,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        raise ValueError("待恢复标记无效") from exc
    if not isinstance(payload, dict) or set(payload) != {"path", "staged_at"}:
        raise ValueError("待恢复标记结构无效")
    if not isinstance(payload["path"], str) or not isinstance(payload["staged_at"], str):
        raise ValueError("待恢复标记结构无效")
    try:
        datetime.fromisoformat(payload["staged_at"])
    except ValueError as exc:
        raise ValueError("待恢复标记时间无效") from exc
    return payload


def _parse_manifest_bytes(data: bytes) -> object:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_json_object_with_unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKeyError) as exc:
        raise ValueError("备份manifest.json无效") from exc


def _json_object_with_unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(f"JSON字段重复：{key}")
        result[key] = value
    return result
