export type Company = {
  id: number;
  name: string;
  code: string;
  address: string | null;
  contact: string | null;
  bank_information: string | null;
  payment_terms_days: number;
};

export type FC = { id: number; company_id: number; company_name: string; name: string; code: string };
export type Platform = { id: number; name: string; code: string; trustee: string | null };
export type MasterStatus = "DRAFT" | "ACTIVE" | "CLOSED";
export type FeePlan = {
  id: number;
  company_id: number;
  company_name: string;
  name: string;
  code: string;
  fee_rate_percent: number;
};
export type Client = {
  id: number;
  company_id: number | null;
  company_name: string | null;
  fc_id: number | null;
  fc_name: string | null;
  name: string;
  contact: string | null;
  management_start_date: string | null;
  remark: string | null;
  status: MasterStatus;
};
export type Account = {
  id: number;
  client_id: number;
  client_name: string;
  platform_id: number | null;
  platform_name: string | null;
  fee_plan_id: number | null;
  fee_plan_name: string | null;
  account_number: string;
  scheme_name: string | null;
  start_date: string | null;
  end_date: string | null;
  remark: string | null;
  status: MasterStatus;
};
export function accountIdentityLabel(account: Account) {
  return [
    account.client_name,
    account.platform_name || "待确认Platform",
    account.account_number,
    account.scheme_name?.trim() || null,
    account.fee_plan_name ? `收费计划 ${account.fee_plan_name}` : "未分配收费计划",
  ].filter(Boolean).join(" · ");
}
export type BalanceSnapshot = {
  id: number;
  account_id: number;
  account_number: string;
  client_id: number;
  client_name: string;
  platform_id: number | null;
  platform_name: string | null;
  fee_plan_id: number | null;
  fee_plan_name: string | null;
  scheme_name: string | null;
  as_of_date: string;
  total_balance: string;
  currency: string;
  source_type: string;
  statement_import_id: number | null;
  holdings: Array<Record<string, unknown>>;
  eligible_for_closing: boolean;
  remark: string | null;
  evidence_complete: boolean;
  evidence_count?: number;
};
export type StatementImport = {
  id: number;
  original_name: string;
  mime_type?: string;
  status: string;
  extracted: Record<string, unknown>;
  reviewed?: Record<string, unknown> | null;
  confidence: Record<string, number>;
  warnings: string[];
  confirmed_account_id?: number | null;
  confirmed_snapshot_id?: number | null;
  ai_recognition?: AiStatementRecognition | null;
  ai_status?: string | null;
  ai_model?: string | null;
  ai_recognized_at?: string | null;
};

export const SOL_MODEL_ID = "gpt-5.6-sol" as const;

export type AiAssistantStatus = {
  available: boolean;
  authenticated: boolean;
  status: "ready" | "signed_out" | "auth_pending" | "unavailable" | "error" | string;
  model: typeof SOL_MODEL_ID | string;
  plan_type?: string | null;
  model_available?: boolean;
  message?: string | null;
  auth_url?: string | null;
  login_url?: string | null;
  verification_url?: string | null;
  user_code?: string | null;
};

export type AiFieldComparison = {
  field: string;
  ocr_value: unknown;
  ai_value: unknown;
};

export type AiValidationCheck = {
  check: string;
  status: "PASSED" | "FAILED" | string;
  difference?: string | null;
};

