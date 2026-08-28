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

    if not ini_path.is_file() or not script_path.is_dir():
        raise RuntimeError("数据库迁移文件缺失，系统已停止启动")

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
        settlement_columns = {
            column["name"] for column in refreshed_inspector.get_columns("quarterly_settlements")
        }
        settlement_chain_columns = {"company_id", "fc_id", "previous_settlement_id"}
        settlement_account_mode_columns = {"calculation_mode"}
        account_line_columns = {
            column["name"] for column in refreshed_inspector.get_columns("settlement_account_lines")
        }
        account_hwm_columns = {
            "previous_line_id",
            "beginning_snapshot_id",
            "original_hwm_cents",
            "next_hwm_cents",
            "service_fee_cents",
            "formula_version",
        }
        account_period_columns = {"start_date", "closing_date", "days"}
        refreshed_tables = set(refreshed_inspector.get_table_names())
        invoice_columns = {
            column["name"] for column in refreshed_inspector.get_columns("invoices")
        }
        invoice_parent_columns = {"client_id", "year", "quarter", "fee_plan_id"}
        invoice_ledger_columns = {
            "invoice_sources": {
                "invoice_id",
                "settlement_id",
                "locked_amount_cents",
                "active",
                "created_at",
                "updated_at",
            },
            "invoice_lines": {
                "invoice_id",
                "source_id",
                "source_settlement_id",
                "source_account_line_id",
                "platform_id",
                "platform_name_snapshot",
                "account_number_snapshot",
                "start_date",
                "closing_date",
                "service_fee_cents",
                "display_order",
            },
            "invoice_issue_attempts": {
                "invoice_id",
                "invoice_number",
                "status",
                "started_at",
                "completed_at",
                "details",
            },
        }
        invoice_ledger_tables_complete = all(
            table_name in refreshed_tables
            and required_columns.issubset(
                {
                    column["name"]
                    for column in refreshed_inspector.get_columns(table_name)
                }
            )
            for table_name, required_columns in invoice_ledger_columns.items()
        )
        with engine.connect() as connection:
            trigger_sql = {
                row[0]: row[1] or ""
                for row in connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
                )
            }
            index_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
        settlement_triggers = set(trigger_sql)
        required_settlement_triggers = {
            "trg_transactions_block_finalized_period",
            "trg_settlement_block_out_of_order_insert",
            "trg_settlement_validate_finalize",
            "trg_settlement_validate_void",
            "trg_settlement_account_line_order",
        }
        required_invoice_triggers = {
            "trg_invoice_validate_issue",
            "trg_invoice_source_insert_draft_only",
            "trg_invoice_source_update_draft_only",
            "trg_invoice_source_delete_draft_only",
            "trg_invoice_line_insert_draft_only",
            "trg_invoice_line_update_draft_only",
            "trg_invoice_line_delete_draft_only",
            "trg_invoice_sources_deactivate_on_void",
            "trg_invoice_financial_header_update_lock",
            "trg_invoice_insert_draft_only",
            "trg_invoice_lifecycle_transition",
            "trg_invoice_issue_metadata_guard",
            "trg_payment_validate_insert",
            "trg_invoice_block_void_with_payment",
        }
        invoice_trigger_sql_is_current = (
            "invoice_sources" in trigger_sql.get("trg_settlement_validate_void", "").lower()
            and "source.active = 1"
            in trigger_sql.get("trg_settlement_validate_void", "").lower()
            and "invoice_lines" in trigger_sql.get("trg_invoice_validate_issue", "").lower()
            and "locked_amount_cents"
            in trigger_sql.get("trg_invoice_validate_issue", "").lower()
            and "invoice_source_set_incomplete"
            in trigger_sql.get("trg_invoice_validate_issue", "").lower()
            and "company_id is not new.company_id"
            in trigger_sql.get("trg_invoice_validate_issue", "").lower()
            and "new.invoice_id = old.invoice_id"
            in trigger_sql.get("trg_invoice_source_update_draft_only", "").lower()
            and "source.invoice_id = new.invoice_id"
            in trigger_sql.get("trg_invoice_line_update_draft_only", "").lower()
            and "payment_amount_exceeds_invoice"
            in trigger_sql.get("trg_payment_validate_insert", "").lower()
            and "invoice_has_payments"
            in trigger_sql.get("trg_invoice_block_void_with_payment", "").lower()
            and "invoice_lifecycle_transition_invalid"
            in trigger_sql.get("trg_invoice_lifecycle_transition", "").lower()
            and "invoice_issue_metadata_invalid"
            in trigger_sql.get("trg_invoice_issue_metadata_guard", "").lower()
        )
        invoice_indexes_complete = {
            "uq_invoice_sources_active_settlement",
            "uq_invoices_active_client_period_plan",
        }.issubset(index_names)
        if not ai_columns.issubset(statement_columns):
            # The original MVP schema predates the AI migration. Stamping it
            # directly at head would falsely mark missing columns as applied.
            command.stamp(alembic_config, "b3c4b22cde0d")
            command.upgrade(alembic_config, "head")
        elif (
            not settlement_chain_columns.issubset(settlement_columns)
        ):
            command.stamp(alembic_config, "d8f42c0b7a11")
            command.upgrade(alembic_config, "head")
        elif (
            not settlement_account_mode_columns.issubset(settlement_columns)
            or not account_hwm_columns.issubset(account_line_columns)
            or not required_settlement_triggers.issubset(settlement_triggers)
        ):
            command.stamp(alembic_config, "e91f7c6a2b40")
            command.upgrade(alembic_config, "head")
        elif not account_period_columns.issubset(account_line_columns):
            command.stamp(alembic_config, "f2a8c7d41e90")
            command.upgrade(alembic_config, "head")
        elif (
            not invoice_parent_columns.issubset(invoice_columns)
            or not invoice_ledger_tables_complete
            or not required_invoice_triggers.issubset(settlement_triggers)
            or not invoice_trigger_sql_is_current
            or not invoice_indexes_complete
        ):
            # A pre-versioned database can contain none, part, or all of the
            # invoice aggregation tables because create_all only fills absent
            # tables.  Always run the real revision unless every column,
            # critical index, and trigger body proves the 0.2.9 shape.
            command.stamp(alembic_config, "a6d1f4c28b73")
            command.upgrade(alembic_config, "head")
        else:
            command.stamp(alembic_config, "head")
    else:
        command.upgrade(alembic_config, "head")
