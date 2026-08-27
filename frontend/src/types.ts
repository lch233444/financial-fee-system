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
export type StatementImport = {
  id: number;
  original_name: string;
  status: string;
  extracted: Record<string, unknown>;
  confidence: Record<string, number>;
  warnings: string[];
  confirmed_account_id?: number;
  ai_recognition?: AiStatementRecognition | null;
  ai_status?: string | null;
  ai_model?: string | null;
  ai_recognized_at?: string | null;
};

export const LUNA_MODEL_ID = "gpt-5.6-luna" as const;

export type AiAssistantStatus = {
  available: boolean;
  authenticated: boolean;
  status: "ready" | "signed_out" | "auth_pending" | "unavailable" | "error" | string;
  model: typeof LUNA_MODEL_ID | string;
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
  model: typeof LUNA_MODEL_ID | string;
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
  status: "DRAFT" | "FINALIZED" | "VOID";
  account_lines: Array<{
    account_id: number;
    account_number: string;
    beginning: string;
    closing: string;
    closing_snapshot_id: number | null;
  }>;
};
export type Invoice = {
  id: number;
  settlement_id: number;
  invoice_number: string | null;
  lifecycle_status: "DRAFT" | "ISSUING" | "ISSUED" | "VOID";
  payment_status: "UNPAID" | "PARTIALLY_PAID" | "PAID" | "OVERDUE";
  issue_date: string | null;
  due_date: string | null;
  amount: string;
  paid_amount: string;
  outstanding_amount: string;
  language: string;
  void_reason: string | null;
  company_name: string;
  fc_name: string;
  client_name: string;
};
