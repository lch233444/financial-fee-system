from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import warnings
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, engine, init_db
from app import main as app_main
from app.main import app
from app.models import AppSetting
from app.routes import system as system_routes
from app.services import backup as backup_service
from app.services.backup import apply_pending_restore, create_backup, stage_restore, validate_backup_archive_root
from app.services.shutdown import get_shutdown_coordinator
from app.services.storage import sha256_file


WRITE_HEADERS = {"X-Financial-System-Request": "1"}


@pytest.fixture(autouse=True)
def clean_pending_restore_artifacts() -> None:
    settings = get_settings()
    marker = settings.data_root / "pending_restore.json"
    marker.unlink(missing_ok=True)
    for pattern in ("pending_restore*", "restore_prepare_*", "restore_apply_*"):
        for path in (settings.data_root / "tmp").glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    yield
    marker.unlink(missing_ok=True)
    for pattern in ("pending_restore*", "restore_prepare_*", "restore_apply_*"):
        for path in (settings.data_root / "tmp").glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def _valid_backup() -> Path:
    init_db()
    return create_backup()


def _archive_files(backup_path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(backup_path) as archive:
        return {member.filename: archive.read(member) for member in archive.infolist() if not member.is_dir()}


def _write_variant(
    source: Path,
    destination: Path,
    mutate: Callable[[dict[str, bytes], dict], None],
) -> Path:
    files = _archive_files(source)
    manifest = json.loads(files["manifest.json"])
    mutate(files, manifest)
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in files.items():
            archive.writestr(relative_path, content)
    return destination


def _assert_restore_rejected(backup_path: Path) -> None:
    settings = get_settings()
    marker = settings.data_root / "pending_restore.json"
    with pytest.raises(ValueError):
        stage_restore(backup_path)
    assert not marker.exists()
    assert not list((settings.data_root / "tmp").glob("pending_restore*"))


def test_backup_creation_uses_unique_atomic_names_within_same_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 31, 20, 30, 40, 123456, tzinfo=tz)

    monkeypatch.setattr(backup_service, "datetime", FrozenDateTime)
    first = create_backup()
    second = create_backup()

    assert first != second
    assert first.name.startswith("financial_system_backup_20260831_203040_123456_")
    assert second.name.startswith("financial_system_backup_20260831_203040_123456_")
    for backup_path in (first, second):
        assert backup_path.is_file()
        with zipfile.ZipFile(backup_path) as archive:
            assert archive.testzip() is None
            assert "manifest.json" in archive.namelist()
    assert not list(first.parent.glob(".financial_system_backup_*.zip.tmp"))


def test_backup_creation_does_not_publish_or_leave_temp_when_self_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_db()
    settings = get_settings()
    before = set((settings.data_root / "backups").glob("financial_system_backup_*.zip"))

    def reject_backup(_backup_path: Path, _extract_root: Path) -> dict:
        raise ValueError("synthetic validation failure")

    monkeypatch.setattr(backup_service, "validate_backup", reject_backup)
    with pytest.raises(ValueError, match="synthetic validation failure"):
        create_backup()

    after = set((settings.data_root / "backups").glob("financial_system_backup_*.zip"))
    assert after == before
    assert not list((settings.data_root / "backups").glob(".financial_system_backup_*.zip.tmp"))


def test_restore_request_is_exclusive_before_upload_read() -> None:
    backup_path = _valid_backup()
    with system_routes._restore_request_lock:
        with TestClient(app) as client:
            response = client.post(
                "/api/backups/restore",
                headers=WRITE_HEADERS,
                files={"file": (backup_path.name, backup_path.read_bytes(), "application/zip")},
            )
    assert response.status_code == 409
    assert "另一份备份正在校验" in response.json()["detail"]


def test_financial_mutation_gate_rejects_write_already_overlapping_restore() -> None:
    assert app_main._restore_mutation_gate.try_enter_restore() is True
    try:
        with TestClient(app) as client:
            response = client.post("/api/transactions", headers=WRITE_HEADERS, json={})
    finally:
        app_main._restore_mutation_gate.leave_restore()
    assert response.status_code == 409
    assert "恢复正在校验" in response.json()["detail"]


