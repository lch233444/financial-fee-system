import { FormEvent, useMemo, useState } from "react";
import { Ban, CheckCircle2, FileDown, FilePlus2, ReceiptText, RotateCcw } from "lucide-react";
import { download, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Loading, Money, PageHeader, Panel, StatusBadge } from "../components";
import { todayIso, useApiList } from "../hooks";
import type { Invoice, Settlement } from "../types";

const ACTIVE_INVOICE_STATUSES = new Set<Invoice["lifecycle_status"]>(["DRAFT", "ISSUING", "ISSUED"]);

type InvoiceCandidateLine = {
  id: number;
  settlement_id: number;
  platform_name: string;
  account_number: string;
  start_date: string | null;
  closing_date: string | null;
  service_fee: string;
};

type InvoiceCandidate = {
  key: string;
  clientId: number;
  clientName: string;
  year: number;
  quarter: number;
  feePlanId: number;
  feePlanName: string;
  companyName: string;
  fcName: string;
  sourceCount: number;
  totalCents: number;
  accountLines: InvoiceCandidateLine[];
};

function invoiceGroupKey(clientId: number, year: number, quarter: number, feePlanId: number) {
  return `${clientId}:${year}:${quarter}:${feePlanId}`;
}

function moneyToCents(value: string | null | undefined) {
  const normalized = (value ?? "0.00").trim();
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(normalized);
  if (!match) return 0;
  const cents = Number(match[2]) * 100 + Number((match[3] ?? "").padEnd(2, "0"));
  return match[1] ? -cents : cents;
}

function moneyFromCents(value: number) {
  return (value / 100).toFixed(2);
}

function InvoiceLifecycleBadge({ value }: { value: Invoice["lifecycle_status"] }) {
  return value === "ISSUING"
    ? <span className="status status-issuing">出具中</span>
    : <StatusBadge value={value} />;
}

