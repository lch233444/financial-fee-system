"""Add settlement correction versions and the append-only payment ledger.

Revision ID: 7f3c2a91b6e4
Revises: 9d2f6a8c4b13
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "7f3c2a91b6e4"
down_revision = "9d2f6a8c4b13"
branch_labels = None
depends_on = None


SETTLEMENT_TRIGGER_NAMES = (
    "trg_transactions_block_finalized_period",
    "trg_transactions_update_block_frozen_period",
    "trg_transactions_delete_block_frozen_period",
    "trg_snapshot_update_block_frozen_reference",
    "trg_snapshot_delete_block_frozen_reference",
    "trg_attachment_update_block_finalized_evidence",
    "trg_attachment_delete_block_finalized_evidence",
    "trg_statement_import_update_block_finalized_evidence",
    "trg_statement_import_delete_block_finalized_evidence",
    "trg_settlement_block_out_of_order_insert",
    "trg_settlement_account_line_order",
    "trg_settlement_account_line_update_order",
    "trg_settlement_validate_finalize",
    "trg_settlement_validate_void",
    "trg_settlement_insert_draft_only",
    "trg_settlement_lifecycle_transition",
    "trg_settlement_delete_non_draft",
    "trg_settlement_parent_financial_lock",
    "trg_settlement_line_insert_draft_only",
    "trg_settlement_line_update_draft_only",
    "trg_settlement_line_delete_draft_only",
)

INVOICE_TRIGGER_NAMES = (
    "trg_invoice_source_insert_draft_only",
    "trg_invoice_source_update_draft_only",
    "trg_invoice_source_delete_draft_only",
    "trg_invoice_line_insert_draft_only",
    "trg_invoice_line_update_draft_only",
    "trg_invoice_line_delete_draft_only",
    "trg_invoice_validate_issue",
    "trg_invoice_financial_header_update_lock",
    "trg_invoice_insert_draft_only",
    "trg_invoice_lifecycle_transition",
    "trg_invoice_issue_metadata_guard",
    "trg_payment_validate_insert",
    "trg_invoice_block_void_with_payment",
    "trg_invoice_sources_deactivate_on_void",
)

REPLACED_SETTLEMENT_TRIGGERS = {
    "trg_settlement_insert_draft_only",
    "trg_settlement_parent_financial_lock",
}

REPLACED_INVOICE_TRIGGERS = {
    "trg_invoice_lifecycle_transition",
    "trg_payment_validate_insert",
    "trg_invoice_block_void_with_payment",
}

NEW_TABLE_NAMES = {
    "invoice_corrections",
    "payment_allocations",
    "payment_refunds",
    "invoice_adjustments",
}

NEW_TRIGGER_NAMES = {
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

OLD_SETTLEMENT_COLUMNS = {
    "id",
    "client_id",
    "platform_id",
    "fee_plan_id",
    "company_id",
    "fc_id",
    "previous_settlement_id",
    "year",
    "quarter",
    "start_date",
    "closing_date",
    "days",
    "beginning_cents",
    "contribution_cents",
    "withdrawal_cents",
    "net_contribution_cents",
    "closing_cents",
    "gain_loss_cents",
    "period_rate_ppm",
    "original_hwm_cents",
    "adjusted_hwm_cents",
    "watermark_difference_cents",
    "chargeable_above_hwm_cents",
    "service_fee_cents",
    "next_hwm_cents",
    "fee_rate_bps",
    "formula_version",
    "calculation_mode",
    "status",
    "finalized_at",
    "void_reason",
    "created_at",
    "updated_at",
}

LEGACY_PAYMENT_COLUMN_ORDER = (
    "id",
    "invoice_id",
    "payment_date",
    "amount_cents",
    "method",
    "proof_attachment_id",
    "remark",
    "created_at",
    "updated_at",
)
OLD_PAYMENT_COLUMNS = set(LEGACY_PAYMENT_COLUMN_ORDER)
TARGET_PAYMENT_COLUMNS = OLD_PAYMENT_COLUMNS | {
    "company_difference_cents",
    "difference_reason",
}


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _foreign_keys(table_name: str) -> set[tuple[tuple[str, ...], str, tuple[str, ...], str]]:
    result: set[tuple[tuple[str, ...], str, tuple[str, ...], str]] = set()
    for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name):
        options = foreign_key.get("options") or {}
        result.add(
            (
                tuple(str(name) for name in foreign_key["constrained_columns"]),
                str(foreign_key["referred_table"]),
                tuple(str(name) for name in foreign_key["referred_columns"]),
                str(options.get("ondelete") or "").upper(),
            )
        )
    return result


def _trigger_sql() -> dict[str, str]:
    rows = op.get_bind().exec_driver_sql(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
    ).fetchall()
    return {str(name): str(sql or "") for name, sql in rows}


def _index_columns(table_name: str) -> dict[str, tuple[bool, tuple[str, ...], str]]:
    connection = op.get_bind()
    result: dict[str, tuple[bool, tuple[str, ...], str]] = {}
    for row in connection.exec_driver_sql(f'PRAGMA index_list("{table_name}")').fetchall():
        name = str(row[1])
        columns = tuple(
            str(info[2])
            for info in connection.exec_driver_sql(f'PRAGMA index_info("{name}")').fetchall()
        )
        sql_row = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?", (name,)
        ).fetchone()
        result[name] = (bool(row[2]), columns, str(sql_row[0] or "") if sql_row else "")
    return result


def _require_zero_rows(sql: str, message: str, parameters: tuple[object, ...] = ()) -> None:
    if op.get_bind().exec_driver_sql(sql, parameters).fetchone() is not None:
        raise RuntimeError(message)


def _validate_legacy_payment_proof_files() -> None:
    """Hash legacy Payment proofs without exposing or parsing their contents."""

    from app.config import get_settings
    from app.services.storage import is_within, sha256_file

    controlled_root = get_settings().data_root / "attachments"
    rows = op.get_bind().exec_driver_sql(
        """
        SELECT proof.stored_path, proof.size_bytes, proof.sha256
        FROM payments AS payment
        JOIN attachments AS proof ON proof.id = payment.proof_attachment_id
        ORDER BY payment.id
        """
    ).fetchall()
    for stored_path, expected_size, expected_sha256 in rows:
        path = Path(str(stored_path))
        if not is_within(path, controlled_root):
            raise RuntimeError(
                "存在旧Payment凭证路径不在系统受控attachments目录；"
                "已在任何DDL前停止迁移"
            )
        try:
            if not path.is_file():
                raise RuntimeError(
                    "存在旧Payment凭证原文件缺失或不是普通文件；"
                    "已在任何DDL前停止迁移"
                )
            actual_size = path.stat().st_size
            if actual_size <= 0 or actual_size != int(expected_size):
                raise RuntimeError(
                    "存在旧Payment凭证文件大小与记录不一致；"
                    "已在任何DDL前停止迁移"
                )
            actual_sha256 = sha256_file(path)
        except OSError as exc:
            raise RuntimeError(
                "存在旧Payment凭证原文件无法读取；"
                "已在任何DDL前停止迁移"
            ) from exc
        if actual_sha256.casefold() != str(expected_sha256).casefold():
            raise RuntimeError(
                "存在旧Payment凭证文件SHA-256与记录不一致；"
                "已在任何DDL前停止迁移"
            )


def _assert_foreign_keys_disabled() -> None:
    connection = op.get_bind()
    foreign_keys_enabled = int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar() or 0)
    if foreign_keys_enabled != 0:
        raise RuntimeError(
            "Settlement核心表重建要求Alembic连接在事务开始前设置PRAGMA foreign_keys=OFF；"
            "当前连接已开启外键，已在任何DDL前停止"
        )


def _validate_pre_upgrade() -> tuple[dict[str, str], int, int, bool, bool]:
    connection = op.get_bind()
    _assert_foreign_keys_disabled()

    revision_rows = [
        tuple(row)
        for row in connection.exec_driver_sql("SELECT version_num FROM alembic_version").fetchall()
    ]
    if revision_rows != [(down_revision,)]:
        raise RuntimeError("数据库不是完整的9d2f6a8c4b13旧head，已停止0.2.14迁移")

    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    missing_tables = {"quarterly_settlements", "payments", "attachments", "invoices"} - tables
    if missing_tables:
        raise RuntimeError(f"数据库缺少0.2.14迁移前置表：{', '.join(sorted(missing_tables))}")
    if tables & NEW_TABLE_NAMES:
        raise RuntimeError("数据库存在不完整的0.2.14追加账本表，已停止迁移并要求从已验证备份恢复")

    settlement_columns = _column_names("quarterly_settlements")
    target_settlement_columns = OLD_SETTLEMENT_COLUMNS | {"version_no", "replaces_settlement_id"}
    if settlement_columns not in (OLD_SETTLEMENT_COLUMNS, target_settlement_columns):
        raise RuntimeError("quarterly_settlements不是完整9d结构或已处于半迁移状态")
    rebuild_settlements = settlement_columns == OLD_SETTLEMENT_COLUMNS
    trusted_legacy_bootstrap = bool(
        op.get_context().config.attributes.get("allow_precreated_0214_parent_shape", False)
    )
    if not rebuild_settlements and not trusted_legacy_bootstrap:
        raise RuntimeError(
            "quarterly_settlements已呈现0.2.14目标形状但revision仍为9d；"
            "已按半迁移状态安全停止"
        )
    payment_columns = _column_names("payments")
    if payment_columns not in (OLD_PAYMENT_COLUMNS, TARGET_PAYMENT_COLUMNS):
        raise RuntimeError("payments不是完整9d结构或已处于半迁移状态")

    settlement_indexes = _index_columns("quarterly_settlements")
    natural_key = ("client_id", "platform_id", "fee_plan_id", "year", "quarter")
    if rebuild_settlements:
        if not any(
            unique and columns == natural_key and not sql
            for unique, columns, sql in settlement_indexes.values()
        ):
            raise RuntimeError("quarterly_settlements缺少旧表级自然键唯一约束")
    else:
        active_index = settlement_indexes.get("uq_settlement_group_period_active")
        if (
            active_index is None
            or not active_index[0]
            or active_index[1] != natural_key
            or "WHERE status != 'VOID'" not in active_index[2]
            or not any(
                unique and columns == natural_key + ("version_no",)
                for unique, columns, _sql in settlement_indexes.values()
            )
        ):
            raise RuntimeError("预创建的Settlement版本表缺少完整版本/活动唯一索引")
        _require_zero_rows(
            "SELECT id FROM quarterly_settlements WHERE version_no < 1 LIMIT 1",
            "预创建的Settlement版本表包含非法版本号",
        )

    expected_parent_fks = {
        (("previous_settlement_id",), "quarterly_settlements", ("id",), "RESTRICT"),
        (("company_id",), "companies", ("id",), "RESTRICT"),
        (("fc_id",), "fcs", ("id",), "RESTRICT"),
    }
    if not expected_parent_fks.issubset(_foreign_keys("quarterly_settlements")):
        raise RuntimeError("quarterly_settlements自引用或冻结归属外键不完整")
    if not rebuild_settlements and (
        (("replaces_settlement_id",), "quarterly_settlements", ("id",), "RESTRICT")
        not in _foreign_keys("quarterly_settlements")
    ):
        raise RuntimeError("预创建的Settlement版本表缺少替代记录RESTRICT外键")

    incoming_fks = {
        "settlement_account_lines": (
            ("settlement_id",), "quarterly_settlements", ("id",), "CASCADE"
        ),
        "invoices": (("settlement_id",), "quarterly_settlements", ("id",), "RESTRICT"),
        "invoice_sources": (
            ("settlement_id",), "quarterly_settlements", ("id",), "RESTRICT"
        ),
        "invoice_lines": (
            ("source_settlement_id",), "quarterly_settlements", ("id",), "RESTRICT"
        ),
    }
    for table_name, expected in incoming_fks.items():
        if expected not in _foreign_keys(table_name):
            raise RuntimeError(f"{table_name}引用Settlement的外键结构不符合9d契约")

    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        affected = ", ".join(sorted({str(row[0]) for row in violations}))
        raise RuntimeError(f"数据库存在外键违规（涉及{affected}），已停止0.2.14迁移")

    _require_zero_rows(
        """
        SELECT id
        FROM invoices
        WHERE lifecycle_status = 'VOID'
          AND (
            void_reason IS NULL
            OR length(trim(void_reason, char(9) || char(10) || char(13) || ' ')) < 2
            OR length(trim(void_reason, char(9) || char(10) || char(13) || ' ')) > 500
            OR voided_at IS NULL
            OR trim(CAST(voided_at AS TEXT)) = ''
          )
        LIMIT 1
        """,
        "存在缺少完整作废原因或作废时间的旧VOID Invoice；已在任何DDL前停止迁移",
    )

    triggers = _trigger_sql()
    expected_triggers = set(SETTLEMENT_TRIGGER_NAMES) | set(INVOICE_TRIGGER_NAMES)
    if set(triggers) != expected_triggers or any(not triggers[name].strip() for name in expected_triggers):
        missing = sorted(expected_triggers - set(triggers))
        extra = sorted(set(triggers) - expected_triggers)
        raise RuntimeError(
            "数据库不是完整35-Trigger的9d结构；"
            f"缺少={missing or '无'}，额外={extra or '无'}"
        )

    payment_info = {
        str(row[1]): row
        for row in connection.exec_driver_sql('PRAGMA table_info("payments")').fetchall()
    }
    payment_fks = _foreign_keys("payments")
    payment_indexes = _index_columns("payments")
    payment_proof_is_target = (
        bool(payment_info.get("proof_attachment_id") and payment_info["proof_attachment_id"][3])
        and (("proof_attachment_id",), "attachments", ("id",), "RESTRICT") in payment_fks
        and (("invoice_id",), "invoices", ("id",), "RESTRICT") in payment_fks
        and any(
            unique and columns == ("proof_attachment_id",)
            for unique, columns, _sql in payment_indexes.values()
        )
    )
    payment_is_target = (
        payment_columns == TARGET_PAYMENT_COLUMNS
        and payment_proof_is_target
        and bool(
            payment_info.get("company_difference_cents")
            and payment_info["company_difference_cents"][3]
        )
    )
    if payment_is_target:
        payment_check_names = {
            str(check.get("name") or "")
            for check in sa.inspect(connection).get_check_constraints("payments")
        }
        required_payment_checks = {
            "ck_payment_amount_positive",
            "ck_payment_company_difference_nonnegative",
            "ck_payment_difference_reason",
        }
        difference_default = str(payment_info["company_difference_cents"][4] or "").strip(
            "()'\" "
        )
        if (
            not required_payment_checks.issubset(payment_check_names)
            or difference_default != "0"
        ):
            raise RuntimeError("payments差额字段默认值或CHECK约束不是完整0.2.14目标结构")
    if payment_columns == TARGET_PAYMENT_COLUMNS and not payment_is_target:
        raise RuntimeError("payments差额字段或凭证约束不是完整0.2.14目标结构")
    if payment_proof_is_target and payment_columns != TARGET_PAYMENT_COLUMNS:
        raise RuntimeError("payments已部分收紧但缺少0.2.14差额字段，已按半迁移状态安全停止")
    if payment_is_target and not trusted_legacy_bootstrap:
        raise RuntimeError(
            "payments已呈现0.2.14目标形状但revision仍为9d；"
            "已按半迁移状态安全停止"
        )
    rebuild_payments = not payment_is_target

    _require_zero_rows(
        """
        SELECT payment.id
        FROM payments AS payment
        JOIN invoices AS invoice ON invoice.id = payment.invoice_id
        WHERE invoice.lifecycle_status != 'ISSUED'
        LIMIT 1
        """,
        "存在旧Payment引用非ISSUED Invoice；无法安全建立活动Allocation，已停止迁移",
    )
    _require_zero_rows(
        """
        SELECT invoice.id
        FROM invoices AS invoice
        JOIN payments AS payment ON payment.invoice_id = invoice.id
        GROUP BY invoice.id, invoice.amount_cents
        HAVING SUM(payment.amount_cents) != invoice.amount_cents
        LIMIT 1
        """,
        "存在旧Payment未完整结清Invoice；不得静默迁移部分付款，请先受控处理",
    )
    _require_zero_rows(
        """
        SELECT payment.id
        FROM payments AS payment
        LEFT JOIN attachments AS proof ON proof.id = payment.proof_attachment_id
        WHERE payment.proof_attachment_id IS NULL
           OR proof.id IS NULL
           OR proof.entity_type != 'PAYMENT'
           OR proof.entity_id IS NOT payment.id
           OR proof.size_bytes <= 0
           OR length(proof.sha256) != 64
        LIMIT 1
        """,
        "存在旧Payment缺少已匹配的PAYMENT凭证；不得伪造凭证或直接迁移",
    )
    _require_zero_rows(
        """
        SELECT proof_attachment_id
        FROM payments
        WHERE proof_attachment_id IS NOT NULL
        GROUP BY proof_attachment_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """,
        "存在多个旧Payment共用同一凭证，已停止迁移",
    )
    _require_zero_rows(
        """
        SELECT id
        FROM payments
        WHERE method IS NULL
           OR length(trim(method, char(9) || char(10) || char(13) || ' ')) < 1
           OR length(trim(method, char(9) || char(10) || char(13) || ' ')) > 80
        LIMIT 1
        """,
        "存在付款方式为空或超过80字符的旧Payment；已在任何DDL前停止迁移",
    )
    _validate_legacy_payment_proof_files()

    settlement_count = int(connection.exec_driver_sql("SELECT COUNT(*) FROM quarterly_settlements").scalar())
    payment_count = int(connection.exec_driver_sql("SELECT COUNT(*) FROM payments").scalar())
    return triggers, settlement_count, payment_count, rebuild_settlements, rebuild_payments


def _drop_triggers(names: Iterable[str]) -> None:
    for name in names:
        op.execute(f'DROP TRIGGER IF EXISTS "{name}"')


def _rebuild_settlements() -> None:
    op.create_table(
        "_quarterly_settlements_0214",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("platform_id", sa.Integer(), nullable=False),
        sa.Column("fee_plan_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("fc_id", sa.Integer(), nullable=True),
        sa.Column("previous_settlement_id", sa.Integer(), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("replaces_settlement_id", sa.Integer(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("quarter", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("closing_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("beginning_cents", sa.Integer(), nullable=False),
        sa.Column("contribution_cents", sa.Integer(), nullable=False),
        sa.Column("withdrawal_cents", sa.Integer(), nullable=False),
        sa.Column("net_contribution_cents", sa.Integer(), nullable=False),
        sa.Column("closing_cents", sa.Integer(), nullable=False),
        sa.Column("gain_loss_cents", sa.Integer(), nullable=False),
        sa.Column("period_rate_ppm", sa.Integer(), nullable=True),
        sa.Column("original_hwm_cents", sa.Integer(), nullable=False),
        sa.Column("adjusted_hwm_cents", sa.Integer(), nullable=False),
        sa.Column("watermark_difference_cents", sa.Integer(), nullable=False),
        sa.Column("chargeable_above_hwm_cents", sa.Integer(), nullable=False),
        sa.Column("service_fee_cents", sa.Integer(), nullable=False),
        sa.Column("next_hwm_cents", sa.Integer(), nullable=False),
        sa.Column("fee_rate_bps", sa.Integer(), nullable=False),
        sa.Column("formula_version", sa.String(length=30), nullable=False),
        sa.Column("calculation_mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_no >= 1", name="ck_settlement_version_positive"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["platform_id"], ["platforms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fee_plan_id"], ["fee_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["fc_id"], ["fcs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["previous_settlement_id"], ["_quarterly_settlements_0214.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["replaces_settlement_id"], ["_quarterly_settlements_0214.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_id",
            "platform_id",
            "fee_plan_id",
            "year",
            "quarter",
            "version_no",
            name="uq_settlement_group_period_version",
        ),
        sa.UniqueConstraint("replaces_settlement_id", name="uq_settlement_replaces"),
    )
    old_columns = [
        "id", "client_id", "platform_id", "fee_plan_id", "company_id", "fc_id",
        "previous_settlement_id", "year", "quarter", "start_date", "closing_date", "days",
        "beginning_cents", "contribution_cents", "withdrawal_cents", "net_contribution_cents",
        "closing_cents", "gain_loss_cents", "period_rate_ppm", "original_hwm_cents",
        "adjusted_hwm_cents", "watermark_difference_cents", "chargeable_above_hwm_cents",
        "service_fee_cents", "next_hwm_cents", "fee_rate_bps", "formula_version",
        "calculation_mode", "status", "finalized_at", "void_reason", "created_at", "updated_at",
    ]
    destination_columns = old_columns[:7] + ["version_no", "replaces_settlement_id"] + old_columns[7:]
    destination_sql = ", ".join(f'"{name}"' for name in destination_columns)
    source_sql = ", ".join(
        "1" if name == "version_no" else "NULL" if name == "replaces_settlement_id" else f'"{name}"'
        for name in destination_columns
    )
    op.execute(
        f'INSERT INTO "_quarterly_settlements_0214" ({destination_sql}) '
        f'SELECT {source_sql} FROM "quarterly_settlements"'
    )
    op.drop_table("quarterly_settlements")
    op.rename_table("_quarterly_settlements_0214", "quarterly_settlements")

    for column_name in (
        "client_id", "platform_id", "fee_plan_id", "company_id", "fc_id",
        "previous_settlement_id", "replaces_settlement_id", "year", "quarter",
        "calculation_mode", "status",
    ):
        op.create_index(f"ix_quarterly_settlements_{column_name}", "quarterly_settlements", [column_name])
    op.create_index(
        "uq_settlement_group_period_active",
        "quarterly_settlements",
        ["client_id", "platform_id", "fee_plan_id", "year", "quarter"],
        unique=True,
        sqlite_where=sa.text("status != 'VOID'"),
    )


def _rebuild_payments() -> None:
    op.create_table(
        "_payments_0214",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "company_difference_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("difference_reason", sa.Text(), nullable=True),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("proof_attachment_id", sa.Integer(), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_payment_amount_positive"),
        sa.CheckConstraint(
            "company_difference_cents >= 0",
            name="ck_payment_company_difference_nonnegative",
        ),
        sa.CheckConstraint(
            "(company_difference_cents = 0 AND difference_reason IS NULL) OR "
            "(company_difference_cents > 0 AND difference_reason IS NOT NULL "
            "AND trim(difference_reason) != '')",
            name="ck_payment_difference_reason",
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proof_attachment_id"], ["attachments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proof_attachment_id", name="uq_payment_proof_attachment"),
    )
    legacy_columns = ", ".join(f'"{name}"' for name in LEGACY_PAYMENT_COLUMN_ORDER)
    op.execute(
        'INSERT INTO "_payments_0214" '
        f'({legacy_columns}, "company_difference_cents", "difference_reason") '
        f'SELECT {legacy_columns}, 0, NULL FROM "payments"'
    )
    op.drop_table("payments")
    op.rename_table("_payments_0214", "payments")
    op.create_index("ix_payments_invoice_id", "payments", ["invoice_id"])
    op.create_index("ix_payments_proof_attachment_id", "payments", ["proof_attachment_id"])


def _create_ledger_tables() -> None:
    op.create_table(
        "invoice_corrections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("original_invoice_id", sa.Integer(), nullable=False),
        sa.Column("replacement_invoice_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('OPEN', 'COMPLETED')", name="ck_invoice_correction_status"),
        sa.ForeignKeyConstraint(["original_invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["replacement_invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("original_invoice_id", name="uq_invoice_correction_original"),
        sa.UniqueConstraint("replacement_invoice_id", name="uq_invoice_correction_replacement"),
    )
    op.create_index("ix_invoice_corrections_original_invoice_id", "invoice_corrections", ["original_invoice_id"])
    op.create_index(
        "ix_invoice_corrections_replacement_invoice_id",
        "invoice_corrections",
        ["replacement_invoice_id"],
    )
    op.create_index("ix_invoice_corrections_status", "invoice_corrections", ["status"])

    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("entry_type", sa.String(length=20), nullable=False),
        sa.Column("reverses_allocation_id", sa.Integer(), nullable=True),
        sa.Column("correction_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_payment_allocation_amount_positive"),
        sa.CheckConstraint(
            "entry_type IN ('APPLY', 'REVERSAL')", name="ck_payment_allocation_entry_type"
        ),
        sa.CheckConstraint(
            "(entry_type = 'APPLY' AND reverses_allocation_id IS NULL) OR "
            "(entry_type = 'REVERSAL' AND reverses_allocation_id IS NOT NULL "
            "AND correction_id IS NOT NULL)",
            name="ck_payment_allocation_reversal_shape",
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["reverses_allocation_id"], ["payment_allocations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["correction_id"], ["invoice_corrections.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reverses_allocation_id", name="uq_payment_allocation_reverses"),
    )
    for column_name in (
        "payment_id", "invoice_id", "entry_type", "reverses_allocation_id", "correction_id"
    ):
        op.create_index(f"ix_payment_allocations_{column_name}", "payment_allocations", [column_name])

    op.create_table(
        "payment_refunds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("correction_id", sa.Integer(), nullable=False),
        sa.Column("refund_date", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("proof_attachment_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_payment_refund_amount_positive"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["correction_id"], ["invoice_corrections.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proof_attachment_id"], ["attachments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proof_attachment_id", name="uq_payment_refund_proof_attachment"),
    )
    for column_name in ("payment_id", "correction_id", "proof_attachment_id"):
        op.create_index(f"ix_payment_refunds_{column_name}", "payment_refunds", [column_name])

    op.create_table(
        "invoice_adjustments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("correction_id", sa.Integer(), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("adjustment_type", sa.String(length=40), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_cents > 0", name="ck_invoice_adjustment_amount_positive"),
        sa.CheckConstraint(
            "adjustment_type = 'COMPANY_BORNE_DIFFERENCE'", name="ck_invoice_adjustment_type"
        ),
        sa.CheckConstraint(
            "(payment_id IS NOT NULL AND correction_id IS NULL) OR "
            "(payment_id IS NULL AND correction_id IS NOT NULL)",
            name="ck_invoice_adjustment_owner_shape",
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["correction_id"], ["invoice_corrections.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_invoice_adjustment_payment"),
    )
    for column_name in ("invoice_id", "correction_id", "payment_id", "adjustment_type"):
        op.create_index(f"ix_invoice_adjustments_{column_name}", "invoice_adjustments", [column_name])


def _backfill_payment_allocations() -> None:
    op.execute(
        """
        INSERT INTO payment_allocations (
            id, payment_id, invoice_id, amount_cents, entry_type,
            reverses_allocation_id, correction_id, created_at, updated_at
        )
        SELECT
            payment.id, payment.id, payment.invoice_id, payment.amount_cents, 'APPLY',
            NULL, NULL, payment.created_at, payment.updated_at
        FROM payments AS payment
        ORDER BY payment.id
        """
    )


def _restore_settlement_triggers(previous_sql: dict[str, str]) -> None:
    for trigger_name in SETTLEMENT_TRIGGER_NAMES:
        if trigger_name not in REPLACED_SETTLEMENT_TRIGGERS:
            trigger_sql = previous_sql[trigger_name]
            if trigger_name == "trg_settlement_validate_finalize":
                begin_marker = "\n        BEGIN\n"
                if trigger_sql.count(begin_marker) != 1:
                    raise RuntimeError("无法安全补强Settlement Finalize父链Trigger")
                container_parent_check = """
            SELECT RAISE(ABORT, 'settlement_container_previous_changed')
            WHERE NEW.previous_settlement_id IS NOT (
                SELECT prior.id
                FROM quarterly_settlements AS prior
                WHERE prior.client_id = NEW.client_id
                  AND prior.platform_id = NEW.platform_id
                  AND prior.fee_plan_id = NEW.fee_plan_id
                  AND prior.status = 'FINALIZED'
                  AND (prior.year * 4 + prior.quarter) <
                      (NEW.year * 4 + NEW.quarter)
                ORDER BY prior.year DESC, prior.quarter DESC
                LIMIT 1
            );
