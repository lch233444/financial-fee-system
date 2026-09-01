from __future__ import annotations

import hashlib
import json
import os
import stat as stat_module
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..database import SessionLocal, get_db
from ..models import (
    Attachment,
    AuditEvent,
    BalanceSnapshot,
    Client,
    ExportRecord,
    Platform,
    SettlementAccountLine,
    StatementImport,
    SubAccount,
)
from ..money import money_string, to_cents
from ..schemas import StatementConfirmRequest, StatementDeleteRequest, StatementHoldingInput
from ..services.calculation import is_quarter_end
from ..services.codex_app_server import (
    AI_PARSER_VERSION,
    FIXED_AI_MODEL,
    CodexIntegrationError,
    build_ai_review_result,
    get_codex_app_server,
)
from ..services.entity_ids import EntityIdAllocationError, allocate_entity_id
from ..services.statement_parser import DOCUMENT_TYPE_LABELS, OCR_PARSER_VERSION, parse_empf_statement
from ..services.statement_delete_recovery import (
    reconcile_statement_delete_commit_outcome,
    reconcile_statement_delete_without_source_commit_outcome,
)
from ..services.storage import detect_statement_format, is_within, sha256_file, store_bytes
from .ai_assistant import raise_ai_http_error, require_financial_system_request


router = APIRouter(prefix="/api/statement-imports", tags=["statement-imports"])
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
_STATEMENT_RECOGNITION_LOCK = threading.Lock()
_UPLOAD_PENDING_FORMAT = "financial-fee-system-statement-upload-pending-v1"


def _statement_dict(item: StatementImport) -> dict:
    return {
        "id": item.id,
        "original_name": item.original_name,
        "mime_type": item.mime_type,
        "parser_name": item.parser_name,
        "parser_version": item.parser_version,
        "status": item.status,
        "extracted": item.extracted_json or {},
        "reviewed": item.reviewed_json,
        "revision_log": item.revision_log_json or [],
        "confidence": item.confidence_json or {},
        "warnings": item.warnings_json or [],
        "ai_recognition": item.ai_recognition_json,
        "ai_status": item.ai_status,
        "ai_model": item.ai_model,
        "ai_recognized_at": item.ai_recognized_at.isoformat() if item.ai_recognized_at else None,
        "duplicate_of_id": item.duplicate_of_id,
        "confirmed_account_id": item.confirmed_account_id,
        "confirmed_snapshot_id": item.confirmed_snapshot_id,
        "created_at": item.created_at.isoformat(),
    }


def _require_statement(db: Session, import_id: int) -> StatementImport:
    item = db.get(StatementImport, import_id)
    if item is None:
        raise HTTPException(status_code=404, detail="导入记录不存在")
    return item


def _is_sqlite_busy(exc: OperationalError) -> bool:
    message = str(exc.orig).casefold()
    return (
        "database is locked" in message
        or "database table is locked" in message
        or "database is busy" in message
    )


def _begin_immediate(db: Session) -> None:
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as exc:
        db.rollback()
        if _is_sqlite_busy(exc):
            raise HTTPException(
                status_code=409,
                detail="数据库正在处理另一笔财务写入，请稍后重试",
            ) from exc
        raise


def _next_entity_id(db: Session, model: type) -> int:
    try:
        return allocate_entity_id(db, model)
    except EntityIdAllocationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="系统ID序号记录异常，已停止新增，请联系管理员检查数据库",
        ) from exc


def _upload_target_preflight(directory: Path, *, data: bytes, suffix: str) -> tuple[Path, str, bool]:
    digest = hashlib.sha256(data).hexdigest()
    target = directory / f"{digest}{suffix}"
    try:
        root_stat = directory.lstat()
        if (
            not stat_module.S_ISDIR(root_stat.st_mode)
            or directory.is_symlink()
            or directory.is_junction()
        ):
            raise OSError("账单目录不是非链接普通目录")
        try:
            target_stat = target.lstat()
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None:
            if not stat_module.S_ISREG(target_stat.st_mode):
                raise OSError("目标账单原件不是普通文件")
            if sha256_file(target).casefold() != digest:
                raise OSError("目标账单原件内容与文件名哈希不一致")
    except OSError as exc:
        raise HTTPException(status_code=409, detail="账单原件目录或目标路径不安全") from exc
    return target, digest, target_stat is None


def _require_trusted_upload_source(
    stored_path: Path,
    *,
    expected_path: Path,
    expected_sha256: str,
) -> None:
    try:
        if (
            stored_path != expected_path
            or not stat_module.S_ISREG(stored_path.lstat().st_mode)
            or sha256_file(stored_path).casefold() != expected_sha256.casefold()
        ):
            raise RuntimeError("账单原件落盘结果无法安全验证")
    except OSError as exc:
        raise RuntimeError("账单原件落盘结果无法安全验证") from exc


def _create_upload_pending_marker(target: Path, *, sha256: str) -> Path:
    marker = target.with_name(f".{target.name}.{uuid4().hex}.upload-pending")
    payload = {
        "format": _UPLOAD_PENDING_FORMAT,
        "original_name": target.name,
        "sha256": sha256,
    }
    try:
        with marker.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise HTTPException(status_code=409, detail="无法建立账单上传安全对账标记") from exc
    return marker


def _remove_upload_pending_marker(marker: Path | None) -> bool:
    if marker is None:
        return True
    try:
        marker.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _statement_source_reference_ids(db: Session, path: Path) -> list[int]:
    reference_ids: list[int] = []
    for statement_id, stored_path in db.execute(
        select(StatementImport.id, StatementImport.stored_path)
    ).all():
        other_path = Path(stored_path)
        if stored_path == str(path):
            reference_ids.append(int(statement_id))
            continue
        try:
            other_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(
                f"无法检查账单记录 #{statement_id} 的原件路径"
            ) from exc
        try:
            if path.samefile(other_path):
                reference_ids.append(int(statement_id))
        except OSError as exc:
            raise RuntimeError(
                f"无法比较账单记录 #{statement_id} 的原件路径"
            ) from exc
    return reference_ids


