from __future__ import annotations

from collections.abc import Generator
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import application_root, get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401

    root = application_root()
    if (root / "alembic.ini").exists():
        ini_path = root / "alembic.ini"
        script_path = root / "alembic"
    else:
        ini_path = root / "backend" / "alembic.ini"
        script_path = root / "backend" / "alembic"

    if not ini_path.exists() or not script_path.exists():
        # Development fallback for a source tree copied without migrations.
        Base.metadata.create_all(bind=engine)
        return

    alembic_config = Config(str(ini_path))
    alembic_config.set_main_option("script_location", str(script_path).replace("%", "%%"))
    alembic_config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    existing_tables = set(inspect(engine).get_table_names())
    if existing_tables and "alembic_version" not in existing_tables:
        # Upgrade a pre-migration MVP database without destroying its records.
        Base.metadata.create_all(bind=engine)
        refreshed_inspector = inspect(engine)
        statement_columns = {
            column["name"] for column in refreshed_inspector.get_columns("statement_imports")
        }
        ai_columns = {
            "ai_recognition_json",
            "ai_status",
            "ai_model",
            "ai_recognized_at",
        }
        if not ai_columns.issubset(statement_columns):
            # The original MVP schema predates the AI migration. Stamping it
            # directly at head would falsely mark missing columns as applied.
            command.stamp(alembic_config, "b3c4b22cde0d")
            command.upgrade(alembic_config, "head")
        else:
            command.stamp(alembic_config, "head")
    else:
        command.upgrade(alembic_config, "head")