"""
                trigger_sql = trigger_sql.replace(
                    begin_marker,
                    begin_marker + container_parent_check,
                    1,
                )
            op.execute(trigger_sql)

    op.execute(
        """
        CREATE TRIGGER trg_settlement_insert_draft_only
        BEFORE INSERT ON quarterly_settlements
        BEGIN
            SELECT RAISE(ABORT, 'settlement_insert_must_be_draft')
            WHERE NEW.status != 'DRAFT'
               OR NEW.finalized_at IS NOT NULL
               OR NEW.void_reason IS NOT NULL;

            SELECT RAISE(ABORT, 'settlement_version_invalid')
            WHERE NEW.version_no < 1
               OR (NEW.version_no = 1 AND NEW.replaces_settlement_id IS NOT NULL)
               OR (NEW.version_no > 1 AND NEW.replaces_settlement_id IS NULL);

            SELECT RAISE(ABORT, 'settlement_replacement_invalid')
            WHERE NEW.version_no > 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM quarterly_settlements AS replaced
                  WHERE replaced.id = NEW.replaces_settlement_id
                    AND replaced.status = 'VOID'
                    AND replaced.client_id = NEW.client_id
                    AND replaced.platform_id = NEW.platform_id
                    AND replaced.fee_plan_id = NEW.fee_plan_id
                    AND replaced.year = NEW.year
                    AND replaced.quarter = NEW.quarter
                    AND replaced.version_no + 1 = NEW.version_no
              );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_parent_financial_lock
        BEFORE UPDATE ON quarterly_settlements
        WHEN NEW.version_no IS NOT OLD.version_no
          OR NEW.replaces_settlement_id IS NOT OLD.replaces_settlement_id
          OR (
            OLD.status IN ('FINALIZED', 'VOID')
            AND (
              NEW.id IS NOT OLD.id
              OR NEW.client_id IS NOT OLD.client_id
              OR NEW.platform_id IS NOT OLD.platform_id
              OR NEW.fee_plan_id IS NOT OLD.fee_plan_id
              OR NEW.company_id IS NOT OLD.company_id
              OR NEW.fc_id IS NOT OLD.fc_id
              OR NEW.previous_settlement_id IS NOT OLD.previous_settlement_id
              OR NEW.year IS NOT OLD.year
              OR NEW.quarter IS NOT OLD.quarter
              OR NEW.start_date IS NOT OLD.start_date
              OR NEW.closing_date IS NOT OLD.closing_date
              OR NEW.days IS NOT OLD.days
              OR NEW.beginning_cents IS NOT OLD.beginning_cents
              OR NEW.contribution_cents IS NOT OLD.contribution_cents
              OR NEW.withdrawal_cents IS NOT OLD.withdrawal_cents
              OR NEW.net_contribution_cents IS NOT OLD.net_contribution_cents
              OR NEW.closing_cents IS NOT OLD.closing_cents
              OR NEW.gain_loss_cents IS NOT OLD.gain_loss_cents
              OR NEW.period_rate_ppm IS NOT OLD.period_rate_ppm
              OR NEW.original_hwm_cents IS NOT OLD.original_hwm_cents
              OR NEW.adjusted_hwm_cents IS NOT OLD.adjusted_hwm_cents
              OR NEW.watermark_difference_cents IS NOT OLD.watermark_difference_cents
              OR NEW.chargeable_above_hwm_cents IS NOT OLD.chargeable_above_hwm_cents
              OR NEW.service_fee_cents IS NOT OLD.service_fee_cents
              OR NEW.next_hwm_cents IS NOT OLD.next_hwm_cents
              OR NEW.fee_rate_bps IS NOT OLD.fee_rate_bps
              OR NEW.formula_version IS NOT OLD.formula_version
              OR NEW.calculation_mode IS NOT OLD.calculation_mode
              OR NEW.finalized_at IS NOT OLD.finalized_at
              OR NEW.created_at IS NOT OLD.created_at
              OR (
                NEW.void_reason IS NOT OLD.void_reason
                AND NOT (
                  OLD.status = 'FINALIZED'
                  AND NEW.status = 'VOID'
                  AND NEW.void_reason IS NOT NULL
                  AND trim(NEW.void_reason) != ''
                )
              )
            )
          )
          OR (
            OLD.status = 'DRAFT'
            AND OLD.replaces_settlement_id IS NOT NULL
            AND (
              NEW.client_id IS NOT OLD.client_id
              OR NEW.platform_id IS NOT OLD.platform_id
              OR NEW.fee_plan_id IS NOT OLD.fee_plan_id
              OR NEW.year IS NOT OLD.year
              OR NEW.quarter IS NOT OLD.quarter
              OR NEW.status = 'FINALIZED'
            )
          )
        BEGIN
            SELECT RAISE(ABORT, 'settlement_replacement_identity_immutable')
            WHERE OLD.status = 'DRAFT'
              AND OLD.replaces_settlement_id IS NOT NULL
              AND (
                NEW.client_id IS NOT OLD.client_id
                OR NEW.platform_id IS NOT OLD.platform_id
                OR NEW.fee_plan_id IS NOT OLD.fee_plan_id
                OR NEW.year IS NOT OLD.year
                OR NEW.quarter IS NOT OLD.quarter
              );

            SELECT RAISE(ABORT, 'settlement_replacement_invalid_at_finalize')
            WHERE OLD.status = 'DRAFT'
              AND NEW.status = 'FINALIZED'
              AND OLD.replaces_settlement_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1
                FROM quarterly_settlements AS replaced
                WHERE replaced.id = NEW.replaces_settlement_id
                  AND replaced.status = 'VOID'
                  AND replaced.client_id = NEW.client_id
                  AND replaced.platform_id = NEW.platform_id
                  AND replaced.fee_plan_id = NEW.fee_plan_id
                  AND replaced.year = NEW.year
                  AND replaced.quarter = NEW.quarter
                  AND replaced.version_no + 1 = NEW.version_no
              );

            SELECT RAISE(ABORT, 'settlement_financial_history_immutable')
            WHERE NOT (
              OLD.status = 'DRAFT'
              AND NEW.status = 'FINALIZED'
              AND OLD.replaces_settlement_id IS NOT NULL
              AND NEW.version_no IS OLD.version_no
              AND NEW.replaces_settlement_id IS OLD.replaces_settlement_id
              AND NEW.client_id IS OLD.client_id
              AND NEW.platform_id IS OLD.platform_id
              AND NEW.fee_plan_id IS OLD.fee_plan_id
              AND NEW.year IS OLD.year
              AND NEW.quarter IS OLD.quarter
            );
        END
        """
    )


def _restore_invoice_triggers(previous_sql: dict[str, str]) -> None:
    for trigger_name in INVOICE_TRIGGER_NAMES:
        if trigger_name not in REPLACED_INVOICE_TRIGGERS:
            op.execute(previous_sql[trigger_name])

    op.execute(
        """
        CREATE TRIGGER trg_invoice_lifecycle_transition
        BEFORE UPDATE OF lifecycle_status, void_reason, voided_at ON invoices
        BEGIN
            SELECT RAISE(ABORT, 'invoice_lifecycle_transition_invalid')
            WHERE NEW.lifecycle_status IS NOT OLD.lifecycle_status
              AND NOT (
                  (OLD.lifecycle_status = 'DRAFT' AND NEW.lifecycle_status IN ('ISSUING', 'VOID'))
                  OR (OLD.lifecycle_status = 'ISSUING' AND NEW.lifecycle_status IN ('ISSUED', 'DRAFT'))
                  OR (OLD.lifecycle_status = 'ISSUED' AND NEW.lifecycle_status = 'VOID')
              );

            SELECT RAISE(ABORT, 'invoice_void_metadata_invalid')
            WHERE OLD.lifecycle_status != 'VOID'
              AND NEW.lifecycle_status = 'VOID'
              AND (
                NEW.void_reason IS NULL
                OR length(trim(
                    NEW.void_reason,
                    char(9) || char(10) || char(13) || ' '
                )) < 2
                OR length(trim(
                    NEW.void_reason,
                    char(9) || char(10) || char(13) || ' '
                )) > 500
                OR NEW.voided_at IS NULL
                OR trim(CAST(NEW.voided_at AS TEXT)) = ''
              );

            SELECT RAISE(ABORT, 'invoice_void_metadata_immutable')
            WHERE OLD.lifecycle_status = 'VOID'
              AND (
                NEW.void_reason IS NOT OLD.void_reason
                OR NEW.voided_at IS NOT OLD.voided_at
              );
        END
        """
    )


def _create_payment_and_correction_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER trg_payment_validate_insert
        BEFORE INSERT ON payments
        BEGIN
            SELECT RAISE(ABORT, 'payment_method_invalid')
            WHERE NEW.method IS NULL
               OR length(trim(
                    NEW.method,
                    char(9) || char(10) || char(13) || ' '
               )) < 1
               OR length(trim(
                    NEW.method,
                    char(9) || char(10) || char(13) || ' '
               )) > 80;
            SELECT RAISE(ABORT, 'payment_invoice_must_be_issued')
            WHERE COALESCE((
                SELECT lifecycle_status FROM invoices WHERE id = NEW.invoice_id
            ), '') != 'ISSUED';
            SELECT RAISE(ABORT, 'payment_invoice_group_has_open_correction')
            WHERE EXISTS (
                SELECT 1
                FROM invoices AS target
                JOIN invoice_corrections AS correction ON correction.status = 'OPEN'
                JOIN invoices AS original ON original.id = correction.original_invoice_id
                WHERE target.id = NEW.invoice_id
                  AND original.client_id = target.client_id
                  AND original.year = target.year
                  AND original.quarter = target.quarter
                  AND original.fee_plan_id = target.fee_plan_id
            );
            SELECT RAISE(ABORT, 'payment_invoice_ledger_not_empty')
            WHERE EXISTS (
                    SELECT 1 FROM payments AS existing
                    WHERE existing.invoice_id = NEW.invoice_id
                  )
               OR EXISTS (
                    SELECT 1 FROM payment_allocations AS allocation
                    WHERE allocation.invoice_id = NEW.invoice_id
                  )
               OR EXISTS (
                    SELECT 1 FROM invoice_adjustments AS adjustment
                    WHERE adjustment.invoice_id = NEW.invoice_id
                  );
            SELECT RAISE(ABORT, 'payment_amount_invalid')
            WHERE NEW.amount_cents <= 0;
            SELECT RAISE(ABORT, 'payment_difference_invalid')
            WHERE NEW.company_difference_cents < 0
               OR (NEW.company_difference_cents = 0 AND NEW.difference_reason IS NOT NULL)
               OR (NEW.company_difference_cents > 0 AND (
                    NEW.difference_reason IS NULL OR trim(NEW.difference_reason) = ''
               ));
            SELECT RAISE(ABORT, 'payment_must_settle_invoice')
            WHERE NEW.amount_cents + NEW.company_difference_cents
                != COALESCE((SELECT amount_cents FROM invoices WHERE id = NEW.invoice_id), -1);
            SELECT RAISE(ABORT, 'payment_proof_invalid')
            WHERE NOT EXISTS (
                SELECT 1
                FROM attachments AS proof
                WHERE proof.id = NEW.proof_attachment_id
                  AND proof.entity_type = 'PAYMENT'
                  AND proof.entity_id IS NULL
                  AND proof.size_bytes > 0
                  AND length(proof.sha256) = 64
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_claim_proof
        AFTER INSERT ON payments
        BEGIN
            UPDATE attachments SET entity_id = NEW.id WHERE id = NEW.proof_attachment_id;
            INSERT INTO invoice_adjustments (
                invoice_id, correction_id, payment_id, adjustment_type,
                amount_cents, reason, created_at, updated_at
            )
            SELECT
                NEW.invoice_id, NULL, NEW.id, 'COMPANY_BORNE_DIFFERENCE',
                NEW.company_difference_cents, NEW.difference_reason,
                NEW.created_at, NEW.updated_at
            WHERE NEW.company_difference_cents > 0;
            INSERT INTO payment_allocations (
                payment_id, invoice_id, amount_cents, entry_type,
                reverses_allocation_id, correction_id, created_at, updated_at
            ) VALUES (
                NEW.id, NEW.invoice_id, NEW.amount_cents, 'APPLY',
                NULL, NULL, NEW.created_at, NEW.updated_at
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_update_immutable
        BEFORE UPDATE ON payments
        BEGIN
            SELECT RAISE(ABORT, 'payment_history_immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_payment_allocation_validate_insert
        BEFORE INSERT ON payment_allocations
        BEGIN
            SELECT RAISE(ABORT, 'payment_allocation_target_invalid')
            WHERE NOT EXISTS (
                SELECT 1 FROM payments AS payment
                JOIN invoices AS invoice ON invoice.id = NEW.invoice_id
                WHERE payment.id = NEW.payment_id
                  AND invoice.lifecycle_status = 'ISSUED'
            );

            SELECT RAISE(ABORT, 'payment_allocation_apply_invalid')
            WHERE NEW.entry_type = 'APPLY'
              AND (
                NEW.reverses_allocation_id IS NOT NULL
                OR (
                    NEW.correction_id IS NULL
                    AND NEW.invoice_id IS NOT (
                        SELECT invoice_id FROM payments WHERE id = NEW.payment_id
                    )
                )
                OR (
                    NEW.correction_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1
                        FROM invoice_corrections AS correction
                        WHERE correction.id = NEW.correction_id
                          AND correction.status = 'OPEN'
                          AND correction.replacement_invoice_id = NEW.invoice_id
                          AND EXISTS (
                              SELECT 1
                              FROM payment_allocations AS reversal
                              WHERE reversal.correction_id = correction.id
                                AND reversal.payment_id = NEW.payment_id
                                AND reversal.invoice_id = correction.original_invoice_id
                                AND reversal.entry_type = 'REVERSAL'
                          )
                    )
                )
                OR (
                    NEW.correction_id IS NOT NULL
                    AND NEW.amount_cents + COALESCE((
                        SELECT SUM(applied.amount_cents)
                        FROM payment_allocations AS applied
                        WHERE applied.correction_id = NEW.correction_id
                          AND applied.payment_id = NEW.payment_id
                          AND applied.entry_type = 'APPLY'
                    ), 0) > COALESCE((
                        SELECT SUM(reversal.amount_cents)
                        FROM payment_allocations AS reversal
                        WHERE reversal.correction_id = NEW.correction_id
                          AND reversal.payment_id = NEW.payment_id
                          AND reversal.entry_type = 'REVERSAL'
                    ), 0)
                )
              );

            SELECT RAISE(ABORT, 'payment_allocation_reversal_invalid')
            WHERE NEW.entry_type = 'REVERSAL'
              AND NOT EXISTS (
                  SELECT 1
                  FROM payment_allocations AS applied
                  JOIN invoice_corrections AS correction ON correction.id = NEW.correction_id
                  WHERE applied.id = NEW.reverses_allocation_id
                    AND applied.entry_type = 'APPLY'
                    AND applied.payment_id = NEW.payment_id
                    AND applied.invoice_id = NEW.invoice_id
                    AND applied.amount_cents = NEW.amount_cents
                    AND correction.original_invoice_id = NEW.invoice_id
                    AND correction.status = 'OPEN'
              );

            SELECT RAISE(ABORT, 'payment_allocation_type_invalid')
            WHERE NEW.entry_type NOT IN ('APPLY', 'REVERSAL') OR NEW.amount_cents <= 0;

            SELECT RAISE(ABORT, 'payment_allocation_exceeds_payment')
            WHERE (
                COALESCE((
                    SELECT SUM(CASE allocation.entry_type
                        WHEN 'APPLY' THEN allocation.amount_cents
                        ELSE -allocation.amount_cents END)
                    FROM payment_allocations AS allocation
                    WHERE allocation.payment_id = NEW.payment_id
                ), 0)
                + CASE NEW.entry_type WHEN 'APPLY' THEN NEW.amount_cents ELSE -NEW.amount_cents END
                + COALESCE((
                    SELECT SUM(refund.amount_cents)
                    FROM payment_refunds AS refund
                    WHERE refund.payment_id = NEW.payment_id
                ), 0)
            ) NOT BETWEEN 0 AND (SELECT amount_cents FROM payments WHERE id = NEW.payment_id);

            SELECT RAISE(ABORT, 'payment_allocation_exceeds_invoice')
            WHERE (
                COALESCE((
                    SELECT SUM(CASE allocation.entry_type
                        WHEN 'APPLY' THEN allocation.amount_cents
                        ELSE -allocation.amount_cents END)
                    FROM payment_allocations AS allocation
                    WHERE allocation.invoice_id = NEW.invoice_id
                ), 0)
                + CASE NEW.entry_type WHEN 'APPLY' THEN NEW.amount_cents ELSE -NEW.amount_cents END
                + COALESCE((
                    SELECT SUM(adjustment.amount_cents)
                    FROM invoice_adjustments AS adjustment
                    WHERE adjustment.invoice_id = NEW.invoice_id
                ), 0)
            ) NOT BETWEEN 0 AND (SELECT amount_cents FROM invoices WHERE id = NEW.invoice_id);

            SELECT RAISE(ABORT, 'ordinary_payment_must_settle_invoice')
            WHERE NEW.entry_type = 'APPLY'
              AND NEW.correction_id IS NULL
              AND (
                  COALESCE((
                      SELECT SUM(CASE allocation.entry_type
                          WHEN 'APPLY' THEN allocation.amount_cents
                          ELSE -allocation.amount_cents END)
                      FROM payment_allocations AS allocation
                      WHERE allocation.invoice_id = NEW.invoice_id
                  ), 0)
                  + NEW.amount_cents
                  + COALESCE((
                      SELECT SUM(adjustment.amount_cents)
                      FROM invoice_adjustments AS adjustment
                      WHERE adjustment.invoice_id = NEW.invoice_id
                  ), 0)
              ) != (SELECT amount_cents FROM invoices WHERE id = NEW.invoice_id);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_allocation_update_immutable
        BEFORE UPDATE ON payment_allocations
        BEGIN
            SELECT RAISE(ABORT, 'payment_allocation_history_immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_allocation_delete_immutable
        BEFORE DELETE ON payment_allocations
        BEGIN
            SELECT RAISE(ABORT, 'payment_allocation_history_immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_payment_refund_validate_insert
        BEFORE INSERT ON payment_refunds
        BEGIN
            SELECT RAISE(ABORT, 'payment_refund_method_invalid')
            WHERE NEW.method IS NULL
               OR length(trim(
                    NEW.method,
                    char(9) || char(10) || char(13) || ' '
               )) < 1
               OR length(trim(
                    NEW.method,
                    char(9) || char(10) || char(13) || ' '
               )) > 80;
            SELECT RAISE(ABORT, 'payment_refund_invalid')
            WHERE NEW.amount_cents <= 0
               OR NEW.reason IS NULL
               OR trim(NEW.reason) = ''
               OR NOT EXISTS (
                   SELECT 1
                   FROM invoice_corrections AS correction
                   JOIN invoices AS original ON original.id = correction.original_invoice_id
                   WHERE correction.id = NEW.correction_id
                     AND correction.status = 'OPEN'
                     AND original.lifecycle_status = 'VOID'
                     AND EXISTS (
                         SELECT 1
                         FROM payment_allocations AS reversal
                         WHERE reversal.correction_id = correction.id
                           AND reversal.payment_id = NEW.payment_id
                           AND reversal.invoice_id = correction.original_invoice_id
                           AND reversal.entry_type = 'REVERSAL'
                     )
               );
            SELECT RAISE(ABORT, 'payment_refund_proof_invalid')
            WHERE NOT EXISTS (
                SELECT 1 FROM attachments AS proof
                WHERE proof.id = NEW.proof_attachment_id
                  AND proof.entity_type = 'PAYMENT_REFUND'
                  AND proof.entity_id IS NULL
                  AND proof.size_bytes > 0
                  AND length(proof.sha256) = 64
            );
            SELECT RAISE(ABORT, 'payment_refund_exceeds_payment')
            WHERE (
                COALESCE((
                    SELECT SUM(CASE allocation.entry_type
                        WHEN 'APPLY' THEN allocation.amount_cents
                        ELSE -allocation.amount_cents END)
                    FROM payment_allocations AS allocation
                    WHERE allocation.payment_id = NEW.payment_id
                ), 0)
                + COALESCE((
                    SELECT SUM(refund.amount_cents)
                    FROM payment_refunds AS refund
                    WHERE refund.payment_id = NEW.payment_id
                ), 0)
                + NEW.amount_cents
            ) > (SELECT amount_cents FROM payments WHERE id = NEW.payment_id);

            SELECT RAISE(ABORT, 'payment_refund_exceeds_correction_reversal')
            WHERE NEW.amount_cents
                + COALESCE((
                    SELECT SUM(refund.amount_cents)
                    FROM payment_refunds AS refund
                    WHERE refund.correction_id = NEW.correction_id
                      AND refund.payment_id = NEW.payment_id
                ), 0)
                + COALESCE((
                    SELECT SUM(applied.amount_cents)
                    FROM payment_allocations AS applied
                    WHERE applied.correction_id = NEW.correction_id
                      AND applied.payment_id = NEW.payment_id
                      AND applied.entry_type = 'APPLY'
                ), 0)
                > COALESCE((
                    SELECT SUM(reversal.amount_cents)
                    FROM payment_allocations AS reversal
                    WHERE reversal.correction_id = NEW.correction_id
                      AND reversal.payment_id = NEW.payment_id
                      AND reversal.entry_type = 'REVERSAL'
                ), 0);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_refund_claim_proof
        AFTER INSERT ON payment_refunds
        BEGIN
            UPDATE attachments SET entity_id = NEW.id WHERE id = NEW.proof_attachment_id;
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_refund_update_immutable
        BEFORE UPDATE ON payment_refunds
        BEGIN
            SELECT RAISE(ABORT, 'payment_refund_history_immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_refund_delete_immutable
        BEFORE DELETE ON payment_refunds
        BEGIN
            SELECT RAISE(ABORT, 'payment_refund_history_immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_invoice_adjustment_validate_insert
        BEFORE INSERT ON invoice_adjustments
        BEGIN
            SELECT RAISE(ABORT, 'invoice_adjustment_invalid')
            WHERE NEW.adjustment_type != 'COMPANY_BORNE_DIFFERENCE'
               OR NEW.amount_cents <= 0
               OR NEW.reason IS NULL
               OR trim(NEW.reason) = ''
               OR COALESCE((SELECT lifecycle_status FROM invoices WHERE id = NEW.invoice_id), '') != 'ISSUED'
               OR NOT (
                   (
                       NEW.payment_id IS NOT NULL
                       AND NEW.correction_id IS NULL
                       AND EXISTS (
                           SELECT 1
                           FROM payments AS payment
                           WHERE payment.id = NEW.payment_id
                             AND payment.invoice_id = NEW.invoice_id
                             AND payment.company_difference_cents = NEW.amount_cents
                             AND payment.company_difference_cents > 0
                             AND payment.difference_reason IS NEW.reason
                       )
                   )
                   OR
                   (
                       NEW.payment_id IS NULL
                       AND NEW.correction_id IS NOT NULL
                       AND EXISTS (
                           SELECT 1
                           FROM invoice_corrections AS correction
                           WHERE correction.id = NEW.correction_id
                             AND correction.status = 'OPEN'
                             AND correction.replacement_invoice_id = NEW.invoice_id
                             AND EXISTS (
                                 SELECT 1
                                 FROM payment_allocations AS reversal
                                 WHERE reversal.correction_id = correction.id
                                   AND reversal.entry_type = 'REVERSAL'
                             )
                       )
                   )
               );
            SELECT RAISE(ABORT, 'invoice_adjustment_exceeds_invoice')
            WHERE (
                COALESCE((
                    SELECT SUM(CASE allocation.entry_type
                        WHEN 'APPLY' THEN allocation.amount_cents
                        ELSE -allocation.amount_cents END)
                    FROM payment_allocations AS allocation
                    WHERE allocation.invoice_id = NEW.invoice_id
                ), 0)
                + COALESCE((
                    SELECT SUM(adjustment.amount_cents)
                    FROM invoice_adjustments AS adjustment
                    WHERE adjustment.invoice_id = NEW.invoice_id
                ), 0)
                + NEW.amount_cents
            ) > (SELECT amount_cents FROM invoices WHERE id = NEW.invoice_id);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_adjustment_update_immutable
        BEFORE UPDATE ON invoice_adjustments
        BEGIN
            SELECT RAISE(ABORT, 'invoice_adjustment_history_immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_adjustment_delete_immutable
        BEFORE DELETE ON invoice_adjustments
        BEGIN
            SELECT RAISE(ABORT, 'invoice_adjustment_history_immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_attachment_update_block_payment_evidence
        BEFORE UPDATE ON attachments
        WHEN EXISTS (
            SELECT 1 FROM payments AS payment
            WHERE payment.proof_attachment_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM payment_refunds AS refund
            WHERE refund.proof_attachment_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'payment_evidence_immutable')
            WHERE NOT (
                OLD.entity_id IS NULL
                AND NEW.entity_type IS OLD.entity_type
                AND NEW.original_name IS OLD.original_name
                AND NEW.stored_path IS OLD.stored_path
                AND NEW.sha256 IS OLD.sha256
                AND NEW.mime_type IS OLD.mime_type
                AND NEW.size_bytes IS OLD.size_bytes
                AND NEW.created_at IS OLD.created_at
                AND (
                    (OLD.entity_type = 'PAYMENT' AND EXISTS (
                        SELECT 1 FROM payments AS payment
                        WHERE payment.proof_attachment_id = OLD.id AND payment.id = NEW.entity_id
                    ))
                    OR
                    (OLD.entity_type = 'PAYMENT_REFUND' AND EXISTS (
                        SELECT 1 FROM payment_refunds AS refund
                        WHERE refund.proof_attachment_id = OLD.id AND refund.id = NEW.entity_id
                    ))
                )
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_attachment_delete_block_payment_evidence
        BEFORE DELETE ON attachments
        WHEN EXISTS (
            SELECT 1 FROM payments AS payment WHERE payment.proof_attachment_id = OLD.id
        ) OR EXISTS (
            SELECT 1 FROM payment_refunds AS refund WHERE refund.proof_attachment_id = OLD.id
        )
        BEGIN
            SELECT RAISE(ABORT, 'payment_evidence_immutable');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_payment_delete_immutable
        BEFORE DELETE ON payments
        BEGIN
            SELECT RAISE(ABORT, 'payment_history_immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_invoice_correction_validate_insert
        BEFORE INSERT ON invoice_corrections
        BEGIN
            SELECT RAISE(ABORT, 'invoice_correction_insert_invalid')
            WHERE NEW.status != 'OPEN'
               OR NEW.replacement_invoice_id IS NOT NULL
               OR NEW.completed_at IS NOT NULL
               OR NEW.opened_at IS NULL
               OR NEW.reason IS NULL
               OR trim(NEW.reason) = '';
            SELECT RAISE(ABORT, 'invoice_correction_original_invalid')
            WHERE COALESCE((
                SELECT lifecycle_status FROM invoices WHERE id = NEW.original_invoice_id
            ), '') != 'ISSUED';
            SELECT RAISE(ABORT, 'invoice_correction_group_already_open')
            WHERE EXISTS (
                SELECT 1
                FROM invoices AS candidate
                JOIN invoice_corrections AS existing ON existing.status = 'OPEN'
                JOIN invoices AS original ON original.id = existing.original_invoice_id
                WHERE candidate.id = NEW.original_invoice_id
                  AND original.client_id = candidate.client_id
                  AND original.year = candidate.year
                  AND original.quarter = candidate.quarter
                  AND original.fee_plan_id = candidate.fee_plan_id
            );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_correction_validate_update
        BEFORE UPDATE ON invoice_corrections
        BEGIN
            SELECT RAISE(ABORT, 'invoice_correction_history_immutable')
            WHERE OLD.status = 'COMPLETED'
               OR NEW.id IS NOT OLD.id
               OR NEW.original_invoice_id IS NOT OLD.original_invoice_id
               OR NEW.reason IS NOT OLD.reason
               OR NEW.opened_at IS NOT OLD.opened_at
               OR NEW.created_at IS NOT OLD.created_at
               OR (OLD.replacement_invoice_id IS NOT NULL
                   AND NEW.replacement_invoice_id IS NOT OLD.replacement_invoice_id)
               OR (OLD.replacement_invoice_id IS NULL
                   AND NEW.replacement_invoice_id IS NOT NULL
                   AND NEW.status != 'OPEN');

            SELECT RAISE(ABORT, 'invoice_correction_replacement_invalid')
            WHERE NEW.replacement_invoice_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM invoices AS original
                  JOIN invoices AS replacement ON replacement.id = NEW.replacement_invoice_id
                  WHERE original.id = NEW.original_invoice_id
                    AND replacement.id != original.id
                    AND replacement.client_id = original.client_id
                    AND replacement.year = original.year
                    AND replacement.quarter = original.quarter
                    AND replacement.fee_plan_id = original.fee_plan_id
                    AND replacement.lifecycle_status IN ('DRAFT', 'ISSUING', 'ISSUED')
              );

            SELECT RAISE(ABORT, 'invoice_correction_source_lineage_invalid')
            WHERE NEW.replacement_invoice_id IS NOT NULL
              AND (
                  (SELECT COUNT(*) FROM invoice_sources AS original_source
                   WHERE original_source.invoice_id = NEW.original_invoice_id) = 0
                  OR
                  (SELECT COUNT(*) FROM invoice_sources AS original_source
                   WHERE original_source.invoice_id = NEW.original_invoice_id)
                  !=
                  (SELECT COUNT(*) FROM invoice_sources AS replacement_source
                   WHERE replacement_source.invoice_id = NEW.replacement_invoice_id
                     AND replacement_source.active = 1)
                  OR EXISTS (
                      WITH RECURSIVE lineage(replacement_source_id, settlement_id) AS (
                          SELECT replacement_source.id, replacement_source.settlement_id
                          FROM invoice_sources AS replacement_source
                          WHERE replacement_source.invoice_id = NEW.replacement_invoice_id
                            AND replacement_source.active = 1
                          UNION ALL
                          SELECT lineage.replacement_source_id, settlement.replaces_settlement_id
                          FROM lineage
                          JOIN quarterly_settlements AS settlement
                            ON settlement.id = lineage.settlement_id
                          WHERE settlement.replaces_settlement_id IS NOT NULL
                      )
                      SELECT 1
                      FROM lineage
                      LEFT JOIN invoice_sources AS original_source
                        ON original_source.invoice_id = NEW.original_invoice_id
                       AND original_source.settlement_id = lineage.settlement_id
                      GROUP BY lineage.replacement_source_id
                      HAVING COUNT(DISTINCT original_source.id) != 1
                  )
                  OR EXISTS (
                      WITH RECURSIVE lineage(replacement_source_id, settlement_id) AS (
                          SELECT replacement_source.id, replacement_source.settlement_id
                          FROM invoice_sources AS replacement_source
                          WHERE replacement_source.invoice_id = NEW.replacement_invoice_id
                            AND replacement_source.active = 1
                          UNION ALL
                          SELECT lineage.replacement_source_id, settlement.replaces_settlement_id
                          FROM lineage
                          JOIN quarterly_settlements AS settlement
                            ON settlement.id = lineage.settlement_id
                          WHERE settlement.replaces_settlement_id IS NOT NULL
                      )
                      SELECT 1
                      FROM invoice_sources AS original_source
                      LEFT JOIN lineage
                        ON lineage.settlement_id = original_source.settlement_id
                      WHERE original_source.invoice_id = NEW.original_invoice_id
                      GROUP BY original_source.id
                      HAVING COUNT(DISTINCT lineage.replacement_source_id) != 1
                  )
              );

            SELECT RAISE(ABORT, 'invoice_correction_transition_invalid')
            WHERE NEW.status NOT IN ('OPEN', 'COMPLETED')
               OR (NEW.status != OLD.status
                   AND NOT (OLD.status = 'OPEN' AND NEW.status = 'COMPLETED'))
               OR (NEW.status = 'OPEN' AND NEW.completed_at IS NOT NULL)
               OR (NEW.status = 'COMPLETED' AND (
                    NEW.replacement_invoice_id IS NULL
                    OR NEW.completed_at IS NULL
                    OR (SELECT lifecycle_status FROM invoices WHERE id = NEW.original_invoice_id) != 'VOID'
                    OR (SELECT lifecycle_status FROM invoices WHERE id = NEW.replacement_invoice_id) != 'ISSUED'
               ));

            SELECT RAISE(ABORT, 'invoice_correction_blank_replacement_ledger_required')
            WHERE NEW.status = 'COMPLETED'
              AND NOT EXISTS (
                  SELECT 1
                  FROM payment_allocations AS reversal
                  WHERE reversal.correction_id = NEW.id
                    AND reversal.entry_type = 'REVERSAL'
              )
              AND (
                  EXISTS (
                      SELECT 1 FROM payment_allocations AS allocation
                      WHERE allocation.correction_id = NEW.id
                  )
                  OR EXISTS (
                      SELECT 1 FROM payment_refunds AS refund
                      WHERE refund.correction_id = NEW.id
                  )
                  OR EXISTS (
                      SELECT 1 FROM invoice_adjustments AS adjustment
                      WHERE adjustment.correction_id = NEW.id
                  )
                  OR (
                      COALESCE((
                          SELECT SUM(CASE allocation.entry_type
                              WHEN 'APPLY' THEN allocation.amount_cents
                              ELSE -allocation.amount_cents END)
                          FROM payment_allocations AS allocation
                          WHERE allocation.invoice_id = NEW.replacement_invoice_id
                      ), 0)
                      + COALESCE((
                          SELECT SUM(adjustment.amount_cents)
                          FROM invoice_adjustments AS adjustment
                          WHERE adjustment.invoice_id = NEW.replacement_invoice_id
                      ), 0)
                  ) != 0
              );

            SELECT RAISE(ABORT, 'invoice_correction_payment_not_conserved')
            WHERE NEW.status = 'COMPLETED'
              AND EXISTS (
                  SELECT 1
                  FROM payment_allocations AS affected
                  WHERE affected.correction_id = NEW.id
                    AND affected.entry_type = 'REVERSAL'
                  GROUP BY affected.payment_id
                  HAVING SUM(affected.amount_cents) != (
                      COALESCE((
                          SELECT SUM(retained.amount_cents)
                          FROM payment_allocations AS retained
                          WHERE retained.correction_id = NEW.id
                            AND retained.payment_id = affected.payment_id
                            AND retained.entry_type = 'APPLY'
                      ), 0)
                      + COALESCE((
                          SELECT SUM(refund.amount_cents)
                          FROM payment_refunds AS refund
                          WHERE refund.correction_id = NEW.id
                            AND refund.payment_id = affected.payment_id
                      ), 0)
                  )
              );

            SELECT RAISE(ABORT, 'invoice_correction_payment_not_globally_conserved')
            WHERE NEW.status = 'COMPLETED'
              AND EXISTS (
                  SELECT 1
                  FROM payments AS payment
                  WHERE EXISTS (
                      SELECT 1
                      FROM payment_allocations AS affected
                      WHERE affected.correction_id = NEW.id
                        AND affected.payment_id = payment.id
                        AND affected.entry_type = 'REVERSAL'
                  )
                  AND payment.amount_cents != (
                      COALESCE((
                          SELECT SUM(CASE allocation.entry_type
                              WHEN 'APPLY' THEN allocation.amount_cents
                              ELSE -allocation.amount_cents END)
                          FROM payment_allocations AS allocation
                          WHERE allocation.payment_id = payment.id
                      ), 0)
                      + COALESCE((
                          SELECT SUM(refund.amount_cents)
                          FROM payment_refunds AS refund
                          WHERE refund.payment_id = payment.id
                      ), 0)
                  )
              );

            SELECT RAISE(ABORT, 'invoice_correction_replacement_not_settled')
            WHERE NEW.status = 'COMPLETED'
              AND EXISTS (
                  SELECT 1
                  FROM payment_allocations AS reversal
                  WHERE reversal.correction_id = NEW.id
                    AND reversal.entry_type = 'REVERSAL'
              )
              AND (SELECT amount_cents FROM invoices WHERE id = NEW.replacement_invoice_id) != (
                  COALESCE((
                      SELECT SUM(CASE allocation.entry_type
                          WHEN 'APPLY' THEN allocation.amount_cents
                          ELSE -allocation.amount_cents END)
                      FROM payment_allocations AS allocation
                      WHERE allocation.invoice_id = NEW.replacement_invoice_id
                  ), 0)
                  + COALESCE((
                      SELECT SUM(adjustment.amount_cents)
                      FROM invoice_adjustments AS adjustment
                      WHERE adjustment.invoice_id = NEW.replacement_invoice_id
                  ), 0)
              );
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_invoice_correction_delete_immutable
        BEFORE DELETE ON invoice_corrections
        BEGIN
            SELECT RAISE(ABORT, 'invoice_correction_history_immutable');
        END
        """
    )

    op.execute(
        """
        CREATE TRIGGER trg_invoice_block_void_with_payment
        BEFORE UPDATE OF lifecycle_status ON invoices
        WHEN NEW.lifecycle_status = 'VOID'
          AND OLD.lifecycle_status != 'VOID'
          AND (
              EXISTS (
                  SELECT 1
                  FROM payment_allocations AS applied
                  WHERE applied.invoice_id = NEW.id
                    AND applied.entry_type = 'APPLY'
              )
              OR EXISTS (
                  SELECT 1
                  FROM invoice_corrections AS correction
                  WHERE correction.replacement_invoice_id = NEW.id
                    AND correction.status = 'OPEN'
              )
          )
        BEGIN
            SELECT RAISE(ABORT, 'invoice_open_correction_replacement_cannot_void')
            WHERE EXISTS (
                SELECT 1
                FROM invoice_corrections AS correction
                WHERE correction.replacement_invoice_id = NEW.id
                  AND correction.status = 'OPEN'
            );
            SELECT RAISE(ABORT, 'invoice_with_payment_requires_open_correction')
            WHERE NOT EXISTS (
                SELECT 1 FROM invoice_corrections AS correction
                WHERE correction.original_invoice_id = NEW.id
                  AND correction.status = 'OPEN'
            );
            SELECT RAISE(ABORT, 'invoice_payment_allocation_not_reversed')
            WHERE EXISTS (
                SELECT 1
                FROM payment_allocations AS applied
                WHERE applied.invoice_id = NEW.id
                  AND applied.entry_type = 'APPLY'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM payment_allocations AS reversal
                      JOIN invoice_corrections AS correction ON correction.id = reversal.correction_id
                      WHERE reversal.reverses_allocation_id = applied.id
                        AND reversal.entry_type = 'REVERSAL'
                        AND reversal.payment_id = applied.payment_id
                        AND reversal.invoice_id = applied.invoice_id
                        AND reversal.amount_cents = applied.amount_cents
                        AND correction.original_invoice_id = NEW.id
                        AND correction.status = 'OPEN'
                  )
            );
        END
        """
    )