function AccountLineTable({ lines }: { lines: InvoiceCandidateLine[] }) {
  return lines.length ? (
    <div className="table-wrap invoice-account-lines">
      <table>
        <thead><tr><th>Platform</th><th>Sub Account</th><th>账户期间</th><th>锁定Service Fee</th></tr></thead>
        <tbody>{lines.map((line) => (
          <tr key={`${line.settlement_id}:${line.id}`}>
            <td>{line.platform_name}</td>
            <td><strong>{line.account_number}</strong><small className="cell-note">Settlement #{line.settlement_id}</small></td>
            <td>{line.start_date || "-"}<small className="cell-note">至 {line.closing_date || "-"}</small></td>
            <td><Money value={line.service_fee} /></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  ) : <EmptyState title="暂无账户收费明细" detail="该Invoice没有可展示的冻结账户行。" />;
}

export default function InvoicesPage({ notify }: { notify: (message: string) => void }) {
  const invoices = useApiList<Invoice>("/api/invoices");
  const settlements = useApiList<Settlement>("/api/settlements");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [candidateKey, setCandidateKey] = useState("");
  const [error, setError] = useState("");
  const [paymentBusy, setPaymentBusy] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState<"COMPLETE" | "RETURN_TO_DRAFT" | "">("");
  const selected = invoices.data.find((item) => item.id === selectedId) || null;
  const candidateState = useMemo(() => {
    const activeInvoices = invoices.data.filter((invoice) => ACTIVE_INVOICE_STATUSES.has(invoice.lifecycle_status));
    const claimedSettlementIds = new Set(activeInvoices.flatMap((invoice) =>
      invoice.settlement_ids?.length ? invoice.settlement_ids : [invoice.settlement_id]));
    const activeInvoiceGroups = new Set(activeInvoices.map((invoice) =>
      invoiceGroupKey(invoice.client_id, invoice.year, invoice.quarter, invoice.fee_plan_id)));
    const groups = new Map<string, Settlement[]>();

    for (const settlement of settlements.data) {
      if (settlement.status !== "FINALIZED") continue;
      const key = invoiceGroupKey(settlement.client_id, settlement.year, settlement.quarter, settlement.fee_plan_id);
      groups.set(key, [...(groups.get(key) ?? []), settlement]);
    }

    const candidates: InvoiceCandidate[] = [];
    let lateSettlementGroupCount = 0;
    let ownershipMismatchGroupCount = 0;

    for (const [key, groupedSettlements] of groups) {
      const availableSettlements = groupedSettlements.filter((settlement) => !claimedSettlementIds.has(settlement.id));
      if (activeInvoiceGroups.has(key)) {
        if (availableSettlements.length) lateSettlementGroupCount += 1;
        continue;
      }
      if (!availableSettlements.length) continue;

      const companyIds = new Set(availableSettlements.map((settlement) => settlement.company_id));
      const fcIds = new Set(availableSettlements.map((settlement) => settlement.fc_id));
      if (companyIds.size !== 1 || fcIds.size !== 1 || availableSettlements[0].company_id == null || availableSettlements[0].fc_id == null) {
        ownershipMismatchGroupCount += 1;
        continue;
      }

      const totalCents = availableSettlements.reduce((total, settlement) => total + moneyToCents(settlement.service_fee), 0);
      if (totalCents <= 0) continue;
      const first = availableSettlements[0];
      const accountLines = availableSettlements.flatMap((settlement) => settlement.account_lines.length
        ? settlement.account_lines.map((line) => ({
            id: line.id,
            settlement_id: settlement.id,
            platform_name: settlement.platform_name,
            account_number: line.account_number,
            start_date: line.start_date,
            closing_date: line.closing_date,
            service_fee: line.service_fee ?? "0.00",
          }))
        : [{
            id: -settlement.id,
            settlement_id: settlement.id,
            platform_name: settlement.platform_name,
            account_number: "历史组合",
            start_date: settlement.start_date,
            closing_date: settlement.closing_date,
            service_fee: settlement.service_fee,
          }]);
      candidates.push({
        key,
        clientId: first.client_id,
        clientName: first.client_name,
        year: first.year,
        quarter: first.quarter,
        feePlanId: first.fee_plan_id,
        feePlanName: first.fee_plan_name,
        companyName: first.company_name ?? "未冻结Company",
        fcName: first.fc_name ?? "未冻结FC",
        sourceCount: availableSettlements.length,
        totalCents,
        accountLines,
      });
    }

    candidates.sort((left, right) => right.year - left.year || right.quarter - left.quarter || left.clientName.localeCompare(right.clientName, "zh-Hans-CN"));
    return { candidates, lateSettlementGroupCount, ownershipMismatchGroupCount };
  }, [settlements.data, invoices.data]);
  const selectedCandidate = candidateState.candidates.find((candidate) => candidate.key === candidateKey) ?? null;

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedCandidate) {
      setError("请选择可建立Invoice的客户季度组合");
      return;
    }
    const data = new FormData(event.currentTarget);
    try {
      setError("");
      const result = await postJson<Invoice>("/api/invoices", {
        client_id: selectedCandidate.clientId,
        year: selectedCandidate.year,
        quarter: selectedCandidate.quarter,
        fee_plan_id: selectedCandidate.feePlanId,
        language: data.get("language"),
      });
      await invoices.reload();
      setSelectedId(result.id);
      setCandidateKey("");
      notify("Invoice Draft已建立");
    } catch (err) { setError(err instanceof Error ? err.message : "建立失败"); }
  }

  async function issue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const data = new FormData(event.currentTarget);
    try {
      setError("");
      await postJson(`/api/invoices/${selected.id}/issue`, { issue_date: data.get("issue_date"), due_date: data.get("due_date") || null, language: data.get("language") });
      await invoices.reload();
      notify("Invoice已出具并锁定编号");
    } catch (err) { setError(err instanceof Error ? err.message : "出具失败"); }
  }

  async function addPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      setError("");
      setPaymentBusy(true);
      await postJson(`/api/invoices/${selected.id}/payments`, { payment_date: data.get("payment_date"), amount: data.get("amount"), method: data.get("method"), remark: data.get("remark") || null });
      form.reset();
      await invoices.reload();
      notify("Payment已登记");
    } catch (err) {
      setError(err instanceof Error ? err.message : "付款登记失败");
    } finally {
      setPaymentBusy(false);
    }
  }

  async function voidInvoice() {
    if (!selected) return;
    const reason = window.prompt(selected.lifecycle_status === "DRAFT" ? "请输入作废Draft的原因：" : "请输入作废原因。编号不会复用：");
    if (!reason) return;
    try {
      setError("");
      await postJson(`/api/invoices/${selected.id}/void`, { reason });
      await invoices.reload();
      notify(selected.invoice_number ? "Invoice已作废，原编号永久保留" : "Invoice Draft已作废，可按最新Settlement重新建立");
    } catch (err) { setError(err instanceof Error ? err.message : "作废失败"); }
  }

  async function recoverIssuing(action: "COMPLETE" | "RETURN_TO_DRAFT") {
    if (!selected || selected.lifecycle_status !== "ISSUING") return;
    try {
      setError("");
      setRecoveryBusy(action);
      await postJson<Invoice>(`/api/invoices/${selected.id}/recover-issuing`, { action });
      await invoices.reload();
      notify(action === "COMPLETE" ? "已核验归档文件并完成Invoice签发" : "Invoice已退回Draft；预留编号不会复用");
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复Invoice失败");
    } finally {
      setRecoveryBusy("");
    }
  }

  async function downloadInvoicePdf(language: "zh" | "en") {
    if (!selected?.invoice_number) return;
    try {
      setError("");
      await download(
        `/api/invoices/${selected.id}/pdf?language=${language}`,
        `${selected.invoice_number}_${language}.pdf`,
        { method: "POST" },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invoice PDF下载失败");
    }
  }

  return (
    <>
      <PageHeader title="Invoice与收款" subtitle="按客户、季度和收费计划汇总已锁定的Sub Account收费；跨Platform只在Invoice阶段合并" />
      {error || invoices.error || settlements.error ? <ErrorBanner message={error || invoices.error || settlements.error} /> : null}
      <div className="split-layout invoices-top">
        <Panel title="建立Invoice Draft" subtitle="系统自动纳入该客户季度全部未占用的Finalized Settlement；不由前端传入金额或来源清单">
          <form className="form-grid" onSubmit={(e) => void createDraft(e)}>
            <Field label="客户季度Invoice组合"><select name="candidate_key" required disabled={invoices.loading || settlements.loading || !candidateState.candidates.length} value={selectedCandidate?.key ?? ""} onChange={(event) => setCandidateKey(event.target.value)}><option value="" disabled>请选择</option>{candidateState.candidates.map((candidate) => <option key={candidate.key} value={candidate.key}>{candidate.clientName} · {candidate.year} Q{candidate.quarter} · {candidate.sourceCount}份Settlement · HKD {moneyFromCents(candidate.totalCents)}</option>)}</select></Field>
            <Field label="默认PDF语言"><select name="language" defaultValue="zh"><option value="zh">中文</option><option value="en">English</option></select></Field>
            {candidateState.lateSettlementGroupCount ? <div className="invoice-candidate-warning">有 {candidateState.lateSettlementGroupCount} 个客户季度已有有效Invoice，但后来又出现未纳入的Settlement。迟到Settlement需先更正或作废原Invoice后重开，系统不会建立第二张有效季度Invoice。</div> : null}
            {candidateState.ownershipMismatchGroupCount ? <div className="invoice-candidate-warning">有 {candidateState.ownershipMismatchGroupCount} 个组合的冻结Company/FC缺失或不一致，已从候选中排除；请先处理Settlement归属。</div> : null}
            {selectedCandidate ? <div className="invoice-group-preview">
              <header><div><strong>{selectedCandidate.clientName} · {selectedCandidate.year} Q{selectedCandidate.quarter}</strong><span>{selectedCandidate.companyName} · {selectedCandidate.fcName} · {selectedCandidate.feePlanName}</span></div><div><small>{selectedCandidate.sourceCount}份Settlement</small><Money value={moneyFromCents(selectedCandidate.totalCents)} emphasis /></div></header>
              <AccountLineTable lines={selectedCandidate.accountLines} />
            </div> : invoices.loading || settlements.loading ? <Loading /> : candidateState.candidates.length ? null : <EmptyState title="暂无可建立的客户季度Invoice" detail="需要同组Finalized Settlement的锁定Service Fee合计大于0，且冻结Company/FC一致。" />}
            <button className="primary" type="submit" disabled={!selectedCandidate}><FilePlus2 size={17} />建立Draft</button>
          </form>
        </Panel>
        <Panel title="选中Invoice">
          {selected ? <div className="invoice-summary"><header><ReceiptText /><div><strong>{selected.invoice_number || `Draft #${selected.id}`}</strong><span>{selected.client_name} · {selected.company_name} · {selected.year} Q{selected.quarter}</span></div><InvoiceLifecycleBadge value={selected.lifecycle_status} /></header><div className="invoice-source-summary"><span><small>Settlement来源</small><strong>{selected.source_count} 份</strong></span><span><small>账户收费明细</small><strong>{selected.account_lines.length} 行</strong></span></div><div className="invoice-amount"><small>Service Fee合计</small><Money value={selected.amount} emphasis /></div><div className="invoice-balance"><span>已收 <Money value={selected.paid_amount} /></span><span>未收 <Money value={selected.outstanding_amount} /></span><StatusBadge value={selected.payment_status} /></div><AccountLineTable lines={selected.account_lines} /></div> : <EmptyState title="尚未选择Invoice" detail="从下方列表选择一条记录。" />}
        </Panel>
      </div>

      {selected?.lifecycle_status === "DRAFT" ? <Panel title="正式出具" subtitle="若Draft建立后又有同组Settlement Finalize，服务端会拒绝漏项签发；请作废本Draft并按最新来源重建。"><form className="inline-form" onSubmit={(e) => void issue(e)}><Field label="Issue Date"><input name="issue_date" type="date" defaultValue={todayIso()} required /></Field><Field label="Due Date" hint="留空则采用Company默认天数"><input name="due_date" type="date" /></Field><Field label="Language"><select name="language" defaultValue={selected.language}><option value="zh">中文</option><option value="en">English</option></select></Field><button className="primary" type="submit">Issued并分配编号</button><button className="danger" type="button" onClick={() => void voidInvoice()}><Ban size={17} />作废Draft</button></form></Panel> : null}
      {selected?.lifecycle_status === "ISSUING" ? <Panel title="恢复出具中的Invoice" subtitle="系统在预留编号后曾中断。请依据归档文件完整性完成签发，或退回Draft重新出具；已预留编号永久保留且不会复用。"><div className="invoice-recovery"><div><strong>{!selected.issue_recovery ? "恢复状态尚未就绪" : selected.issue_recovery.files_complete ? "中英文归档文件完整" : "归档文件不完整"}</strong><span>{!selected.issue_recovery ? "请刷新Invoice清单；恢复状态可用前不会开放任何操作。" : selected.issue_recovery.files_complete ? "可以完成签发，也可以退回Draft重新核对。" : "不能直接完成签发，请退回Draft后重新生成两份归档PDF。"}</span></div><div className="invoice-actions"><button className="primary" type="button" disabled={Boolean(recoveryBusy) || !selected.issue_recovery?.can_complete} onClick={() => void recoverIssuing("COMPLETE")}><CheckCircle2 size={17} />{recoveryBusy === "COMPLETE" ? "正在核验..." : "完成签发"}</button><button className="secondary" type="button" disabled={Boolean(recoveryBusy) || !selected.issue_recovery?.can_return_to_draft} onClick={() => void recoverIssuing("RETURN_TO_DRAFT")}><RotateCcw size={17} />{recoveryBusy === "RETURN_TO_DRAFT" ? "正在退回..." : "退回Draft"}</button></div></div></Panel> : null}
      {selected?.lifecycle_status === "ISSUED" ? <Panel title="PDF与收款"><div className="invoice-actions"><button className="secondary" onClick={() => void downloadInvoicePdf("zh")}><FileDown size={17} />中文PDF</button><button className="secondary" onClick={() => void downloadInvoicePdf("en")}><FileDown size={17} />English PDF</button><button className="danger" onClick={() => void voidInvoice()} disabled={Number(selected.paid_amount) > 0}><Ban size={17} />作废Invoice</button></div><form className="inline-form payment-form" onSubmit={(e) => void addPayment(e)}><Field label="Payment Date"><input name="payment_date" type="date" defaultValue={todayIso()} required disabled={paymentBusy} /></Field><Field label="Amount (HKD)"><input name="amount" type="number" min="0.01" step="0.01" required disabled={paymentBusy} /></Field><Field label="Method"><select name="method" defaultValue="BANK_TRANSFER" disabled={paymentBusy}><option value="BANK_TRANSFER">Bank Transfer</option><option value="CHEQUE">Cheque</option><option value="OTHER">Other</option></select></Field><Field label="Remark"><input name="remark" disabled={paymentBusy} /></Field><button className="primary" type="submit" disabled={paymentBusy || selected.payment_status === "PAID"}>{paymentBusy ? "正在登记..." : "登记Payment"}</button></form></Panel> : null}
      {selected?.lifecycle_status === "VOID" ? <Panel title="作废Invoice档案" subtitle={`作废原因：${selected.void_reason || "未记录"}`}>{selected.invoice_number ? <div className="invoice-actions"><button className="secondary" onClick={() => void downloadInvoicePdf("zh")}><FileDown size={17} />原中文PDF</button><button className="secondary" onClick={() => void downloadInvoicePdf("en")}><FileDown size={17} />Original English PDF</button></div> : <EmptyState title="Draft在签发前已作废" detail="该记录未分配Invoice编号，因此没有正式归档PDF。" />}</Panel> : null}

      <Panel title="Invoice清单">{invoices.loading ? <Loading /> : invoices.error ? null : invoices.data.length ? <div className="table-wrap"><table><thead><tr><th>Invoice No.</th><th>Client / Period</th><th>来源</th><th>Issue / Due</th><th>Amount</th><th>Payment</th><th>Lifecycle</th></tr></thead><tbody>{invoices.data.map((item) => <tr key={item.id} className={selectedId === item.id ? "selected clickable" : "clickable"} onClick={() => setSelectedId(item.id)}><td><strong>{item.invoice_number || `Draft #${item.id}`}</strong></td><td>{item.client_name}<small className="cell-note">{item.year} Q{item.quarter} · {item.fc_name}</small></td><td>{item.source_count}份Settlement<small className="cell-note">{item.account_lines.length}行账户明细</small></td><td>{item.issue_date || "-"}<small className="cell-note">Due {item.due_date || "-"}</small></td><td><Money value={item.amount} /></td><td><StatusBadge value={item.payment_status} /></td><td><InvoiceLifecycleBadge value={item.lifecycle_status} /></td></tr>)}</tbody></table></div> : <EmptyState title="暂无Invoice" detail="先Finalized一个产生Service Fee的客户季度组合。" />}</Panel>
    </>
  );
}
