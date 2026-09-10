import SearchableSelect, { matchesSearch } from "../SearchableSelect";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  BrainCircuit,
  CheckCircle2,
  FileSearch,
  RefreshCw,
  Sparkles,
  Trash2,
  TriangleAlert,
  UploadCloud,
} from "lucide-react";
import {
  api,
  getAiAssistantStatus,
  postJson,
  recognizeStatementWithAi,
} from "../api";
import { EmptyState, ErrorBanner, Field, Loading, PageHeader, Panel, StatusBadge, Pagination, SectionNav } from "../components";
import { useApiList, usePagination } from "../hooks";
import type { Account, AiAssistantStatus, BalanceSnapshot, StatementImport } from "../types";
import { accountIdentityLabel, SOL_MODEL_ID } from "../types";

type Holding = {
  fund_name?: string;
  market_value?: string;
  investment_gain_loss?: string;
  portfolio_percent?: string;
  units?: string;
  unit_price?: string;
};

type HoldingsSource = "ocr" | "sol";

type ReviewKey = "client_name" | "account_number" | "scheme_name" | "trustee" | "as_of_date" | "total_balance";

type ComparisonField = {
  key: string;
  label: string;
  editable?: ReviewKey;
};

type ReviewIssue = {
  id: string;
  field: string;
  label: string;
  reasons: string[];
  ocrValue: unknown;
  aiValue: unknown;
  detail?: string;
};

const comparisonFields: ComparisonField[] = [
  { key: "document_type", label: "文档类型 Document Type" },
  { key: "client_name", label: "Client Name", editable: "client_name" },
  { key: "account_number", label: "Account Number", editable: "account_number" },
  { key: "scheme_name", label: "Scheme Name", editable: "scheme_name" },
  { key: "trustee", label: "Trustee", editable: "trustee" },
  { key: "currency", label: "币种 Currency" },
  { key: "as_of_date", label: "As-of Date", editable: "as_of_date" },
  { key: "total_balance", label: "Total Balance", editable: "total_balance" },
  { key: "lifetime_net_contributions", label: "累计净供款（仅参考）" },
  { key: "lifetime_gain_loss", label: "累计投资盈亏（仅参考）" },
];

const documentTypeLabels: Record<string, string> = {
  empf_account_page: "eMPF账户余额页面",
  contribution_record: "eMPF供款记录详情",
  contribution_asset_transfer_record: "供款／资产转入记录",
  unknown: "未知或不支持的文件",
};

function value(record: StatementImport | null, key: string) {
  const current = record?.extracted?.[key];
  return current == null ? "" : String(current);
}

function confirmedValue(record: StatementImport | null, key: string) {
  const current = record?.reviewed?.[key] ?? record?.extracted?.[key];
  return current == null ? "" : String(current);
}

function aiValue(record: StatementImport | null, key: string) {
  const current = record?.ai_recognition?.values?.[key] ?? record?.ai_recognition?.extracted?.[key];
  return current == null ? "" : String(current);
}

function confidence(record: StatementImport | null, key: string) {
  const score = record?.confidence?.[key] ?? 0;
  return `本地OCR置信度 ${Math.round(score * 100)}%${score < 0.85 ? " · 请重点核对" : ""}`;
}

function comparableValue(key: string, raw: unknown) {
  const text = raw == null ? "" : String(raw).trim();
  if (!text) return "";
  if (["total_balance", "lifetime_net_contributions", "lifetime_gain_loss"].includes(key)) {
    const numeric = Number(text.replace(/HKD|[$,\s]/gi, "").replace(/[()]/g, (token) => token === "(" ? "-" : ""));
    return Number.isFinite(numeric) ? numeric.toFixed(2) : text.toLowerCase();
  }
  if (key === "account_number") return text.replace(/[^a-z0-9]/gi, "").toUpperCase();
  if (key === "as_of_date") {
    const dayFirst = text.match(/^(\d{1,2})[\/-](\d{1,2})[\/-](\d{4})$/);
    if (dayFirst) return `${dayFirst[3]}-${dayFirst[2].padStart(2, "0")}-${dayFirst[1].padStart(2, "0")}`;
  }
  return text.replace(/\s+/g, " ").toLowerCase();
}

function valuesAgree(key: string, left: unknown, right: unknown) {
  return comparableValue(key, left) === comparableValue(key, right);
}

function initialReviewValues(record: StatementImport | null): Record<ReviewKey, string> {
  return {
    client_name: value(record, "client_name"),
    account_number: value(record, "account_number"),
    scheme_name: value(record, "scheme_name"),
    trustee: value(record, "trustee"),
    as_of_date: value(record, "as_of_date"),
    total_balance: value(record, "total_balance"),
  };
}

function recognitionSucceeded(record: StatementImport | null) {
  const status = record?.ai_recognition?.status?.toUpperCase();
  return status === "AGREED" || status === "CONFLICT" || status === "INCOMPLETE";
}

function fieldHasConflict(record: StatementImport | null, field: ComparisonField) {
  if (!recognitionSucceeded(record)) return false;
  const serverConflicts = record?.ai_recognition?.conflicts || [];
  const serverUncorroborated = record?.ai_recognition?.uncorroborated || [];
  const aiValues = record?.ai_recognition?.values || record?.ai_recognition?.extracted || {};
  return serverConflicts.some((item) => item.field === field.key)
    || serverUncorroborated.some((item) => item.field === field.key)
    || !valuesAgree(field.key, record?.extracted?.[field.key], aiValues[field.key]);
}