def test_restore_gate_rejects_restore_while_financial_write_is_active() -> None:
    assert app_main._restore_mutation_gate.try_enter_mutation() is True
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/backups/restore",
                headers=WRITE_HEADERS,
                files={"file": ("synthetic.zip", b"not-read", "application/zip")},
            )
    finally:
        app_main._restore_mutation_gate.leave_mutation()
    assert response.status_code == 409
    assert "仍有财务写入" in response.json()["detail"]


def test_successful_restore_staging_schedules_shutdown_and_blocks_later_writes() -> None:
    backup_path = _valid_backup()

    class FakeShutdownCoordinator:
        def __init__(self) -> None:
            self.events: list[str] = []

        def request(self) -> bool:
            self.events.append("request")
            return True

        def execute(self) -> None:
            self.events.append("execute")

    fake = FakeShutdownCoordinator()
    app.dependency_overrides[get_shutdown_coordinator] = lambda: fake
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/backups/restore",
                headers=WRITE_HEADERS,
                files={"file": (backup_path.name, backup_path.read_bytes(), "application/zip")},
            )
            assert response.status_code == 200
            assert response.json()["staged"] is True
            assert response.json()["shutdown_scheduled"] is True

            blocked = client.post("/api/transactions", headers=WRITE_HEADERS, json={})
            assert blocked.status_code == 409
            assert "已停止接受财务写入" in blocked.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_shutdown_coordinator, None)

    assert fake.events == ["request", "execute"]


