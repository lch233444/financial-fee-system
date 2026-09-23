from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def is_sqlite_busy(exc: OperationalError) -> bool:
    message = str(exc.orig).casefold()
    return any(marker in message for marker in (
        "database is locked", "database table is locked", "database is busy",
    ))


def begin_immediate(db: Session) -> None:
    """Acquire the write lock before reads; keep other database errors visible."""
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as exc:
        db.rollback()
        if is_sqlite_busy(exc):
            raise HTTPException(status_code=409, detail="数据库正在处理另一笔财务写入，请稍后重试") from exc
        raise