const holdingFieldLabels: Record<string, string> = {
  fund_name: "Fund Name",
  market_value: "Market Value",
  investment_gain_loss: "Investment Gain/Loss",
  portfolio_percent: "Portfolio %",
  units: "Units",
  unit_price: "Unit Price",
  mandatory_contributions: "Mandatory Contributions",
  voluntary_contributions: "Voluntary Contributions",
  balance_as_of: "Balance As-of",
};

const validationLabels: Record<string, string> = {
  total_equals_lifetime_net_plus_gain_loss: "总余额 = 累计净供款 + 累计投资盈亏",
  total_equals_sum_of_holding_market_values: "总余额 = 持仓市值合计",
};

function reviewFieldLabel(field: string) {
  const topLevel = comparisonFields.find((item) => item.key === field);
  if (topLevel) return topLevel.label;
  if (field === "holdings.length") return "持仓项目数量";
  const holding = field.match(/^holdings\[(\d+)]\.(.+)$/);
  if (holding) return `持仓 #${Number(holding[1]) + 1} · ${holdingFieldLabels[holding[2]] || holding[2]}`;
  return field;
}

function issueDisplayValue(raw: unknown) {
  if (raw == null || raw === "") return "未识别";
  if (typeof raw === "object") return JSON.stringify(raw);
  return String(raw);
}

function buildReviewIssues(record: StatementImport | null): ReviewIssue[] {
  const recognition = record?.ai_recognition;
  if (!recognition) return [];
  const issues = new Map<string, ReviewIssue>();
  const aiValues = recognition.values || recognition.extracted || {};

  function addField(field: string, reason: string, ocrValue: unknown, aiValue: unknown, detail?: string) {
    const id = `field:${field}`;
    const current = issues.get(id);
    if (current) {
      if (!current.reasons.includes(reason)) current.reasons.push(reason);
      if (detail && !current.detail) current.detail = detail;
      return;
    }
    issues.set(id, { id, field, label: reviewFieldLabel(field), reasons: [reason], ocrValue, aiValue, detail });
  }

  for (const item of recognition.conflicts || []) {
    addField(item.field, "值冲突", item.ocr_value, item.ai_value);
  }
  for (const item of recognition.uncorroborated || []) {
    addField(item.field, "仅单边识别", item.ocr_value, item.ai_value);
  }
  for (const field of recognition.missing_critical_fields || []) {
    addField(field, "关键字段缺失", record?.extracted?.[field], aiValues[field]);
  }
  for (const field of recognition.uncorroborated_critical_fields || []) {
    addField(field, "关键字段缺少交叉确认", record?.extracted?.[field], aiValues[field]);
  }

  const rawUncertain = Array.isArray(aiValues.uncertain_fields) ? aiValues.uncertain_fields.map(String) : [];
  const uncertainCritical = new Set([
    ...(recognition.uncertain_critical_fields || []),
    ...rawUncertain,
  ]);
  for (const field of uncertainCritical) {
    addField(field, "Sol标记不确定", record?.extracted?.[field], aiValues[field]);
  }

  for (const field of comparisonFields) {
    if (fieldHasConflict(record, field) && !issues.has(`field:${field.key}`)) {
      addField(field.key, "识别结果不一致", record?.extracted?.[field.key], aiValues[field.key]);
    }
  }

  for (const checkName of recognition.validation_failures || []) {
    const check = recognition.validation_checks?.find((item) => item.check === checkName);
    const id = `validation:${checkName}`;
    issues.set(id, {
      id,
      field: checkName,
      label: validationLabels[checkName] || checkName,
      reasons: ["数学校验失败"],
      ocrValue: "不适用",
      aiValue: "校验不通过",
      detail: check?.difference != null ? `差额 ${check.difference}` : undefined,
    });
  }

  if (recognition.recognition_requires_human_review && !issues.size) {
    issues.set("recognition:manual", {
      id: "recognition:manual",
      field: "recognition",
      label: "Sol识别结果",
      reasons: ["服务端要求人工复核"],
      ocrValue: "请查看原图",
      aiValue: "请查看识别值",
    });
  }
  return [...issues.values()];
}