def _duplicate_source_is_trusted(
    duplicate: StatementImport,
    *,
    expected_path: Path,
    expected_sha256: str,
    created_by_request: bool,
) -> bool:
    """Require a duplicate row's pre-existing source to be independently valid."""

    statement_root = get_settings().data_root / "statement_imports"
    try:
        if not isinstance(duplicate.stored_path, str) or not duplicate.stored_path:
            return False
        if not isinstance(duplicate.sha256, str) or not duplicate.sha256:
            return False
        source_path = Path(duplicate.stored_path)
        root_stat = statement_root.lstat()
        if (
            not stat_module.S_ISDIR(root_stat.st_mode)
            or statement_root.is_symlink()
            or statement_root.is_junction()
            or not is_within(source_path, statement_root)
        ):
            return False
        resolved_root = statement_root.resolve(strict=True)
        resolved_parent = source_path.parent.resolve(strict=True)
        if os.path.normcase(str(resolved_parent)) != os.path.normcase(str(resolved_root)):
            return False
        source_stat = source_path.lstat()
        if not stat_module.S_ISREG(source_stat.st_mode):
            return False
        if duplicate.sha256.casefold() != expected_sha256.casefold():
            return False
        if sha256_file(source_path).casefold() != expected_sha256.casefold():
            return False
        if created_by_request:
            # If this request created the canonical path, that file did not
            # exist at preflight and therefore cannot serve as proof that an
            # older duplicate row still had an intact source beforehand.
            if source_path == expected_path or source_path.samefile(expected_path):
                return False
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        return False
    return True


def _cleanup_new_upload_source(*, path: Path, expected_sha256: str, created: bool) -> None:
    if not created:
        return
    with SessionLocal() as check_db:
        try:
            check_db.execute(text("BEGIN IMMEDIATE"))
            if _statement_source_reference_ids(check_db, path):
                check_db.commit()
                return
            try:
                source_stat = path.lstat()
            except FileNotFoundError:
                check_db.commit()
                return
            if (
                not stat_module.S_ISREG(source_stat.st_mode)
                or sha256_file(path).casefold() != expected_sha256.casefold()
            ):
                raise RuntimeError("上传失败后的新原件无法证明仍为本请求内容")
            path.unlink()
            check_db.commit()
        except Exception:
            try:
                check_db.rollback()
            except Exception:
                pass
            raise


def _reconcile_statement_upload_commit_outcome(
    *, import_id: int, stored_path: Path, expected_sha256: str
) -> dict | None:
    """Return the committed row after an uncertain commit, or ``None`` if absent."""

    with SessionLocal() as check_db:
        try:
            check_db.execute(text("BEGIN IMMEDIATE"))
            try:
                source_stat = stored_path.lstat()
            except FileNotFoundError as exc:
                raise RuntimeError("上传提交结果不确定且原件不存在") from exc
            if (
                not stat_module.S_ISREG(source_stat.st_mode)
                or sha256_file(stored_path).casefold() != expected_sha256.casefold()
            ):
                raise RuntimeError("上传提交结果不确定且原件完整性异常")

            row_by_id = check_db.get(StatementImport, import_id)
            reference_ids = _statement_source_reference_ids(check_db, stored_path)
            if row_by_id is None:
                if reference_ids:
                    raise RuntimeError("上传提交结果不确定且原件已被其他记录引用")
                check_db.commit()
                return None
            if (
                len(reference_ids) != 1
                or reference_ids[0] != row_by_id.id
                or row_by_id.stored_path != str(stored_path)
                or row_by_id.sha256.casefold() != expected_sha256.casefold()
            ):
                raise RuntimeError("上传提交结果不确定且数据库关系冲突")
            result = {**_statement_dict(row_by_id), "duplicate": False}
            check_db.commit()
            return result
        except Exception:
            try:
                check_db.rollback()
            except Exception:
                pass
            raise


def _delete_conflict(db: Session, detail: str) -> None:
    db.rollback()
    raise HTTPException(status_code=409, detail=detail)


def _normalized_entity_type(model):
    return func.replace(
        func.replace(func.upper(func.trim(model.entity_type)), "-", "_"),
        " ",
        "_",
    )


def _statement_delete_logical_references(
    db: Session,
    *,
    import_id: int,
    snapshot_id: int,
) -> list[str]:
    references: list[str] = []
    for label, model in (("附件记录", Attachment), ("导出记录", ExportRecord)):
        normalized_type = _normalized_entity_type(model)
        has_reference = any(
            db.scalar(
                select(model.id)
                .where(
                    normalized_type == entity_type,
                    model.entity_id == entity_id,
                )
                .limit(1)
            )
            is not None
            for entity_type, entity_id in (
                ("SNAPSHOT", snapshot_id),
                ("STATEMENT_IMPORT", import_id),
            )
        )
        if has_reference:
            references.append(label)
    return references


def _source_path_for_delete(
    db: Session,
    *,
    item: StatementImport,
    require_integrity: bool,
) -> tuple[Path | None, str | None]:
    source_path = Path(item.stored_path)
    statement_root = get_settings().data_root / "statement_imports"
    try:
        root_stat = statement_root.lstat()
        if (
            not stat_module.S_ISDIR(root_stat.st_mode)
            or statement_root.is_symlink()
            or statement_root.is_junction()
        ):
            _delete_conflict(db, "账单原件根路径不是非链接普通目录，已停止删除")
        if not is_within(source_path, statement_root):
            _delete_conflict(db, "导入原件路径不安全，已停止删除")
        resolved_root = statement_root.resolve(strict=True)
        resolved_parent = source_path.parent.resolve(strict=True)
        if os.path.normcase(str(resolved_parent)) != os.path.normcase(str(resolved_root)):
            _delete_conflict(db, "导入原件不在账单目录顶层，已停止删除")
        try:
            source_stat = source_path.lstat()
        except FileNotFoundError:
            source_stat = None
        exists = source_stat is not None
        if exists and not stat_module.S_ISREG(source_stat.st_mode):
            _delete_conflict(db, "导入原件路径不是普通文件，已停止删除")
        actual_sha256 = sha256_file(source_path) if exists else None
        if exists and actual_sha256.casefold() != item.sha256.casefold():
            _delete_conflict(db, "导入原件SHA-256不一致（与数据库记录不符），已停止删除")
        if require_integrity:
            if source_stat is None:
                _delete_conflict(db, "已确认入账的导入原件不存在，已停止删除")
            else:
                if source_stat.st_size <= 0:
                    _delete_conflict(db, "已确认入账的导入原件为空，已停止删除")
    except (OSError, RuntimeError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="导入原件无法安全检查，已停止删除") from exc
    return (source_path if exists else None, actual_sha256)


