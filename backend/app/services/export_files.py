from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import ExportRecord


@contextmanager
def generated_export_file(db: Session, output_path: Path) -> Iterator[None]:
    """Own one new path through generation and its ExportRecord commit."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # A collision must never overwrite an older archive or give this request
    # permission to remove it. Generators may leave even an incomplete file.
    with output_path.open("xb"):
        pass
    try:
        yield
    except Exception:
        try:
            db.rollback()
            # commit() can raise after durably storing the record. Check in a
            # fresh transaction, and hold the write lock across check/unlink.
            # Rendering itself remains outside this short recovery write lock.
            with SessionLocal() as check_db:
                check_db.execute(text("BEGIN IMMEDIATE"))
                reference_id = check_db.scalar(
                    select(ExportRecord.id)
                    .where(ExportRecord.stored_path == str(output_path))
                    .limit(1)
                )
                if reference_id is None:
                    output_path.unlink(missing_ok=True)
        except Exception as cleanup_exc:
            raise HTTPException(
                status_code=500,
                detail="导出失败后的文件清理未完成，请保留现场并人工核对导出目录与数据库登记",
            ) from cleanup_exc
        raise
