from __future__ import annotations

import json
import re
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import SessionLocal
from ..models import AuditEvent, StatementImport
from .storage import sha256_file


_PENDING_NAME = re.compile(
    r"^\.(?P<original_name>.+)\.(?P<token>[0-9a-fA-F]{32})\.delete-pending$"
)
_UPLOAD_PENDING_NAME = re.compile(
    r"^\.(?P<original_name>[0-9a-fA-F]{64}\.(?:jpg|png|pdf))\."
    r"(?P<token>[0-9a-fA-F]{32})\.upload-pending$"
)
_UPLOAD_PENDING_FORMAT = "financial-fee-system-statement-upload-pending-v1"
_DELETE_AUDIT_ACTIONS = (
    "STATEMENT_IMPORT_DELETED",
    "STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED",
)
_CLEANUP_METHOD = "ATOMIC_SAME_DIRECTORY_STAGE_THEN_UNLINK_AFTER_COMMIT"


@dataclass(frozen=True)
class _RecoveryAction:
    kind: str
    pending_path: Path
    original_path: Path
    sha256: str


@dataclass(frozen=True)
class _UploadRecoveryAction:
    kind: str
    marker_path: Path
    original_path: Path
    sha256: str


def _fail(detail: str) -> RuntimeError:
    return RuntimeError(f"账单删除原件启动对账失败：{detail}；系统已停止启动，请人工检查")


def _strict_lstat(path: Path):
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _fail(f"无法检查路径 {path}") from exc


def _pending_candidates(statement_root: Path) -> list[tuple[Path, Path]]:
    try:
        root_stat = statement_root.lstat()
        if (
            not stat_module.S_ISDIR(root_stat.st_mode)
            or statement_root.is_symlink()
            or statement_root.is_junction()
        ):
            raise _fail(f"账单原件根路径不是非链接普通目录 {statement_root}")
        entries = sorted(statement_root.iterdir(), key=lambda path: path.name.casefold())
    except FileNotFoundError as exc:
        raise _fail(f"账单原件根目录不存在 {statement_root}") from exc
    except OSError as exc:
        raise _fail(f"无法扫描账单原件目录 {statement_root}") from exc

    candidates: list[tuple[Path, Path]] = []
    for pending_path in entries:
        if not pending_path.name.endswith(".delete-pending"):
            continue
        match = _PENDING_NAME.fullmatch(pending_path.name)
        if match is None:
            raise _fail(f"发现命名不合法的待处理文件 {pending_path.name}")

        original_name = match.group("original_name")
        if (
            original_name in {".", ".."}
            or Path(original_name).name != original_name
            or "/" in original_name
            or "\\" in original_name
        ):
            raise _fail(f"待处理文件无法安全还原原名 {pending_path.name}")

        pending_stat = _strict_lstat(pending_path)
        if pending_stat is None or not stat_module.S_ISREG(pending_stat.st_mode):
            raise _fail(f"待处理路径不是顶层普通文件 {pending_path.name}")
        candidates.append((pending_path, statement_root / original_name))
    return candidates