def _confirmed_snapshot_audit_summary(
    db: Session,
    *,
    snapshot: BalanceSnapshot,
) -> dict:
    holdings = snapshot.holdings_json if snapshot.holdings_json is not None else []
    if not isinstance(holdings, list):
        _delete_conflict(db, "已确认余额快照的持仓结构异常，已停止删除")
    try:
        canonical_holdings = json.dumps(
            holdings,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="已确认余额快照的持仓摘要无法安全生成，已停止删除",
        ) from exc
    return {
        "snapshot_as_of_date": snapshot.as_of_date.isoformat(),
        "total_balance_cents": snapshot.total_balance_cents,
        "currency": snapshot.currency,
        "eligible_for_closing": snapshot.eligible_for_closing,
        "holdings_count": len(holdings),
        "holdings_json_sha256": hashlib.sha256(canonical_holdings).hexdigest(),
    }


def _pending_source_path(source_path: Path | None) -> Path | None:
    if source_path is None:
        return None
    return source_path.with_name(
        f".{source_path.name}.{uuid4().hex}.delete-pending"
    )


def _stage_source_file(source_path: Path | None, staged_path: Path | None) -> None:
    if source_path is None or staged_path is None:
        return
    source_path.replace(staged_path)


def _path_exists_strict(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _validate_staged_source(
    *,
    source_path: Path | None,
    staged_path: Path | None,
    expected_sha256: str | None,
) -> None:
    if source_path is None or staged_path is None:
        return
    staged_stat = staged_path.lstat()
    if not stat_module.S_ISREG(staged_stat.st_mode):
        raise OSError("暂存原件不是普通文件")
    if _path_exists_strict(source_path):
        raise OSError("原路径在暂存后出现冲突")
    if expected_sha256 is None or sha256_file(staged_path).casefold() != expected_sha256.casefold():
        raise OSError("暂存原件SHA-256在提交前发生变化")


def _same_source_reference_id(
    db: Session,
    *,
    item: StatementImport,
    source_path: Path,
) -> int | None:
    other_sources = db.execute(
        select(StatementImport.id, StatementImport.stored_path).where(
            StatementImport.id != item.id
        )
    ).all()
    for other_id, other_stored_path in other_sources:
        other_path = Path(other_stored_path)
        try:
            other_path.lstat()
        except FileNotFoundError:
            # A currently absent path cannot alias the existing source inode.
            continue
        except (OSError, RuntimeError) as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"无法安全检查导入记录 #{other_id} 的原件路径，已停止删除",
            ) from exc
        try:
            if source_path.samefile(other_path):
                return int(other_id)
        except (OSError, RuntimeError) as exc:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"无法安全比较导入记录 #{other_id} 的原件路径，已停止删除",
            ) from exc
    return None


def _rollback_and_restore_source(
    db: Session,
    *,
    source_path: Path | None,
    staged_path: Path | None,
    expected_sha256: str | None,
) -> None:
    rollback_error: Exception | None = None
    try:
        db.rollback()
    except Exception as exc:  # File restoration must still be attempted.
        rollback_error = exc

    restore_error: OSError | None = None
    if source_path is not None and staged_path is not None:
        try:
            if _path_exists_strict(staged_path):
                staged_stat = staged_path.lstat()
                if (
                    not stat_module.S_ISREG(staged_stat.st_mode)
                    or expected_sha256 is None
                    or sha256_file(staged_path).casefold()
                    != expected_sha256.casefold()
                ):
                    raise OSError("暂存原件无法证明仍为删除前原件")
                if _path_exists_strict(source_path):
                    raise OSError("原路径已被占用")
                staged_path.replace(source_path)
        except OSError as exc:
            restore_error = exc
    if restore_error is not None:
        raise HTTPException(
            status_code=500,
            detail="删除未提交且原件自动恢复失败，请停止操作并检查数据目录",
        ) from restore_error
    if rollback_error is not None:
        raise rollback_error


@router.get("")
def list_statement_imports(db: Session = Depends(get_db)) -> list[dict]:
    items = db.scalars(select(StatementImport).order_by(StatementImport.created_at.desc())).all()
    return [_statement_dict(item) for item in items]


