"""Package-owned settlement periods; fee arithmetic remains shared.

The legacy integer ``quarter`` column keeps 1..4 unchanged. In the new schema,
0 is an explicitly guarded annual-period discriminator, never a fifth quarter.
Every financial header also freezes its period_type; the API accepts an annual
request only in an ANNUAL package. No legacy row is reinterpreted as annual.
"""
from datetime import date
from contextlib import closing
from pathlib import Path
import sqlite3

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AppSetting
from .calculation import quarter_dates

CYCLE_KEY = "settlement_cycle"
CYCLES = {"QUARTERLY", "ANNUAL"}


def read_cycle(path: Path, *, archived: bool = False) -> str:
    # Immutable mode is only for closed archive copies: it must not create WAL
    # sidecars in a manifest-validated tree. Live roots must see committed WAL.
    query = "?mode=ro&immutable=1" if archived else "?mode=ro"
    with closing(sqlite3.connect(path.resolve().as_uri() + query, uri=True)) as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key=?", (CYCLE_KEY,)).fetchone()
        if row is None:
            revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if revision and revision[0] in ("c921f0181100", "d016f0ee1160"):
                return "QUARTERLY"
        elif row[0] in CYCLES:
            return row[0]
    raise ValueError("数据包结算周期缺失或无效")


def cycle_for(db: Session) -> str:
    setting = db.get(AppSetting, CYCLE_KEY)
    if setting is None or setting.value not in CYCLES:
        raise RuntimeError("数据包结算周期缺失或无效，请停止操作并核对数据包")
    return setting.value


def require_period(db: Session, quarter: int | None, *, optional: bool = False) -> str:
    cycle = cycle_for(db)
    if optional and quarter is None:
        return cycle
    if (cycle == "ANNUAL" and quarter != 0) or (cycle == "QUARTERLY" and quarter not in (1, 2, 3, 4)):
        raise HTTPException(409, "所选结算期间与当前数据包周期不符，请刷新页面后操作")
    return cycle


def period_dates(year: int, quarter: int) -> tuple[date, date]:
    return (date(year, 1, 1), date(year, 12, 31)) if quarter == 0 else quarter_dates(year, quarter)


def period_label(year: int, quarter: int, *, english: bool = False) -> str:
    if quarter == 0:
        return f"{year} Annual" if english else f"{year}年度"
    return f"{year} Q{quarter}" if english else f"{year}年第{quarter}季度"