export type AiStatementRecognition = {
  status: "AGREED" | "CONFLICT" | "INCOMPLETE" | string;
  model: typeof SOL_MODEL_ID | string;
  parser_version?: string;
  values: Record<string, unknown>;
  extracted?: Record<string, unknown>;
  confidence?: Record<string, number>;
  agreements?: string[];
  conflicts?: AiFieldComparison[];
  uncorroborated?: AiFieldComparison[];
  missing_critical_fields?: string[];
  uncorroborated_critical_fields?: string[];
  uncertain_critical_fields?: string[];
  automatic_prefill_allowed?: boolean;
  conflict_requires_human_review?: boolean;
  recognition_requires_human_review?: boolean;
  requires_financial_confirmation?: boolean;
  validation_checks?: AiValidationCheck[];
  validation_failures?: string[];
  warnings?: string[];
  error?: string | null;
  recognized_at?: string | null;
};
export type Settlement = {
  id: number;
  client_id: number;
  client_name: string;
  company_id: number | null;
  company_name: string | null;
  fc_id: number | null;
  fc_name: string | null;
  previous_settlement_id: number | null;
  version_no: number;
  replaces_settlement_id: number | null;
  platform_id: number;
  platform_name: string;
  fee_plan_id: number;
  fee_plan_name: string;
  year: number;
  quarter: number;
  start_date: string;
  closing_date: string;
  days: number;
  beginning: string;
  contribution: string;
  withdrawal: string;
  net_contribution: string;
  closing: string;
  gain_loss: string;
  period_rate: number | null;
  original_hwm: string;
  adjusted_hwm: string;
  watermark_difference: string;
  chargeable_above_hwm: string;
  service_fee: string;
  next_hwm: string;
  fee_rate: number;
  formula_version: string;
  calculation_mode: "ACCOUNT_HWM" | "LEGACY_GROUP_HWM";
  status: "DRAFT" | "FINALIZED" | "VOID";
  void_reason: string | null;
  account_lines: Array<{
    id: number;
    account_id: number;
    account_number: string;
    scheme_name: string | null;
    previous_line_id: number | null;
    start_date: string;
    closing_date: string;
    days: number;
    beginning_snapshot_id: number | null;
    beginning: string;
    contribution: string | null;
    withdrawal: string | null;
    net_contribution: string | null;
    closing: string;
    gain_loss: string | null;
    period_rate: number | null;
    original_hwm: string | null;
    adjusted_hwm: string | null;
    watermark_difference: string | null;
    chargeable_above_hwm: string | null;
    service_fee: string | null;
    next_hwm: string | null;
    closing_snapshot_id: number | null;
    beginning_evidence_count: number | null;
    closing_evidence_count: number | null;
  }>;
};
export type Invoice = {
  payee_company_id: number;
  can_correct_company: boolean;
  id: number;
  settlement_id: number;
  settlement_ids: number[];
  client_id: number;
  year: number;
  quarter: number;
  fee_plan_id: number;
  fee_plan_name: string | null;
  fee_plan_ids?: number[];
  source_count: number;
  account_lines: Array<{
    id: number;
    settlement_id: number;
    fee_plan_id?: number;
    fee_plan_name?: string;
    fee_rate_percent?: number;
    platform_name: string;
    account_number: string;
    scheme_name: string | null;
    start_date: string | null;
    closing_date: string | null;
    service_fee: string;
  }>;
  issue_recovery: {
    files_complete: boolean;
    can_complete: boolean;
    can_return_to_draft: boolean;
  } | null;
  invoice_number: string | null;
  lifecycle_status: "DRAFT" | "ISSUING" | "ISSUED" | "VOID";
  payment_status: "UNPAID" | "PAID";
  is_overdue: boolean;
  issue_date: string | null;
  due_date: string | null;
  amount: string;
  paid_amount: string;
  adjustment_amount: string;
  outstanding_amount: string;
  language: string;
  void_reason: string | null;
  company_name: string | null;
  fc_name: string | null;
  client_name: string | null;
  payments: PaymentRecord[];
};

export type PaymentRecord = {
  id: number;
  payment_date: string;
  amount: string;
  method: string;
  proof_attachment_id: number;
  remark?: string | null;
};

export type InvoiceCorrection = {
  target_company_id: number | null;
  target_company_name: string | null;
  original_company_name: string;
  id: number;
  status: "OPEN" | "COMPLETED";
  reason: string;
  opened_at: string;
  completed_at: string | null;
  original_invoice: {
    id: number;
    invoice_number: string | null;
    lifecycle_status: Invoice["lifecycle_status"];
    amount: string;
  };
  replacement_invoice: {
    id: number;
    invoice_number: string | null;
    lifecycle_status: Invoice["lifecycle_status"];
    amount: string;
  } | null;
  payments: PaymentRecord[];
  allocations: Array<{
    id: number;
    payment_id: number;
    invoice_id: number;
    amount: string;
    entry_type: "APPLY" | "REVERSAL";
    reverses_allocation_id: number | null;
  }>;
  refunds: Array<{
    id: number;
    payment_id: number;
    refund_date: string;
    amount: string;
    method: string;
    reason: string;
    proof_attachment_id: number;
  }>;
  adjustments: Array<{
    id: number;
    invoice_id: number;
    adjustment_type: "COMPANY_BORNE_DIFFERENCE";
    amount: string;
    reason: string;
  }>;
};