def _store_parse_and_create_statement(
    *,
    data: bytes,
    detected_suffix: str,
    detected_mime: str,
    original_name: str | None,
    db: Session,
) -> dict:
    # The source file, duplicate decision, OCR result, and database row form one
    # critical section with recognition, confirmation, and deletion. This
    # helper runs in FastAPI's worker pool so waiting for the lock or OCR cannot
    # block the application's asyncio event loop.
    with _STATEMENT_RECOGNITION_LOCK:
        settings = get_settings()
        statement_root = settings.data_root / "statement_imports"
        expected_path, expected_sha256, created_by_request = _upload_target_preflight(
            statement_root,
            data=data,
            suffix=detected_suffix,
        )
        upload_pending_marker = (
            _create_upload_pending_marker(expected_path, sha256=expected_sha256)
            if created_by_request
            else None
        )
        stored_path = expected_path
        commit_attempted = False
        item_id: int | None = None
        try:
            stored_path, digest = store_bytes(
                data=data,
                # The storage suffix and parser path are derived from magic bytes.
                original_name=f"statement{detected_suffix}",
                directory=statement_root,
            )
            if digest.casefold() != expected_sha256.casefold():
                raise RuntimeError("账单原件落盘结果无法安全验证")
            _require_trusted_upload_source(
                stored_path,
                expected_path=expected_path,
                expected_sha256=expected_sha256,
            )

            parsed = parse_empf_statement(stored_path)
            _begin_immediate(db)
            # Recognition may be slow. Re-check the exact file after obtaining
            # the database write lock so a file changed during parsing can
            # never be recorded with a stale digest.
            _require_trusted_upload_source(
                stored_path,
                expected_path=expected_path,
                expected_sha256=expected_sha256,
            )
            duplicate = db.scalar(
                select(StatementImport).where(StatementImport.sha256 == digest)
            )
            if duplicate:
                if not _duplicate_source_is_trusted(
                    duplicate,
                    expected_path=expected_path,
                    expected_sha256=expected_sha256,
                    created_by_request=created_by_request,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            "已有同哈希导入记录的原件缺失或完整性异常，"
                            "已停止重复导入，请先检查原记录"
                        ),
                    )
                db.rollback()
                _cleanup_new_upload_source(
                    path=expected_path,
                    expected_sha256=expected_sha256,
                    created=created_by_request,
                )
                duplicate_result = {**_statement_dict(duplicate), "duplicate": True}
                if not _remove_upload_pending_marker(upload_pending_marker):
                    duplicate_result["upload_recovery_pending"] = True
                return duplicate_result

            semantic_duplicate = None
            semantic_key = (
                parsed.account_number,
                parsed.as_of_date.isoformat() if parsed.as_of_date else None,
                parsed.total_balance,
            )
            if all(semantic_key):
                for previous in db.scalars(
                    select(StatementImport).order_by(StatementImport.id.desc())
                ).all():
                    values = previous.reviewed_json or previous.extracted_json or {}
                    previous_key = (
                        values.get("account_number"),
                        values.get("as_of_date"),
                        values.get("total_balance"),
                    )
                    if tuple(str(value) for value in previous_key) == tuple(
                        str(value) for value in semantic_key
                    ):
                        semantic_duplicate = previous
                        parsed.warnings.insert(
                            0,
                            f"疑似重复账单：关键字段与导入记录#{previous.id}相同，请核对",
                        )
                        break
            item = StatementImport(
                id=_next_entity_id(db, StatementImport),
                original_name=original_name or stored_path.name,
                stored_path=str(stored_path),
                sha256=digest,
                mime_type=detected_mime,
                parser_name=f"LOCAL_OCR_{parsed.document_type.upper()}",
                status="NEEDS_REVIEW",
                parser_version=OCR_PARSER_VERSION,
                raw_text=parsed.raw_text,
                extracted_json=parsed.extracted_dict(),
                confidence_json=parsed.confidence,
                warnings_json=parsed.warnings,
                duplicate_of_id=semantic_duplicate.id if semantic_duplicate else None,
            )
            item_id = item.id
            db.add(item)
            commit_attempted = True
            db.commit()
            db.refresh(item)
            result = {**_statement_dict(item), "duplicate": False}
            if not _remove_upload_pending_marker(upload_pending_marker):
                result["upload_recovery_pending"] = True
            return result
        except Exception as upload_exc:
            rollback_error: Exception | None = None
            try:
                db.rollback()
            except Exception as exc:
                rollback_error = exc

            if commit_attempted and item_id is not None:
                try:
                    committed_result = _reconcile_statement_upload_commit_outcome(
                        import_id=item_id,
                        stored_path=stored_path,
                        expected_sha256=expected_sha256,
                    )
                except Exception as outcome_exc:
                    raise HTTPException(
                        status_code=500,
                        detail=(
                            "账单上传提交结果无法自动确认，原件已保留；"
                            "请立即停止操作并人工检查数据库及账单原件目录"
                        ),
                    ) from outcome_exc
                if committed_result is not None:
                    if not _remove_upload_pending_marker(upload_pending_marker):
                        committed_result["upload_recovery_pending"] = True
                    return committed_result

            try:
                _cleanup_new_upload_source(
                    path=expected_path,
                    expected_sha256=expected_sha256,
                    created=created_by_request,
                )
            except Exception as cleanup_exc:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "账单上传未完成，新原件已保留安全对账标记；"
                        "请停止操作，待数据库写锁释放后重启系统自动清理"
                    ),
                ) from cleanup_exc
            if not _remove_upload_pending_marker(upload_pending_marker):
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "账单上传未完成且安全对账标记暂未清理；"
                        "请停止操作并安全重启系统完成对账"
                    ),
                ) from upload_exc
            if rollback_error is not None:
                raise rollback_error
            raise upload_exc