def test_backup_restore_preserves_database_files_and_hashes() -> None:
    init_db()
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "backup-roundtrip.txt"
    attachment.write_text("original attachment", encoding="utf-8")

    with SessionLocal() as db:
        existing = db.get(AppSetting, "backup_roundtrip")
        if existing:
            existing.value = "original database value"
        else:
            db.add(AppSetting(key="backup_roundtrip", value="original database value"))
        db.commit()

    backup_path = create_backup()
    with zipfile.ZipFile(backup_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for record in manifest["files"]:
            extracted = settings.data_root / "tmp" / "hash-check" / record["path"]
            extracted.parent.mkdir(parents=True, exist_ok=True)
            extracted.write_bytes(archive.read(record["path"]))
            assert sha256_file(extracted) == record["sha256"]

    with SessionLocal() as db:
        db.get(AppSetting, "backup_roundtrip").value = "mutated database value"
        db.commit()
    attachment.write_text("mutated attachment", encoding="utf-8")

    stage_restore(backup_path)
    engine.dispose()
    assert apply_pending_restore() is True
    engine.dispose()

    with SessionLocal() as db:
        restored = db.scalar(select(AppSetting).where(AppSetting.key == "backup_roundtrip"))
        assert restored is not None
        assert restored.value == "original database value"
    assert attachment.read_text(encoding="utf-8") == "original attachment"


def test_restore_rejects_backup_without_database(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def remove_database(files: dict[str, bytes], manifest: dict) -> None:
        database_paths = [record["path"] for record in manifest["files"] if record["path"].startswith("database/")]
        for relative_path in database_paths:
            files.pop(relative_path)
        manifest["files"] = [
            record for record in manifest["files"] if not record["path"].startswith("database/")
        ]

    invalid_backup = _write_variant(backup_path, tmp_path / "missing-database.zip", remove_database)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_empty_manifest_files(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def empty_files(files: dict[str, bytes], manifest: dict) -> None:
        files.clear()
        manifest["files"] = []

    invalid_backup = _write_variant(backup_path, tmp_path / "empty-files.zip", empty_files)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_duplicate_manifest_path(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def duplicate_record(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"].append(dict(manifest["files"][0]))

    invalid_backup = _write_variant(backup_path, tmp_path / "duplicate-path.zip", duplicate_record)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_manifest_record_with_extra_field(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def add_record_field(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["size"] = 123

    invalid_backup = _write_variant(backup_path, tmp_path / "record-extra-field.zip", add_record_field)
    _assert_restore_rejected(invalid_backup)


@pytest.mark.parametrize("invalid_version", [True, 1.0])
def test_restore_rejects_non_integer_manifest_version(tmp_path: Path, invalid_version: object) -> None:
    backup_path = _valid_backup()

    def change_version(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["version"] = invalid_version

    invalid_backup = _write_variant(backup_path, tmp_path / f"invalid-version-{invalid_version}.zip", change_version)
    _assert_restore_rejected(invalid_backup)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../database/financial_system.sqlite3",
        "attachments/CON",
        "attachments/trailing-dot.",
        "attachments/control\x01.txt",
    ],
)
def test_restore_rejects_unsafe_manifest_path(tmp_path: Path, unsafe_path: str) -> None:
    backup_path = _valid_backup()

    def change_path(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["path"] = unsafe_path

    invalid_backup = _write_variant(backup_path, tmp_path / "unsafe-manifest-path.zip", change_path)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_case_insensitive_duplicate_manifest_path(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def duplicate_record_with_different_case(_files: dict[str, bytes], manifest: dict) -> None:
        duplicate = dict(manifest["files"][0])
        parent, filename = duplicate["path"].split("/", 1)
        duplicate["path"] = f"{parent}/{filename.upper()}"
        manifest["files"].append(duplicate)

    invalid_backup = _write_variant(
        backup_path,
        tmp_path / "case-insensitive-duplicate-path.zip",
        duplicate_record_with_different_case,
    )
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_duplicate_archive_member(tmp_path: Path) -> None:
    backup_path = _valid_backup()
    files = _archive_files(backup_path)
    duplicate_path = next(relative_path for relative_path in files if relative_path != "manifest.json")
    invalid_backup = tmp_path / "duplicate-archive-member.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(invalid_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative_path, content in files.items():
                archive.writestr(relative_path, content)
            archive.writestr(duplicate_path, files[duplicate_path])

    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_unlisted_archive_file(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def add_unlisted_file(files: dict[str, bytes], _manifest: dict) -> None:
        files["attachments/not-in-manifest.txt"] = b"unlisted"

    invalid_backup = _write_variant(backup_path, tmp_path / "unlisted-file.zip", add_unlisted_file)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_hash_mismatch(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def change_hash(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["sha256"] = "0" * 64

    invalid_backup = _write_variant(backup_path, tmp_path / "bad-hash.zip", change_hash)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_corrupt_sqlite_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def corrupt_database(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        corrupt_content = b"this is not a sqlite database"
        files[database_record["path"]] = corrupt_content
        database_record["sha256"] = hashlib.sha256(corrupt_content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "corrupt-database.zip", corrupt_database)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_foreign_key_violation_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def break_foreign_key(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        database_copy = tmp_path / "foreign-key-violation.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys = OFF;
                CREATE TABLE backup_fk_parent (id INTEGER PRIMARY KEY);
                CREATE TABLE backup_fk_child (
                    id INTEGER PRIMARY KEY,
                    parent_id INTEGER NOT NULL REFERENCES backup_fk_parent(id)
                );
                INSERT INTO backup_fk_child(id, parent_id) VALUES (1, 999);
                """
            )
            connection.commit()
        finally:
            connection.close()
        corrupt_content = database_copy.read_bytes()
        files[database_record["path"]] = corrupt_content
        database_record["sha256"] = hashlib.sha256(corrupt_content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "foreign-key-violation.zip", break_foreign_key)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_non_system_sqlite_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def replace_with_unrelated_database(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        unrelated_database = tmp_path / "unrelated.sqlite3"
        connection = sqlite3.connect(unrelated_database)
        try:
            connection.execute("CREATE TABLE unrelated_data (id INTEGER PRIMARY KEY, value TEXT)")
            connection.commit()
        finally:
            connection.close()
        content = unrelated_database.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "unrelated-database.zip", replace_with_unrelated_database)
    _assert_restore_rejected(invalid_backup)


def test_restore_rejects_unknown_database_revision_with_matching_hash(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def replace_revision(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        database_copy = tmp_path / "future-revision.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.execute("UPDATE alembic_version SET version_num = 'future_revision_not_supported'")
            connection.commit()
        finally:
            connection.close()
        content = database_copy.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    invalid_backup = _write_variant(backup_path, tmp_path / "future-revision.zip", replace_revision)
    _assert_restore_rejected(invalid_backup)


def test_restore_accepts_recognizable_unversioned_legacy_database(tmp_path: Path) -> None:
    backup_path = _valid_backup()

    def remove_revision_table(files: dict[str, bytes], manifest: dict) -> None:
        database_record = next(record for record in manifest["files"] if record["path"].startswith("database/"))
        database_copy = tmp_path / "unversioned-legacy.sqlite3"
        database_copy.write_bytes(files[database_record["path"]])
        connection = sqlite3.connect(database_copy)
        try:
            connection.execute("DROP TABLE alembic_version")
            connection.commit()
        finally:
            connection.close()
        content = database_copy.read_bytes()
        files[database_record["path"]] = content
        database_record["sha256"] = hashlib.sha256(content).hexdigest()

    legacy_backup = _write_variant(backup_path, tmp_path / "unversioned-legacy.zip", remove_revision_table)
    staged = stage_restore(legacy_backup)
    assert staged.marker.is_file()


def test_restore_rejects_unsafe_archive_path_before_extraction(tmp_path: Path) -> None:
    backup_path = _valid_backup()
    files = _archive_files(backup_path)
    invalid_backup = tmp_path / "unsafe-path.zip"
    with zipfile.ZipFile(invalid_backup, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative_path, content in files.items():
            archive.writestr(relative_path, content)
        archive.writestr("../escaped.txt", b"must not be extracted")

    _assert_restore_rejected(invalid_backup)
    assert not (get_settings().data_root / "tmp" / "escaped.txt").exists()


def test_restore_rejects_invalid_zip(tmp_path: Path) -> None:
    invalid_backup = tmp_path / "not-a-zip.zip"
    invalid_backup.write_bytes(b"not a zip archive")
    _assert_restore_rejected(invalid_backup)


def test_failed_stage_preserves_existing_valid_pending_restore(tmp_path: Path) -> None:
    valid_backup = _valid_backup()
    marker = stage_restore(valid_backup).marker
    original_marker = marker.read_text(encoding="utf-8")
    original_pending_root = Path(json.loads(original_marker)["path"])
    original_pending_files = sorted(path.relative_to(original_pending_root) for path in original_pending_root.rglob("*"))

    def change_hash(_files: dict[str, bytes], manifest: dict) -> None:
        manifest["files"][0]["sha256"] = "f" * 64

    invalid_backup = _write_variant(valid_backup, tmp_path / "replacement-invalid.zip", change_hash)
    with pytest.raises(ValueError):
        stage_restore(invalid_backup)

    assert marker.read_text(encoding="utf-8") == original_marker
    assert original_pending_root.is_dir()
    assert sorted(path.relative_to(original_pending_root) for path in original_pending_root.rglob("*")) == original_pending_files
    validate_backup_archive_root(original_pending_root)


def test_successful_stage_replaces_marker_and_removes_previous_pending_tree() -> None:
    backup_path = _valid_backup()
    marker = stage_restore(backup_path).marker
    previous_pending_root = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])

    stage_restore(backup_path)

    current_pending_root = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])
    assert current_pending_root != previous_pending_root
    assert current_pending_root.is_dir()
    assert not previous_pending_root.exists()
    validate_backup_archive_root(current_pending_root)


def test_stage_reports_old_pending_cleanup_failure_as_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    backup_path = _valid_backup()
    first_stage = stage_restore(backup_path)
    previous_pending_root = Path(json.loads(first_stage.marker.read_text(encoding="utf-8"))["path"])
    real_cleanup = backup_service._remove_obsolete_pending_root

    def fail_cleanup(path: Path) -> None:
        assert Path(path).resolve() == previous_pending_root.resolve()
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(backup_service, "_remove_obsolete_pending_root", fail_cleanup)
    replacement = stage_restore(backup_path)

    current_pending_root = Path(json.loads(replacement.marker.read_text(encoding="utf-8"))["path"])
    assert current_pending_root != previous_pending_root
    assert current_pending_root.is_dir()
    assert previous_pending_root.is_dir()
    assert replacement.cleanup_warning is not None
    assert "新备份已安全暂存" in replacement.cleanup_warning
    validate_backup_archive_root(current_pending_root)

    monkeypatch.setattr(backup_service, "_remove_obsolete_pending_root", real_cleanup)
    engine.dispose()
    assert apply_pending_restore() is True
    assert not previous_pending_root.exists()


def test_startup_removes_unreferenced_restore_upload() -> None:
    settings = get_settings()
    upload_path = settings.data_root / "tmp" / f"restore_{'a' * 64}.zip"
    upload_path.write_bytes(b"orphaned upload")

    assert apply_pending_restore() is False
    assert not upload_path.exists()


def test_apply_revalidates_staged_backup_and_keeps_marker_on_failure() -> None:
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "apply-revalidation.txt"
    attachment.write_text("original", encoding="utf-8")
    marker = stage_restore(_valid_backup()).marker
    pending_root = Path(json.loads(marker.read_text(encoding="utf-8"))["path"])
    staged_attachment = pending_root / "attachments" / attachment.name
    staged_attachment.write_text("tampered after staging", encoding="utf-8")

    with pytest.raises(ValueError):
        apply_pending_restore()

    assert marker.exists()
    assert pending_root.exists()
    assert attachment.read_text(encoding="utf-8") == "original"


@pytest.mark.parametrize(
    "marker_text, expected_message",
    [
        (
            '{"path":"F:/first","path":"F:/second","staged_at":"2026-08-31T12:00:00"}',
            "待恢复标记无效",
        ),
        (
            '{"path":"F:/pending_restore","staged_at":"not-a-time"}',
            "待恢复标记时间无效",
        ),
    ],
)
def test_apply_rejects_ambiguous_or_invalid_pending_marker(marker_text: str, expected_message: str) -> None:
    marker = get_settings().data_root / "pending_restore.json"
    marker.write_text(marker_text, encoding="utf-8")

    with pytest.raises(ValueError, match=expected_message):
        apply_pending_restore()


def test_apply_component_failure_rolls_back_all_live_data(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "apply-rollback.txt"
    attachment.write_text("backup attachment", encoding="utf-8")
    with SessionLocal() as db:
        existing = db.get(AppSetting, "apply_rollback")
        if existing is None:
            db.add(AppSetting(key="apply_rollback", value="backup database"))
        else:
            existing.value = "backup database"
        db.commit()
    backup_path = create_backup()

    with SessionLocal() as db:
        db.get(AppSetting, "apply_rollback").value = "live database"
        db.commit()
    attachment.write_text("live attachment", encoding="utf-8")
    marker = stage_restore(backup_path).marker
    real_activate = backup_service._activate_restore_component

    def fail_on_attachments(component: str, data_root: Path, transaction_root: Path) -> None:
        if component == "attachments":
            raise OSError("simulated component switch failure")
        real_activate(component, data_root, transaction_root)

    monkeypatch.setattr(backup_service, "_activate_restore_component", fail_on_attachments)
    engine.dispose()
    with pytest.raises(OSError, match="simulated component switch failure"):
        apply_pending_restore()
    engine.dispose()

    with SessionLocal() as db:
        assert db.get(AppSetting, "apply_rollback").value == "live database"
    assert attachment.read_text(encoding="utf-8") == "live attachment"
    assert marker.exists()
    assert not list((settings.data_root / "tmp").glob("restore_apply_*"))


@pytest.mark.parametrize("interrupt_after_install", [False, True])
def test_apply_recovers_interrupted_atomic_replace_before_retry(
    monkeypatch: pytest.MonkeyPatch,
    interrupt_after_install: bool,
) -> None:
    class SimulatedPowerLoss(BaseException):
        pass

    init_db()
    settings = get_settings()
    attachment = settings.data_root / "attachments" / "apply-interrupted.txt"
    attachment.write_text("backup attachment", encoding="utf-8")
    with SessionLocal() as db:
        existing = db.get(AppSetting, "apply_interrupted")
        if existing is None:
            db.add(AppSetting(key="apply_interrupted", value="backup database"))
        else:
            existing.value = "backup database"
        db.commit()
    backup_path = create_backup()

    with SessionLocal() as db:
        db.get(AppSetting, "apply_interrupted").value = "live database"
        db.commit()
    attachment.write_text("live attachment", encoding="utf-8")
    stage_restore(backup_path)
    real_replace = backup_service._replace_restore_directory
    replace_count = 0

    def interrupt_second_replace(source: Path, destination: Path) -> None:
        nonlocal replace_count
        replace_count += 1
        if replace_count == 2 and not interrupt_after_install:
            raise SimulatedPowerLoss()
        real_replace(source, destination)
        if replace_count == 2 and interrupt_after_install:
            raise SimulatedPowerLoss()

    monkeypatch.setattr(backup_service, "_replace_restore_directory", interrupt_second_replace)
    engine.dispose()
    with pytest.raises(SimulatedPowerLoss):
        apply_pending_restore()
    assert list((settings.data_root / "tmp").glob("restore_apply_*"))

    monkeypatch.setattr(backup_service, "_replace_restore_directory", real_replace)
    assert apply_pending_restore() is True
    engine.dispose()

    with SessionLocal() as db:
        assert db.get(AppSetting, "apply_interrupted").value == "backup database"
    assert attachment.read_text(encoding="utf-8") == "backup attachment"
    assert not (settings.data_root / "pending_restore.json").exists()
    assert not list((settings.data_root / "tmp").glob("restore_apply_*"))


def test_committed_cleanup_failure_preserves_and_applies_later_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    init_db()
    settings = get_settings()
    with SessionLocal() as db:
        existing = db.get(AppSetting, "committed_cleanup")
        if existing is None:
            db.add(AppSetting(key="committed_cleanup", value="backup database"))
        else:
            existing.value = "backup database"
        db.commit()
    backup_path = create_backup()
    with SessionLocal() as db:
        db.get(AppSetting, "committed_cleanup").value = "live database"
        db.commit()
    stage_restore(backup_path)

    real_remove = backup_service._remove_restore_transaction_root
    removal_attempts = 0

    def fail_first_committed_cleanup(transaction_root: Path) -> None:
        nonlocal removal_attempts
        removal_attempts += 1
        if removal_attempts == 1:
            raise OSError("simulated committed cleanup failure")
        real_remove(transaction_root)

    monkeypatch.setattr(backup_service, "_remove_restore_transaction_root", fail_first_committed_cleanup)
    engine.dispose()
    with pytest.warns(RuntimeWarning, match="已完整恢复"):
        assert apply_pending_restore() is True
    assert not (settings.data_root / "pending_restore.json").exists()
    assert list((settings.data_root / "tmp").glob("restore_apply_*"))

    with SessionLocal() as db:
        db.get(AppSetting, "committed_cleanup").value = "later backup database"
        db.commit()
    later_backup_path = create_backup()
    later_stage = stage_restore(later_backup_path)
    assert later_stage.marker.exists()

    engine.dispose()
    assert apply_pending_restore() is True
    engine.dispose()
    with SessionLocal() as db:
        assert db.get(AppSetting, "committed_cleanup").value == "later backup database"
    assert not (settings.data_root / "pending_restore.json").exists()
    assert not list((settings.data_root / "tmp").glob("restore_apply_*"))
