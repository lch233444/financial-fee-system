from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


CENT = Decimal("0.01")
PERCENT_BASIS_POINT = Decimal("0.01")


def _require_cent_precision(value: Decimal) -> Decimal:
    """Reject values that would silently change when stored as integer cents."""

    try:
        rounded = value.quantize(CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("金额格式无效") from exc
    if value != rounded:
        raise ValueError("金额最多只能有两位小数")
    return rounded


def _require_basis_point_precision(value: Decimal) -> Decimal:
    """A percentage stored in bps must be exact to 0.01 percentage point."""

    try:
        rounded = value.quantize(PERCENT_BASIS_POINT, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("Fee Rate格式无效") from exc
    if value != rounded:
        raise ValueError("Fee Rate百分比最多只能有两位小数")
    return rounded


def _require_meaningful_reason(value: str) -> str:
    reason = value.strip()
    if len(reason) < 2:
        raise ValueError("原因去除首尾空格后至少需要2个字符")
    return reason


def _require_meaningful_payment_method(value: str) -> str:
    method = value.strip()
    if not method:
        raise ValueError("付款或退款方式不能为空")
    return method


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    address: str | None = None
    contact: str | None = None
    bank_information: str | None = None
    cheque_information: str | None = None
    payment_terms_days: int = Field(default=14, ge=0, le=365)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


class FCCreate(BaseModel):
    company_id: int | None = Field(default=None, json_schema_extra={"deprecated": True}, description="旧调用兼容字段，不参与业务归属校验")
    name: str = Field(min_length=1, max_length=160)
    remark: str | None = None


class PlatformCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=40)
    trustee: str | None = None
    remark: str | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


class FeePlanCreate(BaseModel):
    company_id: int | None = Field(default=None, json_schema_extra={"deprecated": True}, description="旧调用兼容字段，不参与业务归属校验")
    name: str = Field(min_length=1, max_length=200)
    fee_rate_percent: Decimal = Field(default=Decimal("20"), ge=0, le=100)
    calculation_method: str = "HIGH_WATER_MARK"

    @field_validator("fee_rate_percent")
    @classmethod
    def validate_fee_rate_precision(cls, value: Decimal) -> Decimal:
        return _require_basis_point_precision(value)


class ClientCreate(BaseModel):
    company_id: int | None = None
    fc_id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    contact: str | None = None
    management_start_date: date | None = None
    remark: str | None = None
    status: Literal["DRAFT", "ACTIVE", "CLOSED"] = "DRAFT"


class ClientUpdate(BaseModel):
    company_id: int | None = None
    fc_id: int | None = None
    name: str | None = None
    contact: str | None = None
    management_start_date: date | None = None
    remark: str | None = None
    status: Literal["DRAFT", "ACTIVE", "CLOSED"] | None = None


class AccountCreate(BaseModel):
    client_id: int
    platform_id: int | None = None
    fee_plan_id: int | None = None
    account_number: str = Field(min_length=1, max_length=100)
    scheme_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["DRAFT", "ACTIVE", "CLOSED"] = "DRAFT"
    remark: str | None = None


class ClientMergeRequest(BaseModel):
    target_client_id: int = Field(gt=0)
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)


class AccountUpdate(BaseModel):
    platform_id: int | None = None
    fee_plan_id: int | None = None
    scheme_name: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    status: Literal["DRAFT", "ACTIVE", "CLOSED"] | None = None
    remark: str | None = None


class TransactionCreate(BaseModel):
    account_id: int
    transaction_date: date
    transaction_type: Literal["CONTRIBUTION", "MONTHLY_CONTRIBUTION", "WITHDRAWAL"]
    amount: Decimal = Field(gt=0)
    remark: str | None = None
    attachment_ids: list[int] = Field(min_length=1, max_length=50)

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)


class TransactionUpdate(BaseModel):
    transaction_date: date
    transaction_type: Literal["CONTRIBUTION", "MONTHLY_CONTRIBUTION", "WITHDRAWAL"]
    amount: Decimal = Field(gt=0)
    remark: str | None = None
    attachment_ids: list[int] | None = Field(default=None, min_length=1, max_length=50)
    correction_reason: str = Field(min_length=2, max_length=500)

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)

    @field_validator("correction_reason")
    @classmethod
    def validate_correction_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)


class BalanceSnapshotCreate(BaseModel):
    account_id: int
    as_of_date: date
    total_balance: Decimal = Field(ge=0)
    attachment_ids: list[int] = Field(min_length=1, max_length=50)
    remark: str | None = None

    @field_validator("total_balance")
    @classmethod
    def validate_balance_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)