@router.post("", status_code=201)
async def upload_statement(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件不能超过25MB")
    if not data:
        raise HTTPException(status_code=400, detail="文件为空")
    detected_format = detect_statement_format(data)
    if not detected_format:
        raise HTTPException(
            status_code=415,
            detail="文件内容不是有效的JPG、PNG或PDF；已拒绝伪装或未知格式文件",
        )
    detected_suffix, detected_mime = detected_format

    return await run_in_threadpool(
        _store_parse_and_create_statement,
        data=data,
        detected_suffix=detected_suffix,
        detected_mime=detected_mime,
        original_name=file.filename,
        db=db,
    )


@router.get("/{import_id}")
def get_statement_import(import_id: int, db: Session = Depends(get_db)) -> dict:
    return _statement_dict(_require_statement(db, import_id))


@router.delete("/{import_id}")
def delete_statement_import(
    import_id: int,
    payload: StatementDeleteRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict:
    """Delete an unconfirmed import or reverse an unused confirmed posting.

    The shared recognition lock prevents a delete from racing OCR, Luna, or
    confirmation.  BEGIN IMMEDIATE also serializes the reference checks against
    Settlement and other financial writes.
    """

    with _STATEMENT_RECOGNITION_LOCK:
        _begin_immediate(db)
        item = db.get(StatementImport, import_id)
        if item is None:
            db.rollback()
            raise HTTPException(status_code=404, detail="导入记录不存在")

        confirmed_snapshot: BalanceSnapshot | None = None
        if item.status == "CONFIRMED":
            if payload is None:
                db.rollback()
                raise HTTPException(status_code=422, detail="删除已确认入账记录必须填写原因")
            linked_snapshots = db.scalars(
                select(BalanceSnapshot).where(BalanceSnapshot.statement_import_id == item.id)
            ).all()
            if len(linked_snapshots) != 1:
                _delete_conflict(
                    db,
                    "已确认入账记录必须且只能关联一条余额快照，当前关系异常，已停止删除",
                )
            confirmed_snapshot = linked_snapshots[0]
            if (
                item.confirmed_snapshot_id != confirmed_snapshot.id
                or item.confirmed_account_id is None
                or item.confirmed_account_id != confirmed_snapshot.account_id
                or item.confirmed_at is None
                or confirmed_snapshot.source_type != "STATEMENT_IMPORT"
            ):
                _delete_conflict(
                    db,
                    "已确认入账记录与余额快照的双向关系不一致，已停止删除",
                )

            linked_settlement_id = db.scalar(
                select(SettlementAccountLine.settlement_id)
                .where(
                    (SettlementAccountLine.beginning_snapshot_id == confirmed_snapshot.id)
                    | (SettlementAccountLine.closing_snapshot_id == confirmed_snapshot.id)
                )
                .limit(1)
            )
            if linked_settlement_id is not None:
                _delete_conflict(
                    db,
                    f"余额快照已被Settlement #{linked_settlement_id}引用，不能撤销入账或删除",
                )

            logical_references = _statement_delete_logical_references(
                db,
                import_id=item.id,
                snapshot_id=confirmed_snapshot.id,
            )
            if logical_references:
                _delete_conflict(
                    db,
                    "已确认入账记录或余额快照已被以下资料引用，不能删除："
                    + "、".join(logical_references),
                )
        else:
            if item.status != "NEEDS_REVIEW" or any(
                value is not None
                for value in (
                    item.confirmed_account_id,
                    item.confirmed_snapshot_id,
                    item.confirmed_at,
                )
            ):
                _delete_conflict(
                    db,
                    "导入记录状态或确认痕迹异常，不能按未确认记录删除，请人工检查",
                )
            linked_snapshot_id = db.scalar(
                select(BalanceSnapshot.id)
                .where(BalanceSnapshot.statement_import_id == item.id)
                .limit(1)
            )
            if linked_snapshot_id is not None:
                _delete_conflict(db, "未确认导入记录已异常关联余额快照，不能删除")

        dependent_duplicate_id = db.scalar(
            select(StatementImport.id)
            .where(StatementImport.duplicate_of_id == item.id)
            .order_by(StatementImport.id)
            .limit(1)
        )
        if dependent_duplicate_id is not None:
            _delete_conflict(
                db,
                f"导入记录仍有后继重复记录 #{dependent_duplicate_id}，请先处理后继记录",
            )

        shared_source_id = db.scalar(
            select(StatementImport.id)
            .where(
                StatementImport.id != item.id,
                StatementImport.stored_path == item.stored_path,
            )
            .limit(1)
        )
        if shared_source_id is not None:
            _delete_conflict(
                db,
                f"导入原件路径同时被记录 #{shared_source_id}引用，已停止删除",
            )

        source_path, actual_source_sha256 = _source_path_for_delete(
            db,
            item=item,
            require_integrity=confirmed_snapshot is not None,
        )
        if source_path is not None:
            same_source_id = _same_source_reference_id(
                db,
                item=item,
                source_path=source_path,
            )
            if same_source_id is not None:
                _delete_conflict(
                    db,
                    f"导入原件物理文件同时被记录 #{same_source_id}引用，已停止删除",
                )
        source_file_found = source_path is not None
        previous_status = item.status
        source_sha256 = item.sha256
        original_name = item.original_name
        had_ai_recognition = item.ai_recognition_json is not None
        confirmed_account_id = item.confirmed_account_id
        confirmed_snapshot_id = confirmed_snapshot.id if confirmed_snapshot else None
        confirmed_snapshot_summary = (
            _confirmed_snapshot_audit_summary(db, snapshot=confirmed_snapshot)
            if confirmed_snapshot is not None
            else None
        )
        staged_path = _pending_source_path(source_path)
        source_cleanup_details = {
            "method": "ATOMIC_SAME_DIRECTORY_STAGE_THEN_UNLINK_AFTER_COMMIT",
            "staged_path": str(staged_path) if staged_path is not None else None,
            "sha256": actual_source_sha256,
        }

        try:
            if confirmed_snapshot is not None:
                item.confirmed_snapshot_id = None
                db.flush()
                snapshot_result = db.execute(
                    delete(BalanceSnapshot).where(BalanceSnapshot.id == confirmed_snapshot.id)
                )
                if snapshot_result.rowcount != 1:
                    _delete_conflict(db, "余额快照状态已变化，请刷新后重试")

                import_result = db.execute(
                    delete(StatementImport).where(StatementImport.id == item.id)
                )
                if import_result.rowcount != 1:
                    _delete_conflict(db, "导入记录状态已变化，请刷新后重试")
                db.add(
                    AuditEvent(
                        action="STATEMENT_IMPORT_CONFIRMATION_REVERSED_AND_DELETED",
                        entity_type="STATEMENT_IMPORT",
                        entity_id=None,
                        details_json={
                            "deleted_import_id": item.id,
                            "deleted_snapshot_id": confirmed_snapshot_id,
                            "confirmed_account_id": confirmed_account_id,
                            "reason": payload.reason,
                            "original_name": original_name,
                            "sha256": source_sha256,
                            "source_file_sha256": actual_source_sha256,
                            "previous_status": previous_status,
                            "had_ai_recognition": had_ai_recognition,
                            "source_file_found": source_file_found,
                            **confirmed_snapshot_summary,
                            "source_cleanup": source_cleanup_details,
                        },
                    )
                )
            else:
                db.add(
                    AuditEvent(
                        action="STATEMENT_IMPORT_DELETED",
                        entity_type="STATEMENT_IMPORT",
                        entity_id=None,
                        details_json={
                            "deleted_import_id": item.id,
                            "sha256": source_sha256,
                            "source_file_sha256": actual_source_sha256,
                            "previous_status": previous_status,
                            "had_ai_recognition": had_ai_recognition,
                            "source_file_found": source_file_found,
                            "source_cleanup": source_cleanup_details,
                        },
                    )
                )
                import_result = db.execute(
                    delete(StatementImport).where(StatementImport.id == item.id)
                )
                if import_result.rowcount != 1:
                    _delete_conflict(db, "导入记录状态已变化，请刷新后重试")

            db.flush()
            _stage_source_file(source_path, staged_path)
            _validate_staged_source(
                source_path=source_path,
                staged_path=staged_path,
                expected_sha256=actual_source_sha256,
            )
        except IntegrityError as exc:
            _rollback_and_restore_source(
                db,
                source_path=source_path,
                staged_path=staged_path,
                expected_sha256=actual_source_sha256,
            )
            raise HTTPException(
                status_code=409,
                detail="导入记录已被其他资料引用或状态已变化，不能删除",
            ) from exc
        except OperationalError as exc:
            _rollback_and_restore_source(
                db,
                source_path=source_path,
                staged_path=staged_path,
                expected_sha256=actual_source_sha256,
            )
            if _is_sqlite_busy(exc):
                raise HTTPException(
                    status_code=409,
                    detail="数据库正在处理另一笔财务写入，请稍后重试",
                ) from exc
            raise
        except SQLAlchemyError:
            _rollback_and_restore_source(
                db,
                source_path=source_path,
                staged_path=staged_path,
                expected_sha256=actual_source_sha256,
            )
            raise
        except OSError as exc:
            _rollback_and_restore_source(
                db,
                source_path=source_path,
                staged_path=staged_path,
                expected_sha256=actual_source_sha256,
            )
            raise HTTPException(status_code=409, detail="导入原件无法安全暂存，已停止删除") from exc

        try:
            db.commit()
        except Exception as commit_exc:
            rollback_error: Exception | None = None
            try:
                db.rollback()
            except Exception as exc:
                rollback_error = exc
            try:
                if source_path is not None and staged_path is not None and actual_source_sha256:
                    deletion_committed = reconcile_statement_delete_commit_outcome(
                        import_id=import_id,
                        original_path=source_path,
                        pending_path=staged_path,
                        expected_sha256=actual_source_sha256,
                    )
                else:
                    deletion_committed = (
                        reconcile_statement_delete_without_source_commit_outcome(
                            import_id=import_id
                        )
                    )
            except RuntimeError as outcome_exc:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        "删除提交结果无法自动确认，待处理原件已保留；"
                        "请立即停止操作并重启系统进行安全对账"
                    ),
                ) from outcome_exc

            if not deletion_committed:
                if rollback_error is not None:
                    raise rollback_error
                if isinstance(commit_exc, IntegrityError):
                    raise HTTPException(
                        status_code=409,
                        detail="导入记录已被其他资料引用或状态已变化，不能删除",
                    ) from commit_exc
                if isinstance(commit_exc, OperationalError) and _is_sqlite_busy(commit_exc):
                    raise HTTPException(
                        status_code=409,
                        detail="数据库正在处理另一笔财务写入，请稍后重试",
                    ) from commit_exc
                raise commit_exc

        source_file_deleted = False
        cleanup_pending = False
        cleanup_audit_failed = False
        if staged_path is not None:
            try:
                staged_path.unlink()
                source_file_deleted = True
            except OSError as cleanup_exc:
                try:
                    cleanup_pending = _path_exists_strict(staged_path)
                except OSError:
                    # If absence cannot be proven, preserve the safer pending state.
                    cleanup_pending = True
                source_file_deleted = not cleanup_pending
                if cleanup_pending:
                    db.add(
                        AuditEvent(
                            action="STATEMENT_IMPORT_SOURCE_FILE_CLEANUP_PENDING",
                            entity_type="STATEMENT_IMPORT",
                            entity_id=None,
                            details_json={
                                "deleted_import_id": import_id,
                                "deleted_snapshot_id": confirmed_snapshot_id,
                                "staged_path": str(staged_path),
                                "sha256": actual_source_sha256,
                                "recorded_sha256": source_sha256,
                                "cleanup_error_type": type(cleanup_exc).__name__,
                            },
                        )
                    )
                    try:
                        db.commit()
                    except OperationalError:
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        cleanup_audit_failed = True
                    except SQLAlchemyError:
                        try:
                            db.rollback()
                        except Exception:
                            pass
                        cleanup_audit_failed = True

        result = {
            "deleted": True,
            "import_id": import_id,
            "source_file_deleted": source_file_deleted,
        }
        if confirmed_snapshot_id is not None:
            result["snapshot_id"] = confirmed_snapshot_id
        if cleanup_pending:
            result["source_file_cleanup_pending"] = True
        if cleanup_audit_failed:
            result["cleanup_audit_failed"] = True
        return result


