from __future__ import annotations

from collections.abc import Generator
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect, text
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
    cursor.execute("PRAGMA busy_timeout=5000")
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
        # Only this audited legacy-bootstrap path may reach 0.2.14 with
        # current metadata having pre-created the otherwise missing parent
        # tables.  A normally stamped 9d database with that target shape is
        # treated as an interrupted/manual migration and must stop.
        alembic_config.attributes["allow_precreated_0214_parent_shape"] = True
        # Upgrade a pre-migration MVP database without destroying its records.
        # Do not let current metadata pre-create the 0.2.14 ledger tables in an
        # older unversioned database.  Their presence is a deliberate migration
        # shape marker and the real revision must create/backfill them atomically.
        settlement_ledger_table_names = {
            "invoice_corrections",
            "payment_allocations",
            "payment_refunds",
            "invoice_adjustments",
        }
        Base.metadata.create_all(
            bind=engine,
            tables=[
                table
                for table_name, table in Base.metadata.tables.items()
                if table_name not in settlement_ledger_table_names
            ],
        )
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
        settlement_version_columns = {"version_no", "replaces_settlement_id"}
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
        payments_columns = {
            column["name"]: column
            for column in refreshed_inspector.get_columns("payments")
        }
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
        correction_ledger_columns = {
            "invoice_corrections": {
                "original_invoice_id",
                "replacement_invoice_id",
                "status",
                "reason",
                "opened_at",
                "completed_at",
            },
            "payment_allocations": {
                "payment_id",
                "invoice_id",
                "amount_cents",
                "entry_type",
                "reverses_allocation_id",
                "correction_id",
            },
            "payment_refunds": {
                "payment_id",
                "correction_id",
                "refund_date",
                "amount_cents",
                "method",
                "reason",
                "proof_attachment_id",
            },
            "invoice_adjustments": {
                "invoice_id",
                "correction_id",
                "payment_id",
                "adjustment_type",
                "amount_cents",
                "reason",
            },
        }
        present_correction_ledger_tables = refreshed_tables & set(correction_ledger_columns)
        correction_ledger_tables_complete = all(
            table_name in refreshed_tables
            and required_columns.issubset(
                {
                    column["name"]
                    for column in refreshed_inspector.get_columns(table_name)
                }
            )
            for table_name, required_columns in correction_ledger_columns.items()
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
            invoice_adjustment_payment_unique = any(
                bool(index_row[2])
                and tuple(
                    str(column_row[2])
                    for column_row in connection.exec_driver_sql(
                        f'PRAGMA index_info("{str(index_row[1])}")'
                    ).fetchall()
                )
                == ("payment_id",)
                for index_row in connection.exec_driver_sql(
                    'PRAGMA index_list("invoice_adjustments")'
                ).fetchall()
            )
        settlement_triggers = set(trigger_sql)
        required_settlement_triggers = {
            "trg_transactions_block_finalized_period",
            "trg_settlement_block_out_of_order_insert",
            "trg_settlement_validate_finalize",
            "trg_settlement_validate_void",
            "trg_settlement_account_line_order",
        }
        required_settlement_integrity_triggers = {
            "trg_transactions_update_block_frozen_period",
            "trg_transactions_delete_block_frozen_period",
            "trg_snapshot_update_block_frozen_reference",
            "trg_snapshot_delete_block_frozen_reference",
            "trg_attachment_update_block_finalized_evidence",
            "trg_attachment_delete_block_finalized_evidence",
            "trg_statement_import_update_block_finalized_evidence",
            "trg_statement_import_delete_block_finalized_evidence",
            "trg_settlement_insert_draft_only",
            "trg_settlement_lifecycle_transition",
            "trg_settlement_delete_non_draft",
            "trg_settlement_parent_financial_lock",
            "trg_settlement_line_insert_draft_only",
            "trg_settlement_line_update_draft_only",
            "trg_settlement_line_delete_draft_only",
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
            and (
                "payment_amount_exceeds_invoice"
                in trigger_sql.get("trg_payment_validate_insert", "").lower()
                or "payment_proof_invalid"
                in trigger_sql.get("trg_payment_validate_insert", "").lower()
            )
            and (
                "invoice_has_payments"
                in trigger_sql.get("trg_invoice_block_void_with_payment", "").lower()
                or "invoice_payment_allocation_not_reversed"
                in trigger_sql.get("trg_invoice_block_void_with_payment", "").lower()
            )
            and "invoice_lifecycle_transition_invalid"
            in trigger_sql.get("trg_invoice_lifecycle_transition", "").lower()
            and "invoice_issue_metadata_invalid"
            in trigger_sql.get("trg_invoice_issue_metadata_guard", "").lower()
        )
        invoice_indexes_complete = {
            "uq_invoice_sources_active_settlement",
            "uq_invoices_active_client_period_plan",
        }.issubset(index_names)
        settlement_integrity_sql_is_current = (
            "legacy_draft_requires_recalculation"
            in trigger_sql.get("trg_settlement_validate_finalize", "").lower()
            and "settlement_formula_version_invalid"
            in trigger_sql.get("trg_settlement_validate_finalize", "").lower()
            and "abs(new.gain_loss_cents) * 1000000"
            in " ".join(
                trigger_sql.get("trg_settlement_validate_finalize", "")
                .lower()
                .split()
            )
            and "settlement_account_period_duplicate"
            in trigger_sql.get("trg_settlement_validate_finalize", "").lower()
            and "settlement_account_period_duplicate"
            in trigger_sql.get("trg_settlement_account_line_order", "").lower()
            and "settlement_transaction_totals_changed"
            in trigger_sql.get("trg_settlement_validate_finalize", "").lower()
            and "transaction_record.transaction_date > line.start_date"
            in trigger_sql.get("trg_settlement_validate_finalize", "").lower()
            and "new.status = 'finalized' and new.void_reason is not null"
            in " ".join(
                trigger_sql.get("trg_settlement_lifecycle_transition", "")
                .lower()
                .split()
            )
            and "old.status = 'draft' and new.status = 'finalized'"
            in " ".join(
                trigger_sql.get("trg_settlement_lifecycle_transition", "")
                .lower()
                .split()
            )
            and "new.void_reason is not old.void_reason"
            in " ".join(
                trigger_sql.get("trg_settlement_parent_financial_lock", "")
                .lower()
                .split()
            )
            and "new.transaction_date >= line.start_date"
            in trigger_sql.get("trg_transactions_block_finalized_period", "").lower()
            and "settlement.status = 'finalized'"
            in trigger_sql.get("trg_transactions_block_finalized_period", "").lower()
        )
        required_correction_triggers = {
            "trg_payment_claim_proof",
            "trg_payment_update_immutable",
            "trg_payment_delete_immutable",
            "trg_invoice_correction_validate_insert",
            "trg_invoice_correction_validate_update",
            "trg_invoice_correction_delete_immutable",
            "trg_payment_allocation_validate_insert",
            "trg_payment_allocation_update_immutable",
            "trg_payment_allocation_delete_immutable",
            "trg_payment_refund_validate_insert",
            "trg_payment_refund_claim_proof",
            "trg_payment_refund_update_immutable",
            "trg_payment_refund_delete_immutable",
            "trg_invoice_adjustment_validate_insert",
            "trg_invoice_adjustment_update_immutable",
            "trg_invoice_adjustment_delete_immutable",
            "trg_attachment_update_block_payment_evidence",
            "trg_attachment_delete_block_payment_evidence",
        }
        required_delete_guard_triggers = {
            "trg_client_delete_no_cascade",
            "trg_account_delete_no_cascade",
            "trg_statement_import_delete_no_snapshot",
            "trg_snapshot_delete_no_confirmed_import",
        }
        expected_0_2_14_head_triggers = (
            required_settlement_triggers
            | required_settlement_integrity_triggers
            | {"trg_settlement_account_line_update_order"}
            | required_invoice_triggers
            | required_correction_triggers
        )
        expected_current_head_triggers = (
            expected_0_2_14_head_triggers | required_delete_guard_triggers
        )
        from .services.delete_guard_contract import (
            delete_guard_trigger_sql_is_current as _delete_guard_sql_is_current,
        )

        delete_guard_trigger_sql_is_current = _delete_guard_sql_is_current(trigger_sql)
        trigger_shape_is_0_2_14 = settlement_triggers == expected_0_2_14_head_triggers
        trigger_shape_is_current = (
            settlement_triggers == expected_current_head_triggers
            and delete_guard_trigger_sql_is_current
        )
        settlement_version_shape_complete = (
            settlement_version_columns.issubset(settlement_columns)
            and correction_ledger_tables_complete
            and payments_columns.get("proof_attachment_id", {}).get("nullable") is False
            and payments_columns.get("company_difference_cents", {}).get("nullable") is False
            and "difference_reason" in payments_columns
            and invoice_adjustment_payment_unique
            and "uq_settlement_group_period_active" in index_names
            and required_correction_triggers.issubset(settlement_triggers)
            and (trigger_shape_is_0_2_14 or trigger_shape_is_current)
            and "settlement_replacement_invalid"
            in trigger_sql.get("trg_settlement_insert_draft_only", "").lower()
            and "settlement_replacement_identity_immutable"
            in trigger_sql.get("trg_settlement_parent_financial_lock", "").lower()
            and "settlement_replacement_invalid_at_finalize"
            in trigger_sql.get("trg_settlement_parent_financial_lock", "").lower()
            and "settlement_container_previous_changed"
            in trigger_sql.get("trg_settlement_validate_finalize", "").lower()
            and "invoice_void_metadata_invalid"
            in trigger_sql.get("trg_invoice_lifecycle_transition", "").lower()
            and "invoice_void_metadata_immutable"
            in trigger_sql.get("trg_invoice_lifecycle_transition", "").lower()
            and "invoice_payment_allocation_not_reversed"
            in trigger_sql.get("trg_invoice_block_void_with_payment", "").lower()
            and "invoice_open_correction_replacement_cannot_void"
            in trigger_sql.get("trg_invoice_block_void_with_payment", "").lower()
            and "payment_invoice_group_has_open_correction"
            in trigger_sql.get("trg_payment_validate_insert", "").lower()
            and "payment_invoice_ledger_not_empty"
            in trigger_sql.get("trg_payment_validate_insert", "").lower()
            and "payment_must_settle_invoice"
            in trigger_sql.get("trg_payment_validate_insert", "").lower()
            and "payment_method_invalid"
            in trigger_sql.get("trg_payment_validate_insert", "").lower()
            and "payment_refund_method_invalid"
            in trigger_sql.get("trg_payment_refund_validate_insert", "").lower()
            and "payment.company_difference_cents"
            in trigger_sql.get("trg_invoice_adjustment_validate_insert", "").lower()
            and "invoice_correction_group_already_open"
            in trigger_sql.get("trg_invoice_correction_validate_insert", "").lower()
            and "invoice_correction_source_lineage_invalid"
            in trigger_sql.get("trg_invoice_correction_validate_update", "").lower()
            and "invoice_correction_blank_replacement_ledger_required"
            in trigger_sql.get("trg_invoice_correction_validate_update", "").lower()
        )
        settlement_version_shape_absent = (
            settlement_columns.isdisjoint(settlement_version_columns)
            and not present_correction_ledger_tables
            and "uq_settlement_group_period_active" not in index_names
            and not (required_correction_triggers & settlement_triggers)
        )
        if present_correction_ledger_tables and not settlement_version_shape_complete:
            # These tables are created only by the 0.2.14 revision.  Seeing
            # any incomplete subset in an unstamped database is evidence of a
            # manually-created or interrupted schema, not an older MVP shape.
            # Stop before stamping an earlier revision over that evidence.
            raise RuntimeError(
                "数据库存在不完整的Settlement版本或付款更正账本结构；"
                "已停止启动，请使用已验证备份并人工检查"
            )
        if settlement_version_shape_complete:
            if trigger_shape_is_current:
                from .services.entity_ids import (
                    EntityIdAllocationError,
                    seed_missing_entity_id_high_water_settings,
                )

                with Session(bind=engine) as bootstrap_session:
                    try:
                        bootstrap_session.execute(text("BEGIN IMMEDIATE"))
                        seed_missing_entity_id_high_water_settings(bootstrap_session)
                        bootstrap_session.commit()
                    except EntityIdAllocationError as exc:
                        bootstrap_session.rollback()
                        raise RuntimeError(
                            "未版本化数据库的ID高水位证据异常；已停止启动，请人工检查"
                        ) from exc
                command.stamp(alembic_config, "head")
            else:
                # A complete unversioned 0.2.14 schema has exactly the 53
                # payment-ledger triggers.  Stamp its proven revision first so
                # the 0.2.15 delete guards are created by the real migration.
                command.stamp(alembic_config, "7f3c2a91b6e4")
                command.upgrade(alembic_config, "head")
        elif not ai_columns.issubset(statement_columns):
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
        elif (
            not required_settlement_integrity_triggers.issubset(settlement_triggers)
            or not settlement_integrity_sql_is_current
        ):
            command.stamp(alembic_config, "c4b7f1d92e60")
            command.upgrade(alembic_config, "head")
        elif settlement_version_shape_absent:
            command.stamp(alembic_config, "9d2f6a8c4b13")
            command.upgrade(alembic_config, "head")
        else:
            raise RuntimeError(
                "数据库存在不完整的Settlement版本或付款更正账本结构；"
                "已停止启动，请使用已验证备份并人工检查"
            )
    else:
        command.upgrade(alembic_config, "head")

    # The current head physically deletes a small set of auditable entities.
    # Their IDs must therefore remain above both live rows and historical
    # deletion audits, otherwise a later insert could make an old audit appear
    # to belong to a new record.
    from .services.entity_ids import (
        EntityIdAllocationError,
        validate_entity_id_high_water_settings,
    )

    with Session(bind=engine) as validation_session:
        try:
            validate_entity_id_high_water_settings(validation_session)
        except EntityIdAllocationError as exc:
            raise RuntimeError(
                "数据库ID高水位完整性校验失败；系统已停止启动，请使用已验证备份并人工检查"
            ) from exc

    from .services.delete_guard_contract import (
        delete_guard_trigger_sql_is_current as _delete_guard_sql_is_current,
    )

    with engine.connect() as validation_connection:
        current_delete_guard_sql = {
            str(row[0]): str(row[1] or "")
            for row in validation_connection.exec_driver_sql(
                """
                SELECT name, sql FROM sqlite_master
                WHERE type = 'trigger' AND name IN (
                    'trg_client_delete_no_cascade',
                    'trg_account_delete_no_cascade',
                    'trg_statement_import_delete_no_snapshot',
                    'trg_snapshot_delete_no_confirmed_import'
                )
                """
            ).fetchall()
        }
    if not _delete_guard_sql_is_current(current_delete_guard_sql):
        raise RuntimeError(
            "数据库受控删除Trigger语义不完整；系统已停止启动，请使用已验证备份并人工检查"
        )