class SettlementAccountInput(BaseModel):
    account_id: int
    start_date: date | None = None
    closing_date: date | None = None
    beginning_snapshot_id: int | None = None
    closing_snapshot_id: int
    original_hwm: Decimal | None = Field(default=None, ge=0)
    hwm_override_reason: str | None = Field(default=None, min_length=2, max_length=500)
    hwm_override_confirmed: StrictBool = False
    remark: str | None = None

    @field_validator("hwm_override_reason")
    @classmethod
    def validate_hwm_reason(cls, value: str | None) -> str | None:
        return _require_meaningful_reason(value) if value is not None else None

    @field_validator("original_hwm")
    @classmethod
    def validate_amount_precision(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _require_cent_precision(value)


class SettlementCalculateRequest(BaseModel):
    client_id: int
    platform_id: int
    fee_plan_id: int
    year: int = Field(ge=2000, le=2200)
    quarter: int = Field(ge=1, le=4)
    start_date: date | None = None
    closing_date: date | None = None
    account_lines: list[SettlementAccountInput] = Field(min_length=1)


class InvoiceDraftCreate(BaseModel):
    payee_company_id: int | None = Field(default=None, gt=0)
    client_id: int
    year: int = Field(ge=2000, le=2200)
    quarter: int = Field(ge=1, le=4)
    # Legacy callers may send this field; it never filters the client-quarter bill.
    fee_plan_id: int | None = Field(default=None, deprecated=True)
    language: Literal["zh", "en"] = "zh"


class InvoiceIssueRequest(BaseModel):
    issue_date: date | None = None
    due_date: date | None = None
    language: Literal["zh", "en"] = "zh"


class InvoiceIssueRecoveryRequest(BaseModel):
    action: Literal["COMPLETE", "RETURN_TO_DRAFT"]


class VoidRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)


class PaymentCreate(BaseModel):
    payment_date: date
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=80)
    proof_attachment_id: int = Field(gt=0)
    company_difference: Decimal = Field(default=Decimal("0"), ge=0)
    difference_reason: str | None = Field(default=None, max_length=500)
    remark: str | None = None

    @field_validator("amount", "company_difference")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        return _require_meaningful_payment_method(value)

    @model_validator(mode="after")
    def validate_difference_reason(self) -> PaymentCreate:
        reason = self.difference_reason.strip() if self.difference_reason else None
        if self.company_difference > 0 and not reason:
            raise ValueError("公司承担差额时必须填写原因")
        if self.company_difference == 0 and reason:
            raise ValueError("公司承担差额为0时不能填写差额原因")
        self.difference_reason = reason
        return self


class InvoiceCorrectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=2, max_length=500)
    target_company_id: int | None = Field(default=None, gt=0)
    recalculate_settlements: StrictBool | None = None

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)


class InvoiceCorrectionUpdate(InvoiceCorrectionCreate):
    recalculate_settlements: StrictBool
    expected_revision: int = Field(gt=0)


class RetainedPaymentAllocation(BaseModel):
    payment_id: int = Field(gt=0)
    amount: Decimal = Field(ge=0)

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)


class PaymentRefundInput(BaseModel):
    payment_id: int = Field(gt=0)
    refund_date: date
    amount: Decimal = Field(gt=0)
    method: str = Field(min_length=1, max_length=80)
    proof_attachment_id: int = Field(gt=0)
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        return _require_meaningful_payment_method(value)


class InvoiceCorrectionComplete(BaseModel):
    replacement_invoice_id: int = Field(gt=0)
    retained_allocations: list[RetainedPaymentAllocation] = Field(default_factory=list)
    refunds: list[PaymentRefundInput] = Field(default_factory=list)
    company_difference: Decimal = Field(default=Decimal("0"), ge=0)
    difference_reason: str | None = Field(default=None, max_length=500)

    @field_validator("company_difference")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)

    @model_validator(mode="after")
    def validate_difference_reason(self) -> InvoiceCorrectionComplete:
        reason = self.difference_reason.strip() if self.difference_reason else None
        if self.company_difference > 0 and not reason:
            raise ValueError("公司承担差额时必须填写原因")
        if self.company_difference == 0 and reason:
            raise ValueError("公司承担差额为0时不能填写差额原因")
        self.difference_reason = reason
        return self


class StatementDeleteRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)


class StatementConfirmRequest(BaseModel):
    client_name: str = Field(min_length=1, max_length=200)
    client_id: int | None = Field(default=None, gt=0)
    account_number: str = Field(min_length=1)
    scheme_name: str | None = None
    trustee: str | None = None
    as_of_date: date
    total_balance: Decimal = Field(ge=0)
    account_id: int | None = None
    account_platform_id: int | None = None
    # Required only when a stored AI comparison contains a conflict,
    # uncertainty, failed validation, or uncorroborated value.
    ai_conflicts_reviewed: bool | None = None
    # A local UNKNOWN classification may only be replaced by the fixed AI
    # model's balance-page classification after a separate, explicit review.
    # The legacy field name remains for saved-client and audit compatibility.
    luna_document_type_reviewed: bool | None = None

    @field_validator("client_name")
    @classmethod
    def validate_client_name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Client Name不能为空")
        return value

    @field_validator("total_balance")
    @classmethod
    def validate_balance_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)


class BackupRestoreResult(BaseModel):
    restored: bool
    details: dict[str, Any]