@router.get("/{import_id}/file")
def get_statement_file(import_id: int, db: Session = Depends(get_db)) -> FileResponse:
    item = _require_statement(db, import_id)
    path = Path(item.stored_path)
    settings = get_settings()
    if not path.exists() or not is_within(path, settings.data_root / "statement_imports"):
        raise HTTPException(status_code=404, detail="原始文件不存在")
    try:
        detected_format = detect_statement_format(path.read_bytes()[:8])
    except OSError as exc:
        raise HTTPException(status_code=404, detail="原始文件无法读取") from exc
    if not detected_format:
        raise HTTPException(status_code=415, detail="原始文件格式无法安全预览")
    _detected_suffix, detected_mime = detected_format
    response = FileResponse(
        path,
        media_type=detected_mime,
        filename=item.original_name,
        content_disposition_type="inline",
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/{import_id}/reparse")
def reparse_statement(import_id: int, db: Session = Depends(get_db)) -> dict:
    with _STATEMENT_RECOGNITION_LOCK:
        item = _require_statement(db, import_id)
        if item.status == "CONFIRMED":
            raise HTTPException(status_code=409, detail="已确认入账的文件不能重新识别")
        if item.ai_recognition_json is not None:
            raise HTTPException(
                status_code=409,
                detail="Luna已基于当前OCR结果完成比对；为保留审计基线，不能重新执行OCR",
            )
        parsed = parse_empf_statement(Path(item.stored_path))
        item.parser_name = f"LOCAL_OCR_{parsed.document_type.upper()}"
        item.parser_version = OCR_PARSER_VERSION
        item.raw_text = parsed.raw_text
        item.extracted_json = parsed.extracted_dict()
        item.confidence_json = parsed.confidence
        item.warnings_json = parsed.warnings
        db.commit()
        return _statement_dict(item)


@router.post("/{import_id}/ai-recognize")
def recognize_statement_with_ai(
    import_id: int,
    db: Session = Depends(get_db),
    _request_guard: None = Depends(require_financial_system_request),
) -> dict:
    """Create a separate Luna review candidate for an unconfirmed import.

    This route deliberately does not update OCR extraction, reviewed values,
    account data, balance snapshots, transactions, or settlements.
    """

    # The app-server is a single local channel. Holding this route-level lock
    # also makes the idempotency check atomic, so concurrent clicks cannot
    # consume the user's subscription twice for the same statement.
    with _STATEMENT_RECOGNITION_LOCK:
        item = _require_statement(db, import_id)
        if item.status == "CONFIRMED":
            raise HTTPException(status_code=409, detail="已确认入账的文件不能再进行AI识别")
        if item.ai_recognition_json is not None:
            return {**_statement_dict(item), "idempotent": True}
        path = Path(item.stored_path)
        statement_root = get_settings().data_root / "statement_imports"
        if not path.exists() or not is_within(path, statement_root):
            raise HTTPException(status_code=404, detail="原始文件不存在或路径不安全")
        ocr_values = dict(item.extracted_json or {})

        try:
            ai_values = get_codex_app_server().recognize_statement(path)
        except CodexIntegrationError as exc:
            raise_ai_http_error(exc)

        review = build_ai_review_result(ocr_values, ai_values)
        recognized_at = datetime.now(timezone.utc)
        item.ai_recognition_json = review
        item.ai_status = review["status"]
        item.ai_model = FIXED_AI_MODEL
        item.ai_recognized_at = recognized_at
        db.add(
            AuditEvent(
                action="STATEMENT_AI_RECOGNIZED",
                entity_type="STATEMENT_IMPORT",
                entity_id=item.id,
                details_json={
                    "model": FIXED_AI_MODEL,
                    "parser_version": AI_PARSER_VERSION,
                    "status": review["status"],
                    "conflict_fields": [value["field"] for value in review["conflicts"]],
                    "model_escalation": "DISABLED",
                    "financial_data_mutated": False,
                },
            )
        )
        db.commit()
        db.refresh(item)
        return _statement_dict(item)


def _platform_for_statement(db: Session, scheme_name: str | None, trustee: str | None) -> Platform:
    target_name = scheme_name or trustee or "eMPF 待确认平台"
    existing = db.scalar(select(Platform).where(Platform.name == target_name))
    if existing:
        return existing
    base_code = "EMPF"
    code = base_code
    suffix = 1
    while db.scalar(select(Platform).where(Platform.code == code)):
        suffix += 1
        code = f"{base_code}-{suffix}"
    item = Platform(name=target_name, code=code, trustee=trustee, remark="由eMPF账单导入自动创建，待财务确认")
    db.add(item)
    db.flush()
    return item


def _normalized_holdings(value: object) -> list[dict]:
    """Canonicalize every source through the same strict finance schema."""

    rows = value if isinstance(value, list) else []
    try:
        return [
            StatementHoldingInput.model_validate(row).model_dump(mode="json")
            for row in rows
        ]
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="已识别的持仓数据格式异常，财务必须重新核对并提交持仓明细",
        ) from exc


