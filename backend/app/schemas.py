from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    company_id: int
    name: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    remark: str | None = None

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()


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
    company_id: int
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=40)
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
    transaction_type: Literal["CONTRIBUTION", "WITHDRAWAL"]
    amount: Decimal = Field(gt=0)
    remark: str | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)


class TransactionUpdate(BaseModel):
    transaction_date: date
    transaction_type: Literal["CONTRIBUTION", "WITHDRAWAL"]
    amount: Decimal = Field(gt=0)
    remark: str | None = None
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
    eligible_for_closing: bool = False
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
    remark: str | None = None

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
    client_id: int
    year: int = Field(ge=2000, le=2200)
    quarter: int = Field(ge=1, le=4)
    fee_plan_id: int
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
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)


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


class StatementHoldingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_name: str | None = Field(default=None, max_length=300)
    market_value: str | None = None
    investment_gain_loss: str | None = None
    portfolio_percent: str | None = None
    units: str | None = None
    unit_price: str | None = None
    mandatory_contributions: str | None = None
    voluntary_contributions: str | None = None
    balance_as_of: str | None = None

    @field_validator(
        "market_value",
        "investment_gain_loss",
        "mandatory_contributions",
        "voluntary_contributions",
    )
    @classmethod
    def validate_money_string(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"-?\d+\.\d{2}", value):
            raise ValueError("金额必须是不含逗号和货币符号的两位小数字符串")
        return value

    @field_validator("units", "unit_price")
    @classmethod
    def validate_decimal_string(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"\d+(?:\.\d{1,8})?", value):
            raise ValueError("单位数及单位价格必须是最多八位小数的非负数字符串")
        return value

    @field_validator("portfolio_percent")
    @classmethod
    def validate_percent_string(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.removesuffix("%").strip()
        try:
            number = Decimal(normalized)
        except InvalidOperation as exc:
            raise ValueError("持仓比例必须是数字字符串") from exc
        if number < 0 or number > 100:
            raise ValueError("持仓比例必须介于0至100")
        return value

    @field_validator("balance_as_of")
    @classmethod
    def validate_balance_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("持仓日期必须使用YYYY-MM-DD") from exc
        return value


class StatementDeleteRequest(BaseModel):
    reason: str = Field(min_length=2, max_length=500)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _require_meaningful_reason(value)


class StatementConfirmRequest(BaseModel):
    client_name: str = Field(min_length=1)
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
    holdings: list[StatementHoldingInput] | None = Field(default=None, max_length=200)

    @field_validator("total_balance")
    @classmethod
    def validate_balance_precision(cls, value: Decimal) -> Decimal:
        return _require_cent_precision(value)


class BackupRestoreResult(BaseModel):
    restored: bool
    details: dict[str, Any]
