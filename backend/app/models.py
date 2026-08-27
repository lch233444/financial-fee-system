from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    address: Mapped[str | None] = mapped_column(Text)
    contact: Mapped[str | None] = mapped_column(String(200))
    bank_information: Mapped[str | None] = mapped_column(Text)
    cheque_information: Mapped[str | None] = mapped_column(Text)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=14)
    logo_path: Mapped[str | None] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    fcs: Mapped[list[FC]] = relationship(back_populates="company")
    clients: Mapped[list[Client]] = relationship(back_populates="company")
    fee_plans: Mapped[list[FeePlan]] = relationship(back_populates="company")


class FC(TimestampMixin, Base):
    __tablename__ = "fcs"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_fc_company_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    code: Mapped[str] = mapped_column(String(20))
    remark: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(back_populates="fcs")
    clients: Mapped[list[Client]] = relationship(back_populates="fc")


class Platform(TimestampMixin, Base):
    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    trustee: Mapped[str | None] = mapped_column(String(250))
    remark: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FeePlan(TimestampMixin, Base):
    __tablename__ = "fee_plans"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_fee_plan_company_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(40))
    fee_rate_bps: Mapped[int] = mapped_column(Integer, default=2000)
    calculation_method: Mapped[str] = mapped_column(String(80), default="HIGH_WATER_MARK")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(back_populates="fee_plans")