def _validate_post_upgrade(settlement_count: int, payment_count: int) -> None:
    connection = op.get_bind()
    if int(connection.exec_driver_sql("SELECT COUNT(*) FROM quarterly_settlements").scalar()) != settlement_count:
        raise RuntimeError("Settlement父表重建后行数不一致，已回滚迁移")
    if int(connection.exec_driver_sql("SELECT COUNT(*) FROM payments").scalar()) != payment_count:
        raise RuntimeError("Payment重建后行数不一致，已回滚迁移")

    _require_zero_rows(
        "SELECT id FROM quarterly_settlements WHERE version_no != 1 OR replaces_settlement_id IS NOT NULL LIMIT 1",
        "存量Settlement版本回填不完整",
    )
    _require_zero_rows(
        """
        SELECT payment.id
        FROM payments AS payment
        LEFT JOIN payment_allocations AS allocation
          ON allocation.payment_id = payment.id
         AND allocation.entry_type = 'APPLY'
         AND allocation.correction_id IS NULL
        GROUP BY payment.id, payment.invoice_id, payment.amount_cents
        HAVING COUNT(allocation.id) != 1
            OR SUM(allocation.amount_cents) != payment.amount_cents
            OR MIN(allocation.invoice_id) != payment.invoice_id
        LIMIT 1
        """,
        "旧Payment的APPLY Allocation回填不完整",
    )

    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        affected = ", ".join(sorted({str(row[0]) for row in violations}))
        raise RuntimeError(f"0.2.14迁移后出现外键违规（涉及{affected}），已回滚")
    integrity = [tuple(row) for row in connection.exec_driver_sql("PRAGMA integrity_check").fetchall()]
    if integrity != [("ok",)]:
        raise RuntimeError("0.2.14迁移后SQLite完整性检查失败，已回滚")

    indexes = _index_columns("quarterly_settlements")
    active = indexes.get("uq_settlement_group_period_active")
    if (
        active is None
        or not active[0]
        or active[1] != ("client_id", "platform_id", "fee_plan_id", "year", "quarter")
        or "WHERE status != 'VOID'" not in active[2]
    ):
        raise RuntimeError("Settlement活动自然键部分唯一索引缺失")
    if not any(
        unique
        and columns == ("client_id", "platform_id", "fee_plan_id", "year", "quarter", "version_no")
        for unique, columns, _sql in indexes.values()
    ):
        raise RuntimeError("Settlement自然键加版本唯一约束缺失")

    payment_info = {
        str(row[1]): row
        for row in connection.exec_driver_sql('PRAGMA table_info("payments")').fetchall()
    }
    payment_columns = {name: bool(row[3]) for name, row in payment_info.items()}
    if (
        set(payment_columns) != TARGET_PAYMENT_COLUMNS
        or not payment_columns.get("proof_attachment_id")
        or not payment_columns.get("company_difference_cents")
    ):
        raise RuntimeError("Payment凭证或公司承担差额列未成功收紧为目标结构")
    payment_check_names = {
        str(check.get("name") or "")
        for check in sa.inspect(connection).get_check_constraints("payments")
    }
    if not {
        "ck_payment_amount_positive",
        "ck_payment_company_difference_nonnegative",
        "ck_payment_difference_reason",
    }.issubset(payment_check_names) or str(
        payment_info["company_difference_cents"][4] or ""
    ).strip("()'\" ") != "0":
        raise RuntimeError("Payment差额字段默认值或CHECK约束不完整")
    if (
        (("proof_attachment_id",), "attachments", ("id",), "RESTRICT")
        not in _foreign_keys("payments")
    ):
        raise RuntimeError("Payment凭证RESTRICT外键缺失")

    required_foreign_keys = {
        "quarterly_settlements": {
            (("previous_settlement_id",), "quarterly_settlements", ("id",), "RESTRICT"),
            (("replaces_settlement_id",), "quarterly_settlements", ("id",), "RESTRICT"),
        },
        "invoice_corrections": {
            (("original_invoice_id",), "invoices", ("id",), "RESTRICT"),
            (("replacement_invoice_id",), "invoices", ("id",), "RESTRICT"),
        },
        "payment_allocations": {
            (("payment_id",), "payments", ("id",), "RESTRICT"),
            (("invoice_id",), "invoices", ("id",), "RESTRICT"),
            (("reverses_allocation_id",), "payment_allocations", ("id",), "RESTRICT"),
            (("correction_id",), "invoice_corrections", ("id",), "RESTRICT"),
        },
        "payment_refunds": {
            (("payment_id",), "payments", ("id",), "RESTRICT"),
            (("correction_id",), "invoice_corrections", ("id",), "RESTRICT"),
            (("proof_attachment_id",), "attachments", ("id",), "RESTRICT"),
        },
        "invoice_adjustments": {
            (("invoice_id",), "invoices", ("id",), "RESTRICT"),
            (("correction_id",), "invoice_corrections", ("id",), "RESTRICT"),
            (("payment_id",), "payments", ("id",), "RESTRICT"),
        },
    }
    for table_name, required in required_foreign_keys.items():
        if not required.issubset(_foreign_keys(table_name)):
            raise RuntimeError(f"0.2.14迁移后{table_name}的RESTRICT外键结构不完整")
    if not any(
        unique and columns == ("payment_id",)
        for unique, columns, _sql in _index_columns("invoice_adjustments").values()
    ):
        raise RuntimeError("InvoiceAdjustment缺少Payment一对一唯一约束")

    expected_triggers = set(SETTLEMENT_TRIGGER_NAMES) | set(INVOICE_TRIGGER_NAMES) | NEW_TRIGGER_NAMES
    actual_triggers = _trigger_sql()
    if set(actual_triggers) != expected_triggers:
        raise RuntimeError("0.2.14迁移后Trigger清单不完整")
    required_markers = {
        "trg_settlement_validate_finalize": "settlement_container_previous_changed",
        "trg_settlement_insert_draft_only": "settlement_replacement_invalid",
        "trg_settlement_parent_financial_lock": "NEW.version_no",
        "trg_invoice_lifecycle_transition": "invoice_void_metadata_invalid",
        "trg_invoice_block_void_with_payment": "invoice_payment_allocation_not_reversed",
        "trg_payment_validate_insert": "payment_proof_invalid",
        "trg_payment_allocation_validate_insert": "ordinary_payment_must_settle_invoice",
        "trg_attachment_update_block_payment_evidence": "payment_evidence_immutable",
        "trg_invoice_correction_validate_insert": "invoice_correction_group_already_open",
        "trg_invoice_correction_validate_update": "invoice_correction_source_lineage_invalid",
        "trg_invoice_adjustment_validate_insert": "payment.company_difference_cents",
    }
    for trigger_name, marker in required_markers.items():
        if marker not in actual_triggers.get(trigger_name, ""):
            raise RuntimeError(f"0.2.14关键Trigger内容不完整：{trigger_name}")
    if "invoice_void_metadata_immutable" not in actual_triggers.get(
        "trg_invoice_lifecycle_transition", ""
    ):
        raise RuntimeError("0.2.14 Invoice作废审计Trigger内容不完整")
    if "payment_invoice_group_has_open_correction" not in actual_triggers.get(
        "trg_payment_validate_insert", ""
    ):
        raise RuntimeError("0.2.14关键Trigger内容不完整：trg_payment_validate_insert")
    payment_trigger = actual_triggers.get("trg_payment_validate_insert", "")
    if not all(
        marker in payment_trigger
        for marker in (
            "payment_invoice_ledger_not_empty",
            "payment_must_settle_invoice",
            "payment_difference_invalid",
            "payment_method_invalid",
        )
    ):
        raise RuntimeError("0.2.14付款二态Trigger内容不完整：trg_payment_validate_insert")
    if "payment_refund_method_invalid" not in actual_triggers.get(
        "trg_payment_refund_validate_insert", ""
    ):
        raise RuntimeError("0.2.14退款方式Trigger内容不完整")
    if "invoice_correction_blank_replacement_ledger_required" not in actual_triggers.get(
        "trg_invoice_correction_validate_update", ""
    ):
        raise RuntimeError("0.2.14无资金更正Trigger内容不完整")
    if "invoice_open_correction_replacement_cannot_void" not in actual_triggers.get(
        "trg_invoice_block_void_with_payment", ""
    ):
        raise RuntimeError("0.2.14替代Invoice冻结Trigger内容不完整")
    settlement_parent_trigger = actual_triggers.get(
        "trg_settlement_parent_financial_lock", ""
    )
    if not all(
        marker in settlement_parent_trigger
        for marker in (
            "settlement_replacement_identity_immutable",
            "settlement_replacement_invalid_at_finalize",
        )
    ):
        raise RuntimeError("0.2.14替代Settlement身份Trigger内容不完整")


