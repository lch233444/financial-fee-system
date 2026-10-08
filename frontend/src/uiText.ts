/** Translate system terminology for display only; API values and exports stay unchanged. */
const terms: Record<string, string> = {
  "Sub Account": "子账户", SubAccount: "子账户", "Fee Plan": "收费计划", FeePlan: "收费计划",
  "Closing Snapshot": "期末结余", "Beginning Snapshot": "期初结余", BalanceSnapshot: "历史结余",
  "Closing Date": "结算日期", "Starting Date": "开始日期", "Original HWM": "期初高水位线", "Next HWM": "下期高水位线",
  "Service Fee": "服务费", QuarterlySettlement: "季度结算", Settlement: "结算", Invoice: "账单", Client: "客户",
  Company: "收款公司", Platform: "投资平台", Scheme: "平台计划", Trustee: "受托机构", Snapshot: "历史结余",
  Code: "编码", Name: "名称", Sol: "智能辅助", OCR: "文字识别",
  Finalized: "已锁定", Finalize: "锁定", Draft: "草稿", Void: "已作废", Payment: "付款记录", Correction: "更正",
  FC: "中介人", HWM: "高水位线", DRAFT: "草稿", FINALIZED: "已锁定", VOID: "已作废", ISSUED: "已出具", ISSUING: "出具中",
  ACTIVE: "已启用", CLOSED: "已结束", OPEN: "处理中", COMPLETED: "已完成", HKD: "港币",
};
const termPattern = new RegExp(`(?<![A-Za-z_])(${Object.keys(terms).sort((a, b) => b.length - a.length).join("|")})(?![A-Za-z_])`, "g");
export function systemMessage(text: string) { return text.replace(termPattern, (term) => terms[term]); }

const validationFields: Record<string, string> = {
  name: "名称", code: "编码", client_id: "客户", account_id: "账户", platform_id: "投资平台", fee_plan_id: "收费计划", fc_id: "中介人", company_id: "收款公司", payee_company_id: "收款公司",
  account_number: "账户号码", scheme_name: "平台计划名称", start_date: "开始日期", end_date: "结束日期", management_start_date: "开始管理日期", as_of_date: "结余日期", total_balance: "总余额",
  issue_date: "出具日期", due_date: "到期日期", payment_date: "收款日期", amount: "金额", reason: "原因", remark: "备注", year: "年份", quarter: "季度", account_lines: "账户明细", original_hwm: "期初高水位线",
};
export function validationMessage(message: string, location?: Array<string | number>) {
  const detail = systemMessage(message)
    .replace(/^Field required$/, "此项必填")
    .replace(/^Value error, /, "填写有误：")
    .replace(/^Input should be a valid (?:number|integer).*$/, "请填写有效数字")
    .replace(/^Input should be a valid date.*$/, "请填写有效日期")
    .replace(/^String should have at least (\d+) characters$/, "至少填写$1个字符")
    .replace(/^String should have at most (\d+) characters$/, "最多填写$1个字符")
    .replace(/^Input should be greater than or equal to (.+)$/, "不得小于$1")
    .replace(/^Input should be less than or equal to (.+)$/, "不得大于$1");
  const field = location?.filter((part) => !["body", "query", "path"].includes(String(part))).map((part) => typeof part === "number" ? `第${part + 1}项` : validationFields[part] || part).join("／");
  return field ? `${field}：${detail}` : detail;
}

const paymentMethods: Record<string, string> = { BANK_TRANSFER: "银行转账", CHEQUE: "支票", CASH: "现金", OTHER: "其他", "Bank Transfer": "银行转账", Cheque: "支票", Other: "其他" };
export function paymentMethodLabel(value: string) { return paymentMethods[value] || value; }