class Client(TimestampMixin, Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    fc_id: Mapped[int | None] = mapped_column(ForeignKey("fcs.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    contact: Mapped[str | None] = mapped_column(String(250))
    management_start_date: Mapped[date | None] = mapped_column(Date)
    remark: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)

    company: Mapped[Company | None] = relationship(back_populates="clients")
    fc: Mapped[FC | None] = relationship(back_populates="clients")
    accounts: Mapped[list[SubAccount]] = relationship(back_populates="client")


class SubAccount(TimestampMixin, Base):
    __tablename__ = "sub_accounts"
    __table_args__ = (UniqueConstraint("platform_id", "account_number", name="uq_platform_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    platform_id: Mapped[int | None] = mapped_column(ForeignKey("platforms.id", ondelete="RESTRICT"), index=True)
    fee_plan_id: Mapped[int | None] = mapped_column(ForeignKey("fee_plans.id", ondelete="RESTRICT"), index=True)
    account_number: Mapped[str] = mapped_column(String(100), index=True)
    scheme_name: Mapped[str | None] = mapped_column(String(250))
    currency: Mapped[str] = mapped_column(String(3), default="HKD")
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    remark: Mapped[str | None] = mapped_column(Text)

    client: Mapped[Client] = relationship(back_populates="accounts")
    platform: Mapped[Platform | None] = relationship()
    fee_plan: Mapped[FeePlan | None] = relationship()


class TransactionRecord(TimestampMixin, Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sub_accounts.id", ondelete="CASCADE"), index=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    transaction_type: Mapped[str] = mapped_column(String(20), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)
    remark: Mapped[str | None] = mapped_column(Text)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL"))

    account: Mapped[SubAccount] = relationship()


class BalanceSnapshot(TimestampMixin, Base):
    __tablename__ = "balance_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "as_of_date", name="uq_account_snapshot_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("sub_accounts.id", ondelete="CASCADE"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    total_balance_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="HKD")
    source_type: Mapped[str] = mapped_column(String(30), default="MANUAL")
    statement_import_id: Mapped[int | None] = mapped_column(
        ForeignKey("statement_imports.id", ondelete="SET NULL"), index=True
    )
    holdings_json: Mapped[list | None] = mapped_column(JSON)
    eligible_for_closing: Mapped[bool] = mapped_column(Boolean, default=False)
    remark: Mapped[str | None] = mapped_column(Text)

    account: Mapped[SubAccount] = relationship()


class QuarterlySettlement(TimestampMixin, Base):
    __tablename__ = "quarterly_settlements"
    __table_args__ = (
        UniqueConstraint(
            "client_id", "platform_id", "fee_plan_id", "year", "quarter", name="uq_settlement_group_period"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platforms.id", ondelete="RESTRICT"), index=True)
    fee_plan_id: Mapped[int] = mapped_column(ForeignKey("fee_plans.id", ondelete="RESTRICT"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    fc_id: Mapped[int | None] = mapped_column(ForeignKey("fcs.id", ondelete="RESTRICT"), index=True)
    previous_settlement_id: Mapped[int | None] = mapped_column(
        ForeignKey("quarterly_settlements.id", ondelete="RESTRICT"), index=True
    )
    year: Mapped[int] = mapped_column(Integer, index=True)
    quarter: Mapped[int] = mapped_column(Integer, index=True)
    start_date: Mapped[date] = mapped_column(Date)
    closing_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)
    beginning_cents: Mapped[int] = mapped_column(Integer)
    contribution_cents: Mapped[int] = mapped_column(Integer)
    withdrawal_cents: Mapped[int] = mapped_column(Integer)
    net_contribution_cents: Mapped[int] = mapped_column(Integer)
    closing_cents: Mapped[int] = mapped_column(Integer)
    gain_loss_cents: Mapped[int] = mapped_column(Integer)
    period_rate_ppm: Mapped[int | None] = mapped_column(Integer)
    original_hwm_cents: Mapped[int] = mapped_column(Integer)
    adjusted_hwm_cents: Mapped[int] = mapped_column(Integer)
    watermark_difference_cents: Mapped[int] = mapped_column(Integer)
    chargeable_above_hwm_cents: Mapped[int] = mapped_column(Integer)
    service_fee_cents: Mapped[int] = mapped_column(Integer)
    next_hwm_cents: Mapped[int] = mapped_column(Integer)
    fee_rate_bps: Mapped[int] = mapped_column(Integer)
    formula_version: Mapped[str] = mapped_column(String(30), default="HWM-1.0")
    calculation_mode: Mapped[str] = mapped_column(String(30), default="ACCOUNT_HWM", index=True)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", index=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)

    client: Mapped[Client] = relationship()
    platform: Mapped[Platform] = relationship()
    fee_plan: Mapped[FeePlan] = relationship()
    company: Mapped[Company | None] = relationship(foreign_keys=[company_id])
    fc: Mapped[FC | None] = relationship(foreign_keys=[fc_id])
    previous_settlement: Mapped[QuarterlySettlement | None] = relationship(
        remote_side="QuarterlySettlement.id", foreign_keys=[previous_settlement_id]
    )
    account_lines: Mapped[list[SettlementAccountLine]] = relationship(
        back_populates="settlement", cascade="all, delete-orphan"
    )


class SettlementAccountLine(TimestampMixin, Base):
    __tablename__ = "settlement_account_lines"
    __table_args__ = (UniqueConstraint("settlement_id", "account_id", name="uq_settlement_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_id: Mapped[int] = mapped_column(
        ForeignKey("quarterly_settlements.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("sub_accounts.id", ondelete="RESTRICT"), index=True)
    previous_line_id: Mapped[int | None] = mapped_column(
        ForeignKey("settlement_account_lines.id", ondelete="RESTRICT"), index=True
    )
    beginning_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("balance_snapshots.id", ondelete="RESTRICT"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    closing_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)
    beginning_cents: Mapped[int] = mapped_column(Integer)
    closing_cents: Mapped[int] = mapped_column(Integer)
    closing_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("balance_snapshots.id", ondelete="RESTRICT"), index=True
    )
    contribution_cents: Mapped[int | None] = mapped_column(Integer)
    withdrawal_cents: Mapped[int | None] = mapped_column(Integer)
    net_contribution_cents: Mapped[int | None] = mapped_column(Integer)
    gain_loss_cents: Mapped[int | None] = mapped_column(Integer)
    period_rate_ppm: Mapped[int | None] = mapped_column(Integer)
    original_hwm_cents: Mapped[int | None] = mapped_column(Integer)
    adjusted_hwm_cents: Mapped[int | None] = mapped_column(Integer)
    watermark_difference_cents: Mapped[int | None] = mapped_column(Integer)
    chargeable_above_hwm_cents: Mapped[int | None] = mapped_column(Integer)
    service_fee_cents: Mapped[int | None] = mapped_column(Integer)
    next_hwm_cents: Mapped[int | None] = mapped_column(Integer)
    fee_rate_bps: Mapped[int | None] = mapped_column(Integer)
    formula_version: Mapped[str | None] = mapped_column(String(30))
    remark: Mapped[str | None] = mapped_column(Text)

    settlement: Mapped[QuarterlySettlement] = relationship(back_populates="account_lines")
    account: Mapped[SubAccount] = relationship()


class InvoiceSequence(Base):
    __tablename__ = "invoice_sequences"
    __table_args__ = (UniqueConstraint("company_id", "fc_id", name="uq_invoice_sequence_company_fc"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    fc_id: Mapped[int] = mapped_column(ForeignKey("fcs.id", ondelete="RESTRICT"))
    last_number: Mapped[int] = mapped_column(Integer, default=0)


class Invoice(TimestampMixin, Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_id: Mapped[int] = mapped_column(
        ForeignKey("quarterly_settlements.id", ondelete="RESTRICT"), index=True
    )
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    fc_id: Mapped[int] = mapped_column(ForeignKey("fcs.id", ondelete="RESTRICT"), index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(10), default="zh")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    pdf_paths_json: Mapped[dict | None] = mapped_column(JSON)

    settlement: Mapped[QuarterlySettlement] = relationship()
    company: Mapped[Company] = relationship()
    fc: Mapped[FC] = relationship()
    payments: Mapped[list[Payment]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    payment_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(80))
    proof_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id", ondelete="SET NULL"))
    remark: Mapped[str | None] = mapped_column(Text)

    invoice: Mapped[Invoice] = relationship(back_populates="payments")


class Attachment(TimestampMixin, Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(600), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)


class StatementImport(TimestampMixin, Base):
    __tablename__ = "statement_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mime_type: Mapped[str] = mapped_column(String(120))
    parser_name: Mapped[str] = mapped_column(String(80), default="EMPF_ACCOUNT_PAGE")
    parser_version: Mapped[str] = mapped_column(String(30), default="1.1")
    status: Mapped[str] = mapped_column(String(30), default="NEEDS_REVIEW", index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    extracted_json: Mapped[dict | None] = mapped_column(JSON)
    reviewed_json: Mapped[dict | None] = mapped_column(JSON)
    revision_log_json: Mapped[list | None] = mapped_column(JSON)
    confidence_json: Mapped[dict | None] = mapped_column(JSON)
    warnings_json: Mapped[list | None] = mapped_column(JSON)
    # AI recognition is deliberately stored separately from OCR extraction and
    # human-reviewed values.  It is only a review aid and is never a posting.
    ai_recognition_json: Mapped[dict | None] = mapped_column(JSON)
    ai_status: Mapped[str | None] = mapped_column(String(30), index=True)
    ai_model: Mapped[str | None] = mapped_column(String(80))
    ai_recognized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duplicate_of_id: Mapped[int | None] = mapped_column(Integer)
    confirmed_account_id: Mapped[int | None] = mapped_column(ForeignKey("sub_accounts.id", ondelete="SET NULL"))
    # Kept as an application-level reference to avoid a circular DDL
    # dependency with BalanceSnapshot.statement_import_id.
    confirmed_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExportRecord(TimestampMixin, Base):
    __tablename__ = "export_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    export_type: Mapped[str] = mapped_column(String(30), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    stored_path: Mapped[str] = mapped_column(String(600))
    sha256: Mapped[str] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(10))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    details_json: Mapped[dict | None] = mapped_column(JSON)


class AppSetting(TimestampMixin, Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