def upgrade() -> None:
    # Refuse an FK-enabled connection before opening any transaction, then
    # acquire SQLite's single-writer lock before reading the schema, Payment
    # rows, or physical proof metadata.  This closes the TOCTOU window in
    # which an old process could otherwise change legacy Payment state after
    # preflight but before the first DDL statement.
    _assert_foreign_keys_disabled()
    op.get_bind().exec_driver_sql("BEGIN IMMEDIATE")
    (
        previous_trigger_sql,
        settlement_count,
        payment_count,
        rebuild_settlements,
        rebuild_payments,
    ) = _validate_pre_upgrade()
    _drop_triggers(SETTLEMENT_TRIGGER_NAMES)
    _drop_triggers(INVOICE_TRIGGER_NAMES)
    if rebuild_settlements:
        _rebuild_settlements()
    if rebuild_payments:
        _rebuild_payments()
    _create_ledger_tables()
    _backfill_payment_allocations()
    _restore_settlement_triggers(previous_trigger_sql)
    _restore_invoice_triggers(previous_trigger_sql)
    _create_payment_and_correction_triggers()
    _validate_post_upgrade(settlement_count, payment_count)


def downgrade() -> None:
    raise RuntimeError(
        "Settlement版本、付款分配、退款及更正账本不允许原地降级；"
        "请使用0.2.14升级前已验证的完整备份回滚"
    )
