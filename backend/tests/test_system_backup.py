from __future__ import annotations

import json
import zipfile

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, engine, init_db
from app.models import AppSetting
from app.services.backup import apply_pending_restore, create_backup, stage_restore
from app.services.storage import sha256_file


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
