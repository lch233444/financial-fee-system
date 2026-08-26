from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from ..config import get_settings
from .storage import sha256_file


INCLUDED_DIRECTORIES = ("attachments", "statement_imports", "output")


def create_backup() -> Path:
    settings = get_settings()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = settings.data_root / "backups" / f"financial_system_backup_{timestamp}.zip"
    output_path.parent.mkdir(parents=True, exist_ok=True)

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

        files: list[dict] = []
        for file_path in [db_copy]:
            files.append({"path": file_path.relative_to(tmp).as_posix(), "sha256": sha256_file(file_path)})
        for directory_name in INCLUDED_DIRECTORIES:
            directory = settings.data_root / directory_name
            if not directory.exists():
                continue
            for file_path in directory.rglob("*"):
                if file_path.is_file():
                    relative = file_path.relative_to(settings.data_root)
                    destination = tmp / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, destination)
                    files.append({"path": relative.as_posix(), "sha256": sha256_file(destination)})

        manifest = {
            "format": "financial-fee-system-backup",
            "version": 1,
            "created_at": datetime.now().isoformat(),
            "files": files,
        }
        (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in tmp.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(tmp).as_posix())
    return output_path


def validate_backup(backup_path: Path, extract_root: Path) -> dict:
    with zipfile.ZipFile(backup_path) as archive:
        for member in archive.infolist():
            member_path = (extract_root / member.filename).resolve()
            try:
                member_path.relative_to(extract_root.resolve())
            except ValueError as exc:
                raise ValueError("备份文件包含不安全路径") from exc
        archive.extractall(extract_root)
    manifest_path = extract_root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("备份缺少manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "financial-fee-system-backup" or manifest.get("version") != 1:
        raise ValueError("不支持的备份格式")
    for record in manifest.get("files", []):
        file_path = extract_root / record["path"]
        if not file_path.exists() or sha256_file(file_path) != record["sha256"]:
            raise ValueError(f"备份校验失败：{record['path']}")
    return manifest


def stage_restore(backup_path: Path) -> Path:
    settings = get_settings()
    pending_root = settings.data_root / "tmp" / "pending_restore"
    if pending_root.exists():
        shutil.rmtree(pending_root)
    pending_root.mkdir(parents=True)
    validate_backup(backup_path, pending_root)
    marker = settings.data_root / "pending_restore.json"
    marker.write_text(
        json.dumps({"path": str(pending_root), "staged_at": datetime.now().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )
    return marker


def apply_pending_restore() -> bool:
    settings = get_settings()
    marker = settings.data_root / "pending_restore.json"
    if not marker.exists():
        return False
    payload = json.loads(marker.read_text(encoding="utf-8"))
    pending_root = Path(payload["path"])
    validate_backup_archive_root(pending_root)
    restored_db = pending_root / "database" / settings.database_path.name
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(restored_db, settings.database_path)
    for directory_name in INCLUDED_DIRECTORIES:
        source = pending_root / directory_name
        destination = settings.data_root / directory_name
        if destination.exists():
            shutil.rmtree(destination)
        if source.exists():
            shutil.copytree(source, destination)
        else:
            destination.mkdir(parents=True, exist_ok=True)
    marker.unlink(missing_ok=True)
    shutil.rmtree(pending_root, ignore_errors=True)
    return True


def validate_backup_archive_root(root: Path) -> None:
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        raise ValueError("待恢复目录缺少manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest.get("files", []):
        file_path = root / record["path"]
        if not file_path.exists() or sha256_file(file_path) != record["sha256"]:
            raise ValueError(f"待恢复文件校验失败：{record['path']}")
