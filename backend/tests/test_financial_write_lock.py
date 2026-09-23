import sqlite3
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.routes import invoices, master, settlements, statements


@pytest.mark.parametrize("route", [master, statements, settlements, invoices])
def test_write_lock_is_first_sql_and_busy_rolls_back(route, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lock.db'}", connect_args={"timeout": 0.01})
    queries = []
    def record(_conn, _cursor, statement, *_args):
        queries.append(statement.upper())
    event.listen(engine, "before_cursor_execute", record)
    try:
        with Session(engine) as owner, Session(engine) as contender:
            route._begin_immediate(owner)
            assert queries == ["BEGIN IMMEDIATE"]
            with pytest.raises(HTTPException) as caught:
                route._begin_immediate(contender)
            assert caught.value.status_code == 409
            assert caught.value.detail == "数据库正在处理另一笔财务写入，请稍后重试"
            assert not contender.in_transaction()
            owner.rollback()
            route._begin_immediate(contender)
            contender.execute(text("CREATE TABLE synthetic (id INTEGER)"))
            contender.commit()
    finally:
        engine.dispose()


@pytest.mark.parametrize("route", [master, statements, settlements, invoices])
@pytest.mark.parametrize("message, busy", [
    ("database is locked", True), ("database table is locked", True),
    ("database is busy", True), ("disk I/O error", False),
])
def test_write_lock_keeps_original_error_and_rollback_semantics(route, message, busy):
    original = OperationalError("BEGIN IMMEDIATE", {}, sqlite3.OperationalError(message))
    db = Mock(spec=Session)
    db.execute.side_effect = original
    with pytest.raises(HTTPException if busy else OperationalError) as caught:
        route._begin_immediate(db)
    db.rollback.assert_called_once_with()
    if busy:
        assert caught.value.status_code == 409
        assert caught.value.__cause__ is original
    else:
        assert caught.value is original
