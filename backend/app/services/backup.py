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

from ..config import get_settings
from .storage import sha256_file


INCLUDED_DIRECTORIES = ("attachments", "statement_imports", "output")
BACKUP_FORMAT = "financial-fee-system-backup"
BACKUP_VERSION = 1
MANIFEST_KEYS = frozenset({"format", "version", "created_at", "files"})
MANIFEST_RECORD_KEYS = frozenset({"path", "sha256"})
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
    }
)
REQUIRED_DATABASE_COLUMNS = {
    "companies": {"id", "name"},
    "clients": {"id", "company_id", "fc_id", "name"},
    "sub_accounts": {"id", "client_id", "account_number"},
    "quarterly_settlements": {"id", "client_id", "platform_id", "fee_plan_id", "year", "quarter", "status"},
    "app_settings": {"key", "value"},
}


class _DuplicateJsonKeyError(ValueError):
    pass


@dataclass(frozen=True)
class RestoreStageResult:
    marker: Path
    cleanup_warning: str | None = None


def create_backup() -> Path:
    settings = get_settings()
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
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    with tempfile.NamedTemporaryFile(
        dir=backup_directory,
        prefix=f".financial_system_backup_{timestamp}_",
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

            manifest = {
                "format": BACKUP_FORMAT,
                "version": BACKUP_VERSION,
                "created_at": datetime.now().isoformat(),
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
    _validate_sqlite_database(root.joinpath(*PurePosixPath(database_relative_path).parts))
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


def apply_pending_restore() -> bool:
    settings = get_settings()
    _recover_interrupted_restore_transactions(settings.data_root)
    _cleanup_unreferenced_restore_artifacts(settings.data_root)
    marker = settings.data_root / "pending_restore.json"
    if not marker.exists():
        return False
    payload = _read_pending_restore_payload(marker)
    pending_root = _validate_pending_restore_root(Path(payload["path"]), settings.data_root / "tmp")
    validate_backup_archive_root(pending_root)
    transaction_root = _prepare_restore_transaction(pending_root, settings.data_root)
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


def _prepare_restore_transaction(pending_root: Path, data_root: Path) -> Path:
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
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ValueError("备份清单结构无效")
    if (
        manifest.get("format") != BACKUP_FORMAT
        or type(manifest.get("version")) is not int
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