def _read_upload_pending_marker(marker_path: Path) -> tuple[Path, str]:
    match = _UPLOAD_PENDING_NAME.fullmatch(marker_path.name)
    if match is None:
        raise _fail(f"发现命名不合法的上传待处理文件 {marker_path.name}")
    marker_stat = _strict_lstat(marker_path)
    if (
        marker_stat is None
        or not stat_module.S_ISREG(marker_stat.st_mode)
        or marker_stat.st_size <= 0
        or marker_stat.st_size > 4096
    ):
        raise _fail(f"上传待处理标记不是小型普通文件 {marker_path.name}")
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise _fail(f"上传待处理标记内容异常 {marker_path.name}") from exc
    if not isinstance(payload, dict) or set(payload) != {"format", "original_name", "sha256"}:
        raise _fail(f"上传待处理标记字段异常 {marker_path.name}")
    original_name = match.group("original_name")
    sha256 = payload.get("sha256")
    if (
        payload.get("format") != _UPLOAD_PENDING_FORMAT
        or payload.get("original_name") != original_name
        or not isinstance(sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        or Path(original_name).stem.casefold() != sha256.casefold()
    ):
        raise _fail(f"上传待处理标记证据不一致 {marker_path.name}")
    return marker_path.parent / original_name, sha256.casefold()


def _upload_pending_candidates(statement_root: Path) -> list[tuple[Path, Path, str]]:
    # Reuse the strict root-directory contract before inspecting upload markers.
    _pending_candidates(statement_root)
    try:
        entries = sorted(statement_root.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        raise _fail(f"无法扫描账单原件目录 {statement_root}") from exc
    candidates: list[tuple[Path, Path, str]] = []
    for marker_path in entries:
        if not marker_path.name.endswith(".upload-pending"):
            continue
        original_path, sha256 = _read_upload_pending_marker(marker_path)
        candidates.append((marker_path, original_path, sha256))
    return candidates


def _plan_upload_recovery(db: Session, statement_root: Path) -> list[_UploadRecoveryAction]:
    actions: list[_UploadRecoveryAction] = []
    seen_originals: set[Path] = set()
    statements = db.execute(
        select(StatementImport.id, StatementImport.stored_path, StatementImport.sha256)
    ).all()
    for marker_path, original_path, expected_sha256 in _upload_pending_candidates(
        statement_root
    ):
        if original_path in seen_originals:
            raise _fail(f"同一上传原件存在多个待处理标记 {original_path.name}")
        seen_originals.add(original_path)
        if any(stored_path == str(marker_path) for _id, stored_path, _sha in statements):
            raise _fail(f"数据库记录直接引用上传待处理标记 {marker_path.name}")

        original_stat = _strict_lstat(original_path)
        original_exists = original_stat is not None
        if original_exists:
            if not stat_module.S_ISREG(original_stat.st_mode):
                raise _fail(f"上传待处理原件不是普通文件 {original_path.name}")
            if sha256_file(original_path).casefold() != expected_sha256:
                raise _fail(f"上传待处理原件SHA-256不一致 {original_path.name}")

        reference_ids: list[int] = []
        for statement_id, stored_path, recorded_sha256 in statements:
            other_path = Path(stored_path)
            same_source = stored_path == str(original_path)
            if not same_source and original_exists:
                try:
                    other_path.lstat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise _fail(f"无法检查账单记录 #{statement_id} 的原件路径") from exc
                try:
                    same_source = original_path.samefile(other_path)
                except OSError as exc:
                    raise _fail(f"无法比较账单记录 #{statement_id} 的原件路径") from exc
            if same_source:
                if not isinstance(recorded_sha256, str) or recorded_sha256.casefold() != expected_sha256:
                    raise _fail(f"上传待处理原件与数据库记录 #{statement_id} 哈希冲突")
                reference_ids.append(int(statement_id))

        if len(reference_ids) > 1:
            raise _fail(f"上传待处理原件被多条数据库记录引用 {original_path.name}")
        if reference_ids and not original_exists:
            raise _fail(f"数据库已引用但上传待处理原件不存在 {original_path.name}")
        actions.append(
            _UploadRecoveryAction(
                kind="marker_only" if reference_ids or not original_exists else "orphan_source",
                marker_path=marker_path,
                original_path=original_path,
                sha256=expected_sha256,
            )
        )
    return actions


def _deletion_audits_by_staged_path(db: Session) -> dict[str, list[AuditEvent]]:
    result: dict[str, list[AuditEvent]] = {}
    audits = db.scalars(
        select(AuditEvent).where(AuditEvent.action.in_(_DELETE_AUDIT_ACTIONS))
    ).all()
    for audit in audits:
        details = audit.details_json
        if not isinstance(details, dict):
            continue
        source_cleanup = details.get("source_cleanup")
        if not isinstance(source_cleanup, dict):
            continue
        if source_cleanup.get("method") != _CLEANUP_METHOD:
            continue
        staged_path = source_cleanup.get("staged_path")
        if isinstance(staged_path, str) and staged_path:
            result.setdefault(staged_path, []).append(audit)
    return result


def _plan_recovery(db: Session, statement_root: Path) -> list[_RecoveryAction]:
    candidates = _pending_candidates(statement_root)
    if not candidates:
        return []

    audits_by_path = _deletion_audits_by_staged_path(db)
    actions: list[_RecoveryAction] = []
    for pending_path, original_path in candidates:
        if _strict_lstat(original_path) is not None:
            raise _fail(f"原路径与待处理文件同时存在 {original_path.name}")
        try:
            pending_sha256 = sha256_file(pending_path).casefold()
        except OSError as exc:
            raise _fail(f"无法读取待处理文件 {pending_path.name}") from exc

        pending_references = db.scalars(
            select(StatementImport.id).where(
                StatementImport.stored_path == str(pending_path)
            )
        ).all()
        if pending_references:
            raise _fail(f"数据库记录直接引用待处理路径 {pending_path.name}")

        statements = db.scalars(
            select(StatementImport).where(
                StatementImport.stored_path == str(original_path)
            )
        ).all()
        if len(statements) > 1:
            raise _fail(f"数据库有多条记录指向原路径 {original_path.name}")
        if len(statements) == 1:
            if statements[0].sha256.casefold() != pending_sha256:
                raise _fail(f"待恢复原件与数据库SHA-256不一致 {pending_path.name}")
            actions.append(
                _RecoveryAction(
                    kind="restore",
                    pending_path=pending_path,
                    original_path=original_path,
                    sha256=pending_sha256,
                )
            )
            continue

        matching_audits = audits_by_path.get(str(pending_path), [])
        if len(matching_audits) != 1:
            raise _fail(f"待清理原件没有唯一、已提交的删除审计 {pending_path.name}")
        audit_details = matching_audits[0].details_json
        source_cleanup = audit_details["source_cleanup"]
        audit_sha256 = source_cleanup.get("sha256")
        if not isinstance(audit_sha256, str) or audit_sha256.casefold() != pending_sha256:
            raise _fail(f"待清理原件与删除审计SHA-256不一致 {pending_path.name}")
        actions.append(
            _RecoveryAction(
                kind="cleanup",
                pending_path=pending_path,
                original_path=original_path,
                sha256=pending_sha256,
            )
        )
    return actions


def _revalidate_action(action: _RecoveryAction) -> None:
    pending_stat = _strict_lstat(action.pending_path)
    if pending_stat is None or not stat_module.S_ISREG(pending_stat.st_mode):
        raise _fail(f"待处理普通文件在执行前已变化 {action.pending_path.name}")
    if _strict_lstat(action.original_path) is not None:
        raise _fail(f"原路径在执行前出现冲突 {action.original_path.name}")
    try:
        current_sha256 = sha256_file(action.pending_path).casefold()
    except OSError as exc:
        raise _fail(f"待处理文件在执行前无法读取 {action.pending_path.name}") from exc
    if current_sha256 != action.sha256:
        raise _fail(f"待处理文件在执行前SHA-256已变化 {action.pending_path.name}")


def _revalidate_upload_action(action: _UploadRecoveryAction) -> None:
    current_original, current_sha256 = _read_upload_pending_marker(action.marker_path)
    if current_original != action.original_path or current_sha256 != action.sha256:
        raise _fail(f"上传待处理标记在执行前已变化 {action.marker_path.name}")
    original_stat = _strict_lstat(action.original_path)
    if action.kind == "orphan_source":
        if original_stat is None or not stat_module.S_ISREG(original_stat.st_mode):
            raise _fail(f"上传孤儿原件在执行前已变化 {action.original_path.name}")
        if sha256_file(action.original_path).casefold() != action.sha256:
            raise _fail(f"上传孤儿原件在执行前SHA-256已变化 {action.original_path.name}")
    elif original_stat is not None:
        if (
            not stat_module.S_ISREG(original_stat.st_mode)
            or sha256_file(action.original_path).casefold() != action.sha256
        ):
            raise _fail(f"已入库上传原件在执行前已变化 {action.original_path.name}")


def reconcile_statement_delete_commit_outcome(
    *,
    import_id: int,
    original_path: Path,
    pending_path: Path,
    expected_sha256: str,
) -> bool:
    """Prove an uncertain delete commit and restore or preserve accordingly.

    ``False`` means the database deletion did not commit and the original file
    has been restored. ``True`` means both the deleted row and its committed
    audit prove that deletion committed; the caller may continue post-commit
    cleanup of the still-pending file.
    """

    statement_root = get_settings().data_root / "statement_imports"
    candidates = dict(_pending_candidates(statement_root))
    candidate_original = candidates.get(pending_path)
    if candidate_original is None or candidate_original != original_path:
        raise _fail(f"无法识别提交结果不确定的待处理文件 {pending_path.name}")

    with SessionLocal() as db:
        try:
            db.execute(text("BEGIN IMMEDIATE"))
            if _strict_lstat(original_path) is not None:
                raise _fail(f"提交结果不确定时原路径已被占用 {original_path.name}")
            pending_stat = _strict_lstat(pending_path)
            if pending_stat is None or not stat_module.S_ISREG(pending_stat.st_mode):
                raise _fail(f"提交结果不确定时待处理文件不是普通文件 {pending_path.name}")
            pending_sha256 = sha256_file(pending_path).casefold()
            if pending_sha256 != expected_sha256.casefold():
                raise _fail(f"提交结果不确定时待处理文件SHA-256不一致 {pending_path.name}")

            if db.scalar(
                select(StatementImport.id)
                .where(StatementImport.stored_path == str(pending_path))
                .limit(1)
            ) is not None:
                raise _fail(f"数据库记录直接引用待处理路径 {pending_path.name}")

            original_references = db.scalars(
                select(StatementImport).where(
                    StatementImport.stored_path == str(original_path)
                )
            ).all()
            row_by_id = db.get(StatementImport, import_id)
            matching_audits = _deletion_audits_by_staged_path(db).get(
                str(pending_path), []
            )

            if original_references:
                if (
                    len(original_references) != 1
                    or original_references[0].id != import_id
                    or row_by_id is None
                    or row_by_id.id != original_references[0].id
                    or row_by_id.sha256.casefold() != pending_sha256
                    or matching_audits
                ):
                    raise _fail(f"提交结果不确定且数据库原件关系冲突 {original_path.name}")
                pending_path.rename(original_path)
                db.commit()
                return False

            if row_by_id is not None:
                raise _fail(f"提交结果不确定且原ID已指向其他记录 #{import_id}")
            if len(matching_audits) != 1:
                raise _fail(f"提交结果不确定且没有唯一删除审计 {pending_path.name}")
            details = matching_audits[0].details_json
            source_cleanup = details.get("source_cleanup") if isinstance(details, dict) else None
            if (
                not isinstance(details, dict)
                or details.get("deleted_import_id") != import_id
                or not isinstance(source_cleanup, dict)
                or source_cleanup.get("method") != _CLEANUP_METHOD
                or source_cleanup.get("staged_path") != str(pending_path)
                or not isinstance(source_cleanup.get("sha256"), str)
                or source_cleanup["sha256"].casefold() != pending_sha256
            ):
                raise _fail(f"提交结果不确定且删除审计证据不一致 {pending_path.name}")
            db.commit()
            return True
        except OperationalError as exc:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise _fail("无法取得提交结果对账写锁") from exc
        except Exception:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise


def reconcile_statement_delete_without_source_commit_outcome(*, import_id: int) -> bool:
    """Prove an uncertain deletion when an unconfirmed import had no source file."""

    with SessionLocal() as db:
        try:
            db.execute(text("BEGIN IMMEDIATE"))
            row_by_id = db.get(StatementImport, import_id)
            matching_audits: list[AuditEvent] = []
            for audit in db.scalars(
                select(AuditEvent).where(AuditEvent.action.in_(_DELETE_AUDIT_ACTIONS))
            ).all():
                details = audit.details_json
                if not isinstance(details, dict) or details.get("deleted_import_id") != import_id:
                    continue
                source_cleanup = details.get("source_cleanup")
                if (
                    isinstance(source_cleanup, dict)
                    and source_cleanup.get("method") == _CLEANUP_METHOD
                    and source_cleanup.get("staged_path") is None
                    and source_cleanup.get("sha256") is None
                ):
                    matching_audits.append(audit)

            if row_by_id is not None:
                if matching_audits:
                    raise _fail(f"无原件删除提交结果冲突 #{import_id}")
                db.commit()
                return False
            if len(matching_audits) != 1:
                raise _fail(f"无原件删除没有唯一已提交审计 #{import_id}")
            db.commit()
            return True
        except OperationalError as exc:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise _fail("无法取得无原件删除提交结果对账写锁") from exc
        except Exception:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise


def reconcile_statement_delete_pending_files() -> dict[str, int]:
    """Resolve interrupted statement source deletion before serving requests.

    Every candidate is proven against the current database before any file is
    changed.  Ambiguous evidence fails startup closed.
    """

    statement_root = get_settings().data_root / "statement_imports"
    with SessionLocal() as db:
        try:
            db.execute(text("BEGIN IMMEDIATE"))
            actions = _plan_recovery(db, statement_root)
            upload_actions = _plan_upload_recovery(db, statement_root)
            delete_originals = {action.original_path for action in actions}
            upload_originals = {action.original_path for action in upload_actions}
            if delete_originals & upload_originals:
                raise _fail("同一账单原路径同时存在删除与上传待处理证据")
            restored = 0
            cleaned = 0
            for action in actions:
                try:
                    _revalidate_action(action)
                    if action.kind == "restore":
                        action.pending_path.rename(action.original_path)
                        restored += 1
                    else:
                        action.pending_path.unlink()
                        cleaned += 1
                except OSError as exc:
                    raise _fail(f"无法处理待删除原件 {action.pending_path.name}") from exc
            for action in upload_actions:
                try:
                    _revalidate_upload_action(action)
                    if action.kind == "orphan_source":
                        action.original_path.unlink()
                    action.marker_path.unlink()
                    cleaned += 1
                except OSError as exc:
                    raise _fail(f"无法处理上传待对账原件 {action.marker_path.name}") from exc
            db.commit()
            return {"restored": restored, "cleaned": cleaned}
        except OperationalError as exc:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise _fail("无法取得SQLite启动对账写锁") from exc
        except Exception:
            try:
                db.rollback()
            except SQLAlchemyError:
                pass
            raise