@router.post("/{import_id}/confirm")
def confirm_statement(
    import_id: int,
    payload: StatementConfirmRequest,
    db: Session = Depends(get_db),
) -> dict:
    # Confirmation, OCR reparse, and Luna recognition all share one critical
    # section. A snapshot can therefore never be posted from a baseline that
    # changes while an independent recognition is still in flight.
    with _STATEMENT_RECOGNITION_LOCK:
        return _confirm_statement_locked(import_id, payload, db)


def _confirm_statement_locked(
    import_id: int,
    payload: StatementConfirmRequest,
    db: Session,
) -> dict:
    _begin_immediate(db)
    item = _require_statement(db, import_id)
    if item.status == "CONFIRMED":
        raise HTTPException(status_code=409, detail="该文件已经确认入账")

    extracted = item.extracted_json or {}
    document_type = extracted.get("document_type")
    ai_review = item.ai_recognition_json or {}
    ai_values = ai_review.get("values") or ai_review.get("extracted") or {}
    luna_document_type = ai_values.get("document_type") if isinstance(ai_values, dict) else None
    luna_balance_page_override = (
        document_type == "unknown"
        and luna_document_type == "empf_account_page"
        and item.ai_status in {"AGREED", "CONFLICT", "INCOMPLETE"}
    )
    if document_type != "empf_account_page" and not luna_balance_page_override:
        label = DOCUMENT_TYPE_LABELS.get(str(document_type), DOCUMENT_TYPE_LABELS["unknown"])
        raise HTTPException(
            status_code=409,
            detail=f"该文件被识别为“{label}”，不是账户余额页面，禁止生成余额快照",
        )
    if luna_balance_page_override and payload.luna_document_type_reviewed is not True:
        raise HTTPException(
            status_code=409,
            detail="本地OCR未能确认文档类型；财务必须查看原件并勾选已确认采用Luna余额页分类",
        )

    ai_requires_acknowledgement = bool(
        item.ai_recognition_json
        and (
            item.ai_status != "AGREED"
            or ai_review.get("conflicts")
            or ai_review.get("uncorroborated")
            or ai_review.get("validation_failures")
            or ai_review.get("uncertain_critical_fields")
        )
    )
    if ai_requires_acknowledgement and payload.ai_conflicts_reviewed is not True:
        raise HTTPException(
            status_code=409,
            detail="Luna识别存在冲突、不确定或校验异常；财务必须勾选已逐项人工核对后才能入账",
        )

    account: SubAccount | None = None
    if payload.account_id:
        account = db.get(SubAccount, payload.account_id)
        if not account:
            raise HTTPException(status_code=404, detail="指定的Sub Account不存在")
        if payload.account_platform_id is None:
            raise HTTPException(status_code=400, detail="明确选择已有Sub Account时必须同时确认Platform")
        if account.platform_id != payload.account_platform_id:
            raise HTTPException(status_code=409, detail="所选Sub Account的Platform已变化，请刷新后重新选择")
        if account.account_number != payload.account_number:
            raise HTTPException(status_code=400, detail="Account Number与指定账户不一致")
    elif payload.account_platform_id is not None:
        raise HTTPException(status_code=400, detail="未选择Sub Account时不能单独提交Platform")
    else:
        candidates = db.scalars(
            select(SubAccount).where(SubAccount.account_number == payload.account_number)
        ).all()
        if len(candidates) == 1:
            account = candidates[0]
        elif len(candidates) > 1:
            scheme_matches = [
                candidate
                for candidate in candidates
                if payload.scheme_name
                and candidate.scheme_name
                and candidate.scheme_name.strip().casefold() == payload.scheme_name.strip().casefold()
            ]
            if len(scheme_matches) == 1:
                account = scheme_matches[0]
            else:
                raise HTTPException(
                    status_code=409,
                    detail="多个Platform存在相同Account Number，请在复核页明确选择Sub Account",
                )

    created_draft = False
    if not account:
        platform = _platform_for_statement(db, payload.scheme_name, payload.trustee)
        client = Client(
            id=_next_entity_id(db, Client),
            name=payload.client_name,
            status="DRAFT",
            remark="由eMPF账单导入创建，待补全Company和FC",
        )
        db.add(client)
        db.flush()
        account = SubAccount(
            id=_next_entity_id(db, SubAccount),
            client_id=client.id,
            platform_id=platform.id,
            fee_plan_id=None,
            account_number=payload.account_number,
            scheme_name=payload.scheme_name,
            currency="HKD",
            status="DRAFT",
            remark="由eMPF账单导入创建，待补全Fee Plan",
        )
        db.add(account)
        db.flush()
        created_draft = True
    elif account.client.name.strip().casefold() != payload.client_name.strip().casefold():
        raise HTTPException(status_code=409, detail="Account Number已存在，但Client Name不一致")
    elif (
        payload.scheme_name
        and account.scheme_name
        and account.scheme_name.strip().casefold() != payload.scheme_name.strip().casefold()
    ):
        raise HTTPException(status_code=409, detail="账单Scheme与选定Sub Account不一致")

    recognized_holdings = _normalized_holdings(extracted.get("holdings"))
    ai_holdings = _normalized_holdings(
        ai_values.get("holdings") if isinstance(ai_values, dict) else None
    )
    submitted_holdings = (
        _normalized_holdings([holding.model_dump(mode="json") for holding in payload.holdings])
        if payload.holdings is not None
        else None
    )
    confirmed_holdings = submitted_holdings if submitted_holdings is not None else recognized_holdings
    if submitted_holdings is None:
        holdings_source = "LOCAL_OCR_DEFAULT"
    elif submitted_holdings == recognized_holdings:
        holdings_source = "LOCAL_OCR_SELECTED"
    elif item.ai_recognition_json is not None and submitted_holdings == ai_holdings:
        holdings_source = "LUNA_SELECTED"
    else:
        holdings_source = "FINANCE_EDITED"

    eligible = is_quarter_end(payload.as_of_date) or (account.end_date == payload.as_of_date)
    snapshot = BalanceSnapshot(
        id=_next_entity_id(db, BalanceSnapshot),
        account_id=account.id,
        as_of_date=payload.as_of_date,
        total_balance_cents=to_cents(payload.total_balance),
        currency="HKD",
        source_type="STATEMENT_IMPORT",
        statement_import_id=item.id,
        holdings_json=confirmed_holdings,
        eligible_for_closing=eligible,
        remark="季末/退出日Closing候选" if eligible else "非季末余额快照，不可直接作为Closing",
    )
    db.add(snapshot)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="该账户在同一天已经有余额快照") from exc

    reviewed_values = {
        **extracted,
        **payload.model_dump(
            mode="json",
            exclude={
                "account_id",
                "account_platform_id",
                "ai_conflicts_reviewed",
                "luna_document_type_reviewed",
                "holdings",
            },
        ),
        "document_type": "empf_account_page",
        "holdings": confirmed_holdings,
    }
    changes = {
        key: {"recognized": extracted.get(key), "confirmed": value}
        for key, value in reviewed_values.items()
        if extracted.get(key) != value and key != "holdings"
    }
    changes["holdings"] = {
        "source": holdings_source,
        "recognized_count": len(recognized_holdings),
        "luna_count": len(ai_holdings),
        "confirmed_count": len(confirmed_holdings),
        "changed": recognized_holdings != confirmed_holdings,
    }
    item.reviewed_json = reviewed_values
    confirmed_at = datetime.now(timezone.utc)
    item.revision_log_json = [
        *((item.revision_log_json or [])),
        {
            "reviewed_at": confirmed_at.isoformat(),
            "changes": changes,
            "ai_review_acknowledged": bool(payload.ai_conflicts_reviewed),
            "luna_document_type_reviewed": bool(payload.luna_document_type_reviewed),
        },
    ]
    item.status = "CONFIRMED"
    item.confirmed_account_id = account.id
    item.confirmed_snapshot_id = snapshot.id
    item.confirmed_at = confirmed_at
    db.add(
        AuditEvent(
            action="STATEMENT_CONFIRMED",
            entity_type="STATEMENT_IMPORT",
            entity_id=item.id,
            details_json={
                "account_id": account.id,
                "snapshot_id": snapshot.id,
                "changes": changes,
                "ai_status": item.ai_status,
                "ai_review_acknowledged": bool(payload.ai_conflicts_reviewed),
                "document_type_source": (
                    "LUNA_HUMAN_CONFIRMED" if luna_balance_page_override else "LOCAL_OCR"
                ),
            },
        )
    )
    db.commit()
    db.refresh(snapshot)
    return {
        "statement_import": _statement_dict(item),
        "account_id": account.id,
        "created_draft": created_draft,
        "snapshot": {
            "id": snapshot.id,
            "as_of_date": snapshot.as_of_date.isoformat(),
            "total_balance": money_string(snapshot.total_balance_cents),
            "eligible_for_closing": snapshot.eligible_for_closing,
        },
    }