function HoldingsSourceTable({ title, holdings, selected }: { title: string; holdings: Holding[]; selected: boolean }) {
  return <div className={`holding-source-card ${selected ? "selected" : ""}`}>
    <div className="holding-source-card-heading">
      <div><strong>{title}</strong><span>{holdings.length}项持仓</span></div>
      {selected ? <b><CheckCircle2 size={13} />当前入账来源</b> : null}
    </div>
    {holdings.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap"><table><thead><tr><th>Fund</th><th>Market Value</th><th>Gain/Loss</th><th>Units</th><th>Unit Price</th></tr></thead><tbody>{holdings.map((holding, index) => <tr key={`${holding.fund_name}-${index}`}><td>{holding.fund_name || "-"}</td><td>{holding.market_value || "-"}</td><td>{holding.investment_gain_loss || "-"}</td><td>{holding.units || "-"}</td><td>{holding.unit_price || "-"}</td></tr>)}</tbody></table></div> : <div className="holding-source-empty">该来源没有识别到持仓项目</div>}
  </div>;
}

function DocumentRoutingNotice({ record }: { record: StatementImport }) {
  const documentType = value(record, "document_type") || "unknown";
  const solType = aiValue(record, "document_type");
  const details = (record.extracted?.document_details as Record<string, unknown> | undefined) || {};
  return <div className="document-routing-notice">
    <TriangleAlert size={30} />
    <div>
      <strong>{documentTypeLabels[documentType] || documentTypeLabels.unknown}</strong>
      <p>这不是账户余额页面，系统已禁止生成余额快照。请保留原始凭证，并在“资金与余额”模块人工复核后登记交易。</p>
      {solType ? <span>Sol分类：{documentTypeLabels[solType] || solType}</span> : null}
    </div>
    {Object.keys(details).length ? <div className="document-routing-details">{Object.entries(details).map(([key, raw]) => <span key={key}><small>{key}</small><b>{String(raw)}</b></span>)}</div> : null}
  </div>;
}

export default function ImportsPage({ notify }: { notify: (message: string) => void }) {
  const imports = useApiList<StatementImport>("/api/statement-imports");
  const accounts = useApiList<Account>("/api/accounts");
  const snapshots = useApiList<BalanceSnapshot>("/api/balance-snapshots");
  const [importSearch, setImportSearch] = useState("");
  const [importStatus, setImportStatus] = useState("");
  const filteredImports = imports.data.filter((item) => matchesSearch(item.original_name, importSearch) && (!importStatus || item.status === importStatus));
  const importPages = usePagination(filteredImports, `${importSearch}:${importStatus}`, 12);
  const [selected, setSelected] = useState<StatementImport | null>(null);
  const [uploading, setUploading] = useState(false);
  const [recognizing, setRecognizing] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [localError, setLocalError] = useState("");
  const [assistant, setAssistant] = useState<AiAssistantStatus | null>(null);
  const [assistantLoading, setAssistantLoading] = useState(true);
  const [conflictsAcknowledged, setConflictsAcknowledged] = useState(false);
  const [solDocumentTypeReviewed, setSolDocumentTypeReviewed] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const deleteBusyRef = useRef(false);
  const [holdingsSource, setHoldingsSource] = useState<HoldingsSource>("ocr");
  const [holdingsDifferenceReason, setHoldingsDifferenceReason] = useState("");
  const [selectedAccountId, setSelectedAccountId] = useState("");
  const [reviewValues, setReviewValues] = useState<Record<ReviewKey, string>>(initialReviewValues(null));
  const holdings = (selected?.extracted?.holdings as Holding[] | undefined) || [];
  const aiHoldings = ((selected?.ai_recognition?.values?.holdings ?? selected?.ai_recognition?.extracted?.holdings) as Holding[] | undefined) || [];
  const selectedHoldings = holdingsSource === "sol" ? aiHoldings : holdings;
  const holdingsReduced = selectedHoldings.length < Math.max(holdings.length, aiHoldings.length);
  const holdingsReasonValid = holdingsDifferenceReason.trim().length >= 2 && holdingsDifferenceReason.trim().length <= 500;
  const recognized = recognitionSucceeded(selected);
  const comparisonRows = recognized ? comparisonFields.map((field) => ({ field, conflict: fieldHasConflict(selected, field) })) : [];
  const reviewIssues = buildReviewIssues(selected);
  const reviewIssueCount = reviewIssues.length;
  const aiNeedsReview = reviewIssueCount > 0 || selected?.ai_recognition?.status?.toUpperCase() !== "AGREED";
  const assistantReady = assistant?.status === "ready";
  const localDocumentType = value(selected, "document_type") || "unknown";
  const solDocumentType = aiValue(selected, "document_type");
  const usesSolBalanceClassification = localDocumentType === "unknown" && solDocumentType === "empf_account_page";
  const canReviewAsBalancePage = localDocumentType === "empf_account_page" || usesSolBalanceClassification;
  const confirmedAccount = accounts.data.find((item) => item.id === selected?.confirmed_account_id);
  const confirmedSnapshot = snapshots.data.find((item) => item.id === selected?.confirmed_snapshot_id);
  const selectedExistingAccount = accounts.data.find((item) => item.id === Number(selectedAccountId));
  const confirmedHoldings = (confirmedSnapshot?.holdings as Holding[] | undefined) || [];
  const statementWriteBusy = uploading || recognizing || reviewing || deletingId !== null;

  useEffect(() => {
    getAiAssistantStatus()
      .then(setAssistant)
      .catch((err: Error) => setAssistant({
        available: false,
        authenticated: false,
        status: "unavailable",
        model: SOL_MODEL_ID,
        message: err.message,
      }))
      .finally(() => setAssistantLoading(false));
  }, []);

  useEffect(() => {
    if (selected) {
      const refreshed = imports.data.find((item) => item.id === selected.id);
      if (refreshed) setSelected(refreshed);
    }
  }, [imports.data]);

  useEffect(() => {
    setReviewValues(initialReviewValues(selected));
    setConflictsAcknowledged(false);
    setSolDocumentTypeReviewed(false);
    setHoldingsSource("ocr");
    setHoldingsDifferenceReason("");
    setSelectedAccountId("");
  }, [selected?.id]);

  function chooseHoldings(source: HoldingsSource) {
    setHoldingsSource(source);
    setHoldingsDifferenceReason("");
  }

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (recognizing || reviewing || deletingId !== null) return;
    setUploading(true);
    setLocalError("");
    try {
      const data = new FormData(event.currentTarget);
      const result = await api<StatementImport & { duplicate: boolean; upload_recovery_pending?: boolean }>("/api/statement-imports", { method: "POST", body: data });
      await imports.reload();
      setSelected(result);
      setReviewValues(initialReviewValues(result));
      chooseHoldings("ocr");
      if (result.upload_recovery_pending) {
        setLocalError("账单记录已安全处理，但上传对账标记仍待系统清理；请安全退出并重新启动，若仍有提示请停止操作并检查数据目录");
        notify(result.duplicate
          ? "已打开原记录；上传对账标记待重启清理"
          : "本地OCR已完成；上传对账标记待重启清理");
      } else {
        notify(result.duplicate ? "该文件已经上传，已打开原记录" : "本地OCR已完成，可选择Sol辅助识别后复核");
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }

  async function reparse() {
    if (!selected || selected.ai_recognition || uploading || recognizing || reviewing || deletingId !== null) return;
    setReviewing(true);
    setLocalError("");
    try {
      const result = await postJson<StatementImport>(`/api/statement-imports/${selected.id}/reparse`, {});
      setSelected(result);
      setReviewValues(initialReviewValues(result));
      setConflictsAcknowledged(false);
      chooseHoldings("ocr");
      await imports.reload();
      notify(result.ai_recognition ? "本地OCR已重新执行；将继续与原Sol结果比较，不会再次调用模型" : "本地OCR已重新执行，可运行一次Sol辅助识别");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "重新识别失败");
    } finally {
      setReviewing(false);
    }
  }

  async function runSol() {
    if (!selected || !assistantReady || selected.ai_recognition || uploading || reviewing || deletingId !== null) return;
    setRecognizing(true);
    setLocalError("");
    setConflictsAcknowledged(false);
    try {
      const result = await recognizeStatementWithAi(selected.id);
      setSelected(result);
      chooseHoldings("ocr");
      await imports.reload();
      const reviewIssueTotal = buildReviewIssues(result).length;
      notify(reviewIssueTotal ? `Sol识别完成，有${reviewIssueTotal}项需人工复核` : "Sol与本地OCR结果一致，请财务最终复核");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "Sol识别失败，已转入人工复核");
    } finally {
      setRecognizing(false);
    }
  }

  function chooseValue(key: ReviewKey, source: "local" | "ai") {
    setReviewValues((current) => ({ ...current, [key]: source === "local" ? value(selected, key) : aiValue(selected, key) }));
  }

  async function deleteImport(record: StatementImport) {
    if (deleteBusyRef.current || uploading || recognizing || reviewing) return;
    const confirmed = record.status === "CONFIRMED";
    let reason = "";
    if (confirmed) {
      const enteredReason = window.prompt(
        `请输入撤销并删除误入账记录 #${record.id} 的原因（2至500个字符）：`,
      );
      if (enteredReason === null) return;
      reason = enteredReason.trim();
      if (reason.length < 2 || reason.length > 500) {
        setLocalError("删除原因必须为2至500个字符");
        return;
      }
      if (!window.confirm(
        `最终确认撤销并删除已入账记录 #${record.id}？\n\n` +
        "系统将删除这次入账生成且未被后续业务引用的Balance Snapshot、持仓明细、原始文件及OCR/Sol结果，但不会删除Client或Sub Account。只有尚未用于Settlement且没有其他受保护引用时才允许删除；审计记录会保留，此操作不可撤销。",
      )) return;
    } else if (!window.confirm(
      `确定删除导入记录 #${record.id}？原始文件及OCR/Sol结果也会删除，审计记录会保留，此操作不可撤销。`,
    )) return;

    deleteBusyRef.current = true;
    setDeletingId(record.id);
    setLocalError("");
    try {
      const result = await api<{
        deleted: boolean;
        source_file_deleted: boolean;
        source_file_cleanup_pending?: boolean;
        cleanup_audit_failed?: boolean;
      }>(`/api/statement-imports/${record.id}`, {
        method: "DELETE",
        ...(confirmed ? { body: JSON.stringify({ reason }) } : {}),
      });
      if (selected?.id === record.id) setSelected(null);
      const refreshResults = await Promise.all([imports.reload(), accounts.reload(), snapshots.reload()]);
      const refreshFailed = refreshResults.some((refreshed) => !refreshed);
      if (result.source_file_cleanup_pending) {
        let message = result.cleanup_audit_failed
          ? "导入记录已删除，但原始文件仍待系统清理，且补充清理记录未能写入；请安全退出并重新启动，若仍有提示请停止操作并检查数据目录"
          : "导入记录已删除，但原始文件仍待系统清理；请安全退出并重新启动，若仍有提示请停止操作并检查数据目录";
        if (refreshFailed) message += "；页面资料刷新也失败，请重新载入页面确认最新状态";
        setLocalError(message);
        notify(result.cleanup_audit_failed
          ? "导入记录已删除；原始文件和补充清理记录需要系统启动时核对"
          : "导入记录已删除，原始文件待清理");
      } else if (refreshFailed) {
        setLocalError("导入记录已删除，但页面资料刷新失败，请重新载入页面确认最新状态");
        notify("导入记录已删除，但清单刷新失败");
      } else {
        notify(confirmed
          ? "误入账记录、未使用Snapshot、持仓及原件已删除；Client和Sub Account未删除"
          : result.source_file_deleted
            ? "未确认导入记录及其原始文件已删除"
            : "未确认导入记录及OCR/Sol结果已删除；原始文件原本不存在");
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "删除导入记录失败");
    } finally {
      deleteBusyRef.current = false;
      setDeletingId(null);
    }
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || uploading || recognizing || reviewing || deletingId !== null) return;
    if (holdingsReduced && !holdingsReasonValid) {
      setLocalError("所选持仓数量少于识别候选。请核对原件并选择完整明细；若较少的明细才正确，必须填写持仓差异原因。");
      return;
    }
    if (reviewIssueCount > 0 && !conflictsAcknowledged) {
      setLocalError("Sol与本地OCR存在冲突，请完成逐项核对并勾选人工确认声明。");
      return;
    }
    if (usesSolBalanceClassification && !solDocumentTypeReviewed) {
      setLocalError("请查看原件并勾选已确认采用Sol的余额页分类。");
      return;
    }
    setReviewing(true);
    setLocalError("");
    try {
      const result = await postJson<{
        statement_import: StatementImport;
        account_id: number;
        created_draft: boolean;
        snapshot: { id: number; as_of_date: string; total_balance: string; eligible_for_closing: boolean };
      }>(`/api/statement-imports/${selected.id}/confirm`, {
        client_name: reviewValues.client_name,
        account_number: reviewValues.account_number,
        scheme_name: reviewValues.scheme_name || null,
        trustee: reviewValues.trustee || null,
        as_of_date: reviewValues.as_of_date,
        total_balance: reviewValues.total_balance,
        account_id: selectedExistingAccount?.id ?? null,
        account_platform_id: selectedExistingAccount?.platform_id ?? null,
        holdings: selectedHoldings,
        holdings_difference_reason: holdingsReduced ? holdingsDifferenceReason.trim() : null,
        ai_conflicts_reviewed: reviewIssueCount > 0 && conflictsAcknowledged,
        luna_document_type_reviewed: usesSolBalanceClassification && solDocumentTypeReviewed,
      });
      setSelected(result.statement_import);
      await Promise.all([imports.reload(), accounts.reload(), snapshots.reload()]);
      notify(result.created_draft ? "已入账并创建待确认客户/账户档案" : "余额快照已正式入账");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "确认入账失败");
    } finally {
      setReviewing(false);
    }
  }

  return (
    <>
      <PageHeader
        title="余额快照导入"
        subtitle="本地OCR + Sol独立识别 · 冲突直接交由财务确认"
      />
      {(localError || imports.error || accounts.error || snapshots.error) ? <ErrorBanner message={localError || imports.error || accounts.error || snapshots.error} /> : null}

      <SectionNav items={[{ id: "statement-upload", label: "上传文件" }, { id: "statement-records", label: "导入记录" }, { id: "statement-review", label: "财务复核" }]} />
      <Panel id="statement-upload" title="上传eMPF文件" subtitle="支持账户余额页及供款凭证JPG、PNG、PDF；系统会先分类，单个文件不超过25MB">
        <form className="upload-box" onSubmit={(event) => void upload(event)}>
          <UploadCloud size={32} />
          <div><strong>选择客人账单、余额页面或供款凭证</strong><span>先在本机分类并执行OCR；非余额文件不会生成余额快照</span></div>
          <input aria-label="选择账单文件" name="file" type="file" accept=".jpg,.jpeg,.png,.pdf" required />
          <button className="primary" type="submit" disabled={statementWriteBusy}>{uploading ? "正在执行本地OCR..." : "上传并本地识别"}</button>
        </form>
      </Panel>

      <Panel title="AI大模型辅助识别" className="ai-assistant-panel">
        <div className="ai-assistant-strip">
          <div className="ai-assistant-icon"><BrainCircuit /></div>
          <div className="ai-assistant-copy">
            <strong>Sol 单模型复核</strong>
            <span>{assistantLoading ? "正在检查ChatGPT登录状态..." : assistantReady ? "ChatGPT订阅已登录 · Sol可用" : assistant?.message || "尚未登录或Sol当前不可用，请到“数据与系统”处理"}</span>
          </div>
          <StatusBadge value={assistantReady ? "READY" : assistant?.status || "UNAVAILABLE"} />
          <button className="secondary" type="button" disabled={!selected || !assistantReady || statementWriteBusy || Boolean(selected?.ai_recognition) || selected?.status === "CONFIRMED"} onClick={() => void runSol()}>
            <Sparkles size={15} />{recognizing ? "Sol识别中..." : selected?.ai_recognition ? "Sol识别已完成" : "运行Sol辅助识别"}
          </button>
        </div>
      </Panel>

      <div className="import-layout">
        <Panel id="statement-records" title="导入记录" className="import-list-panel">
          <div className="list-search"><Field label="搜索导入文件"><input type="search" value={importSearch} onChange={(event) => setImportSearch(event.target.value)} placeholder="文件名称…" /></Field><Field label="复核状态"><select value={importStatus} onChange={(event) => setImportStatus(event.target.value)}><option value="">全部记录</option><option value="NEEDS_REVIEW">待复核</option><option value="CONFIRMED">已入账</option></select></Field></div>
          {imports.loading ? <Loading /> : filteredImports.length ? <div className="import-list">{importPages.rows.map((item) => {
            const type = String(item.extracted?.document_type || "unknown");
            const solType = String(item.ai_recognition?.values?.document_type ?? item.ai_recognition?.extracted?.document_type ?? "");
            const typeLabel = type === "unknown" && solType === "empf_account_page" ? "本地未知 · Sol余额页" : documentTypeLabels[type] || documentTypeLabels.unknown;
            return <div key={item.id} className={`import-list-item ${selected?.id === item.id ? "active" : ""}`}>
              <button aria-pressed={selected?.id === item.id} className="import-select" onClick={() => setSelected(item)}><FileSearch size={18} /><span><strong>{item.original_name}</strong><small>#{item.id} · {typeLabel}</small></span><StatusBadge value={item.status} /></button>
              <button className="import-delete" type="button" title={item.status === "CONFIRMED" ? "撤销并删除误入账记录（须填写原因且未进入Settlement）" : "删除未确认导入记录"} aria-label={`删除导入记录 #${item.id}`} disabled={statementWriteBusy} onClick={() => void deleteImport(item)}><Trash2 size={14} /></button>
            </div>;
          })}</div> : <EmptyState title={importSearch || importStatus ? "没有匹配的导入记录" : "暂无导入记录"} detail={importSearch || importStatus ? "更换关键词或复核状态后重试。" : "上传第一份eMPF文件开始。"} />}
          <Pagination {...importPages} />
        </Panel>

        <Panel title="原始文件" subtitle={selected ? selected.original_name : "选择一条记录查看"}>
          {selected ? <><div className="document-open"><a className="text-link" href={`/api/statement-imports/${selected.id}/file`} target="_blank" rel="noreferrer">打开原件 ↗</a><small>预览未显示时，可直接打开文件查看。</small></div>{selected.mime_type?.startsWith("image/") ? <img className="document-preview" src={`/api/statement-imports/${selected.id}/file`} alt={`原始账单：${selected.original_name}`} /> : <iframe className="document-preview" src={`/api/statement-imports/${selected.id}/file`} title="原始账单预览" sandbox="" />}</> : <EmptyState title="尚未选择文件" detail="从导入记录选择文件后查看原件。" />}
        </Panel>

        <Panel id="statement-review" title="识别结果与财务复核" subtitle="AI只提供待确认结果，不会直接写入余额或流水">
          {selected ? selected.status === "CONFIRMED" ? <div className="confirmed-receipt">
            <header><CheckCircle2 /><div><strong>该账单已经复核并生成余额快照</strong><span>原始账单、人工确认值、账户和Snapshot均已关联保留</span></div>{confirmedAccount ? <StatusBadge value={confirmedAccount.status} /> : null}</header>
            <div className="confirmed-trace-grid">
              <span><small>Client</small><b>{confirmedAccount?.client_name || confirmedValue(selected, "client_name") || "账户资料加载中"}</b></span>
              <span><small>Platform</small><b>{confirmedAccount?.platform_name || "待确认Platform"}</b></span>
              <span><small>Account Number</small><b>{confirmedAccount?.account_number || confirmedValue(selected, "account_number") || "账户资料加载中"}</b></span>
              <span><small>Scheme</small><b>{confirmedAccount?.scheme_name || confirmedValue(selected, "scheme_name") || "-"}</b></span>
              <span><small>Trustee（MPF计划受托机构）</small><b>{confirmedValue(selected, "trustee") || "-"}</b></span>
              <span><small>Fee Plan</small><b>{confirmedAccount?.fee_plan_name || "待补全Fee Plan"}</b></span>
              <span><small>Snapshot日期</small><b>{confirmedSnapshot?.as_of_date || confirmedValue(selected, "as_of_date") || "加载中"}</b></span>
              <span><small>Snapshot金额</small><b>HKD {confirmedSnapshot?.total_balance || confirmedValue(selected, "total_balance") || "-"}</b></span>
              <span><small>Closing资格</small><b>{confirmedSnapshot ? (confirmedSnapshot.eligible_for_closing ? "可作为季末/退出日Closing" : "普通快照，不可作为Closing") : "加载中"}</b></span>
            </div>
            {confirmedSnapshot && !confirmedHoldings.length && (holdings.length || aiHoldings.length) && !selected.reviewed?.holdings_difference_reason ? <div className="warning-list" role="alert"><p>历史持仓遗漏：识别候选中有明细，但该快照保存了0项，且没有单独的差异原因。请核对原件与子账户资料；已结算或签发的记录不能直接覆盖。</p></div> : null}
            {confirmedHoldings.length ? <div className="holdings-source-review confirmed-holdings"><HoldingsSourceTable title="已确认入账持仓（凭证记录）" holdings={confirmedHoldings} selected /></div> : <small className="confirmed-audit-reference">该Snapshot保存了0项基金持仓。收费单位是已登记的Sub Account，各自使用余额、流水、收费计划及HWM计算。</small>}
            {selected.reviewed?.holdings_difference_reason ? <p className="confirmed-audit-reference">持仓差异原因：{String(selected.reviewed.holdings_difference_reason)}</p> : null}
            <p className="confirmed-next-step">{!confirmedAccount || !confirmedSnapshot
              ? "正在加载已关联的账户与Snapshot资料。"
              : confirmedAccount.status === "DRAFT"
              ? "下一步：到“客户与账户”补全Client的Company/FC及Sub Account的Fee Plan、管理日期并激活；完成前不会进入正式季度结算。"
              : confirmedSnapshot.eligible_for_closing
                ? "下一步：该Snapshot已在“资金与余额”可查；到“季度结算”选择同一Client、Platform、Fee Plan及账户期间，人工计算并Finalize。系统不会自动结算。"
                : "该Snapshot已在“资金与余额”可查；因不是季末或实际退出日，只作余额记录，不会出现在Closing选项。"}</p>
            {selected.confirmed_snapshot_id ? <small className="confirmed-audit-reference">审计引用：Snapshot #{selected.confirmed_snapshot_id}</small> : null}
          </div> : !canReviewAsBalancePage ? <DocumentRoutingNotice record={selected} /> : <form className="form-grid" onSubmit={(event) => void confirm(event)}>
            {usesSolBalanceClassification ? <label className="conflict-acknowledgement document-type-acknowledgement"><input type="checkbox" checked={solDocumentTypeReviewed} onChange={(event) => setSolDocumentTypeReviewed(event.target.checked)} /><span><strong>我已查看原件，确认这是eMPF账户余额页面</strong><small>本地OCR未能分类；勾选后采用Sol的文档类型进入人工复核，Sol不会自动生成余额快照。</small></span></label> : null}
            <div className="review-toolbar"><span>本地OCR最高置信度：{Math.round(Math.max(0, ...Object.values(selected.confidence || {})) * 100)}%</span><button type="button" className="ghost" disabled={Boolean(selected.ai_recognition) || statementWriteBusy} onClick={() => void reparse()}><RefreshCw size={15} />{selected.ai_recognition ? "本地OCR已锁定" : reviewing ? "处理中..." : "重新执行本地OCR"}</button></div>
            {selected.warnings?.length ? <div className="warning-list">{selected.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div> : null}
            {selected.ai_recognition?.warnings?.length ? <div className="warning-list ai-warning-list">{selected.ai_recognition.warnings.map((warning, index) => <p key={index}>Sol：{warning}</p>)}</div> : null}
            {selected.ai_recognition?.status?.toUpperCase() === "FAILED" ? <div className="ai-failure"><TriangleAlert size={17} /><span>Sol识别失败：{selected.ai_recognition.error || "未返回有效结果"}。不会切换其他模型，请直接人工复核。</span></div> : null}

            {recognized ? <div className="ai-comparison">
              <div className="ai-comparison-heading">
                <div><strong>本地OCR与Sol逐字段比较</strong><span>{reviewIssueCount ? `${reviewIssueCount}项差异或校验问题必须人工确认` : "当前字段全部一致，仍需财务最终确认"}</span></div>
                <StatusBadge value={aiNeedsReview ? "NEEDS_REVIEW" : "MATCHED"} />
              </div>
              <div className="ai-comparison-table">
                <div className="ai-comparison-row header"><span>字段</span><span>本地OCR</span><span>Sol</span><span>结果 / 选择</span></div>
                {comparisonRows.map(({ field, conflict }) => {
                  const local = value(selected, field.key);
                  const sol = aiValue(selected, field.key);
                  const bothMissing = !local && !sol;
                  return <div className={`ai-comparison-row ${conflict ? "conflict" : "match"}`} key={field.key}>
                    <strong>{field.label}</strong>
                    <span title={local}>{local || "未识别"}</span>
                    <span title={sol}>{sol || "未识别"}</span>
                    <div>{conflict ? <><b><TriangleAlert size={13} />冲突</b>{field.editable ? <span className="comparison-actions"><button type="button" onClick={() => chooseValue(field.editable!, "local")}>采用本地</button><button type="button" onClick={() => chooseValue(field.editable!, "ai")}>采用Sol</button></span> : null}</> : <b className={bothMissing ? "missing" : "agree"}>{bothMissing ? "均未识别" : "一致"}</b>}</div>
                  </div>;
                })}
              </div>
              {holdings.length || aiHoldings.length ? <p className="holdings-comparison">基金持仓：本地OCR {holdings.length} 项 · Sol {aiHoldings.length} 项。各收费子账户须在“客户与账户”独立登记和分配计划；识别基金不会自动建立收费子账户。</p> : null}
              {reviewIssueCount ? <div className="ai-review-register">
                <div className="ai-review-register-heading"><strong>完整人工复核清单</strong><span>{reviewIssueCount}项 · 包含持仓路径与数学校验</span></div>
                <div className="ai-review-register-row header"><span>字段 / 检查</span><span>本地OCR</span><span>Sol</span><span>复核原因</span></div>
                {reviewIssues.map((issue) => {
                  const ocrText = issueDisplayValue(issue.ocrValue);
                  const aiText = issueDisplayValue(issue.aiValue);
                  return <div className="ai-review-register-row" key={issue.id}>
                    <div><strong>{issue.label}</strong><small>{issue.field}</small></div>
                    <span title={ocrText}>{ocrText}</span>
                    <span title={aiText}>{aiText}</span>
                    <div><b>{issue.reasons.join(" / ")}</b>{issue.detail ? <small>{issue.detail}</small> : null}</div>
                  </div>;
                })}
              </div> : null}
            </div> : selected.ai_recognition ? null : <div className="ai-not-run"><BrainCircuit size={18} /><span>尚未运行Sol。本地OCR结果仍可由财务人工复核；运行Sol前请确认可以将此账单发送至OpenAI。</span></div>}

            <Field group label="匹配已有Sub Account" hint="每次切换导入记录都会清空选择；请按Client、Platform、Account Number及Scheme核对"><SearchableSelect label="匹配已有账户" name="account_id" value={selectedAccountId} onChange={setSelectedAccountId} placeholder="自动匹配 / 创建草稿" searchPlaceholder="搜索客户、账户号码或平台…" options={accounts.data.map((item) => ({ value: String(item.id), label: accountIdentityLabel(item) }))} /></Field>
            <Field label="Client Name" hint={confidence(selected, "client_name")}><input name="client_name" required value={reviewValues.client_name} onChange={(event) => setReviewValues((current) => ({ ...current, client_name: event.target.value }))} /></Field>
            <Field label="Account Number" hint={confidence(selected, "account_number")}><input name="account_number" required value={reviewValues.account_number} onChange={(event) => setReviewValues((current) => ({ ...current, account_number: event.target.value }))} /></Field>
            <Field label="Scheme Name"><input name="scheme_name" value={reviewValues.scheme_name} onChange={(event) => setReviewValues((current) => ({ ...current, scheme_name: event.target.value }))} /></Field>
            <Field label="Trustee（MPF计划受托机构）" hint="不是FC/中介人，不参与Invoice编号"><input name="trustee" value={reviewValues.trustee} onChange={(event) => setReviewValues((current) => ({ ...current, trustee: event.target.value }))} /></Field>
            <Field label="As-of Date" hint={confidence(selected, "as_of_date")}><input name="as_of_date" type="date" required value={reviewValues.as_of_date} onChange={(event) => setReviewValues((current) => ({ ...current, as_of_date: event.target.value }))} /></Field>
            <Field label="Total Balance (HKD)" hint={confidence(selected, "total_balance")}><input name="total_balance" type="number" min="0" step="0.01" required value={reviewValues.total_balance} onChange={(event) => setReviewValues((current) => ({ ...current, total_balance: event.target.value }))} /></Field>
            <div className="readonly-grid"><span><small>累计净供款（仅参考）</small><b>{value(selected, "lifetime_net_contributions") || "未识别"}</b></span><span><small>累计投资盈亏（仅参考）</small><b>{value(selected, "lifetime_gain_loss") || "未识别"}</b></span></div>
            {holdings.length || selected.ai_recognition ? <div className="holdings-source-review">
              <div className="holdings-source-toolbar">
                <div><strong>选择正式保存的持仓来源</strong><span>请对照原件选择；Sol结果需主动采用后才会保存。</span></div>
                <div className="holdings-source-buttons" role="group" aria-label="持仓入账来源">
                  <button type="button" aria-pressed={holdingsSource === "ocr"} className={holdingsSource === "ocr" ? "active" : ""} onClick={() => chooseHoldings("ocr")}><CheckCircle2 size={13} />采用本地持仓（{holdings.length}项）</button>
                  {selected.ai_recognition ? <button type="button" aria-pressed={holdingsSource === "sol"} className={holdingsSource === "sol" ? "active" : ""} onClick={() => chooseHoldings("sol")}><Sparkles size={13} />采用Sol持仓（{aiHoldings.length}项）</button> : null}
                </div>
              </div>
              <p role="status">将保存：{selectedHoldings.length}项基金持仓 · 来源：{holdingsSource === "sol" ? "Sol（人工选择）" : "本地OCR"}</p>
              {holdingsReduced ? <div className="warning-list">
                <p>所选来源只有{selectedHoldings.length}项，另一来源有更多明细。请先核对原件，避免漏存；统一差异勾选不能代替此项处理。</p>
                <Field label="持仓差异原因" hint="仅当较少的明细才正确时填写，例如另一来源重复或误识别；2至500字，随入账记录保留。"><textarea name="holdings_difference_reason" value={holdingsDifferenceReason} minLength={2} maxLength={500} required aria-invalid={!holdingsReasonValid} onChange={(event) => setHoldingsDifferenceReason(event.target.value)} /></Field>
              </div> : null}
              <div className={selected.ai_recognition ? "holdings-source-grid" : "holdings-source-grid single"}>
                <HoldingsSourceTable title="本地OCR持仓" holdings={holdings} selected={holdingsSource === "ocr"} />
                {selected.ai_recognition ? <HoldingsSourceTable title="Sol持仓" holdings={aiHoldings} selected={holdingsSource === "sol"} /> : null}
              </div>
            </div> : null}
            {reviewIssueCount > 0 ? <label className="conflict-acknowledgement"><input type="checkbox" checked={conflictsAcknowledged} onChange={(event) => setConflictsAcknowledged(event.target.checked)} /><span><strong>我已人工核对完整清单中的{reviewIssueCount}项差异与校验问题</strong><small>包含顶层字段、持仓路径、单边识别、关键字段不确定/缺失及数学校验；系统没有调用更高模型。</small></span></label> : null}
            <button className="primary" type="submit" disabled={statementWriteBusy || (holdingsReduced && !holdingsReasonValid) || (usesSolBalanceClassification && !solDocumentTypeReviewed)}>{reviewing ? "处理中..." : reviewIssueCount ? "人工复核完成并生成余额快照" : "确认并生成余额快照"}</button>
          </form> : <EmptyState title="等待选择" detail="选择左侧记录后，在此核对识别字段。" />}
        </Panel>
      </div>
    </>
  );
}
