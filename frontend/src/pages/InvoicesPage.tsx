import SearchableSelect, { matchesSearch } from "../SearchableSelect";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Ban, CheckCircle2, FileDown, FilePlus2, ReceiptText, RotateCcw } from "lucide-react";
import { api, download, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Loading, Money, PageHeader, Panel, StatusBadge } from "../components";
import { todayIso, useApiList } from "../hooks";
import { settlementSourcesFollowOriginal } from "../invoiceSources";
import type { Invoice, InvoiceCorrection, Settlement } from "../types";

type UploadedAttachment = {
  id: number;
  entity_type: string;
  entity_id: number | null;
  original_name: string;
  duplicate?: boolean;
};

function invoicePdfDownloadName(invoice: Invoice, language: "zh" | "en") {
  const safeNumber = (invoice.invoice_number || "")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_")
    .replace(/[. ]+$/g, "");
  const readablePrefix = safeNumber.slice(0, 130).replace(/[. ]+$/g, "");
  return `${readablePrefix || "Invoice"}-record-${invoice.id}_${language}.pdf`;
}

const ACTIVE_INVOICE_STATUSES = new Set<Invoice["lifecycle_status"]>(["DRAFT", "ISSUING", "ISSUED"]);

type InvoiceCandidateLine = {
  id: number;
  settlement_id: number;
  platform_name: string;
  account_number: string;
  scheme_name: string | null;
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
  return parseMoneyInput(value ?? "0.00") ?? 0;
}

function parseMoneyInput(value: string) {
  const normalized = (value ?? "0.00").trim();
  const match = /^(-?)(\d+)(?:\.(\d{1,2}))?$/.exec(normalized);
  if (!match) return null;
  const cents = Number(match[2]) * 100 + Number((match[3] ?? "").padEnd(2, "0"));
  if (!Number.isSafeInteger(cents)) return null;
  return match[1] ? -cents : cents;
}

function moneyFromCents(value: number) {
  return (value / 100).toFixed(2);
}

async function uploadUnclaimedProof(file: File, entityType: "PAYMENT" | "PAYMENT_REFUND") {
  const data = new FormData();
  data.set("file", file);
  data.set("entity_type", entityType);
  return api<UploadedAttachment>("/api/attachments", { method: "POST", body: data });
}

type PendingRefund = {
  payment_id: number;
  refund_date: string;
  amount: string;
  method: string;
  reason: string;
  proof: File;
};

type RefundRequest = Omit<PendingRefund, "proof"> & { proof_attachment_id: number };

function InvoiceLifecycleBadge({ value }: { value: Invoice["lifecycle_status"] }) {
  return value === "ISSUING"
    ? <span className="status status-issuing">出具中</span>
    : <StatusBadge value={value} />;
}

function AccountLineTable({ lines }: { lines: InvoiceCandidateLine[] }) {
  return lines.length ? (
    <div className="table-wrap invoice-account-lines">
      <table>
        <thead><tr><th>Platform</th><th>Sub Account / Scheme</th><th>账户期间</th><th>锁定Service Fee</th></tr></thead>
        <tbody>{lines.map((line) => (
          <tr key={`${line.settlement_id}:${line.id}`}>
            <td>{line.platform_name}</td>
            <td><strong>{line.account_number}</strong><small className="cell-note">Scheme（当前资料）· {line.scheme_name?.trim() || "未填写"}</small><small className="cell-note">Settlement #{line.settlement_id}</small></td>
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
  const corrections = useApiList<InvoiceCorrection>("/api/invoice-corrections");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [candidateKey, setCandidateKey] = useState("");
  const [invoiceSearch, setInvoiceSearch] = useState("");
  const visibleInvoices = invoices.data.filter((item) => matchesSearch(`${item.client_name ?? ""} ${item.invoice_number ?? ""}`, invoiceSearch));
  const [error, setError] = useState("");
  const [paymentBusy, setPaymentBusy] = useState(false);
  const [correctionBusy, setCorrectionBusy] = useState(false);
  const [invoiceVoidBusy, setInvoiceVoidBusy] = useState(false);
  const [issueBusy, setIssueBusy] = useState(false);
  const [recoveryBusy, setRecoveryBusy] = useState<"COMPLETE" | "RETURN_TO_DRAFT" | "">("");
  const [paymentDifference, setPaymentDifference] = useState("0.00");
  const [replacementInvoiceId, setReplacementInvoiceId] = useState("");
  const [retainedAmounts, setRetainedAmounts] = useState<Record<number, string>>({});
  const [refundAmounts, setRefundAmounts] = useState<Record<number, string>>({});
  const [correctionDifference, setCorrectionDifference] = useState("0.00");
  const paymentBusyRef = useRef(false);
  const correctionBusyRef = useRef(false);
  const invoiceVoidBusyRef = useRef(false);
  const issueBusyRef = useRef(false);
  const recoveryBusyRef = useRef(false);
  const selected = invoices.data.find((item) => item.id === selectedId) || null;
  const selectedOriginalCorrection = corrections.data.find((item) =>
    item.original_invoice.id === selectedId) ?? null;
  const selectedReplacementCorrection = corrections.data.find((item) =>
    item.replacement_invoice?.id === selectedId) ?? null;
  const selectedCorrection = selectedOriginalCorrection ?? selectedReplacementCorrection;
  const hasIncompleteHistoricalFunds = Boolean(
    selected && selected.payment_status === "UNPAID" && selected.payments.length,
  );
  const invoiceMutationBusy = paymentBusy || correctionBusy || invoiceVoidBusy || issueBusy || Boolean(recoveryBusy);
  const correctionContextReady = !corrections.loading
    && !settlements.loading
    && !corrections.error
    && !settlements.error;

  useEffect(() => {
    setPaymentDifference("0.00");
  }, [selectedId]);

  useEffect(() => {
    const openCorrection = selectedOriginalCorrection?.status === "OPEN"
      ? selectedOriginalCorrection
      : null;
    setReplacementInvoiceId("");
    setRetainedAmounts(Object.fromEntries(
      openCorrection?.payments.map((payment) => [payment.id, payment.amount]) ?? [],
    ));
    setRefundAmounts(Object.fromEntries(
      openCorrection?.payments.map((payment) => [payment.id, "0.00"]) ?? [],
    ));
    setCorrectionDifference("0.00");
  }, [selectedOriginalCorrection?.id, selectedOriginalCorrection?.status]);
  const candidateState = useMemo(() => {
    const activeInvoices = invoices.data.filter((invoice) => ACTIVE_INVOICE_STATUSES.has(invoice.lifecycle_status));
    const claimedSettlementIds = new Set(activeInvoices.flatMap((invoice) =>
      invoice.settlement_ids?.length ? invoice.settlement_ids : [invoice.settlement_id]));
    const correctionSourceIds = new Set<number>();
    for (const correction of corrections.data.filter((item) => item.status === "OPEN")) {
      const original = invoices.data.find((invoice) => invoice.id === correction.original_invoice.id);
      for (const settlementId of original?.settlement_ids ?? []) correctionSourceIds.add(settlementId);
    }
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
      const availableSettlements = groupedSettlements.filter((settlement) =>
        !claimedSettlementIds.has(settlement.id) && !correctionSourceIds.has(settlement.id));
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
            scheme_name: line.scheme_name,
            start_date: line.start_date,
            closing_date: line.closing_date,
            service_fee: line.service_fee ?? "0.00",
          }))
        : [{
            id: -settlement.id,
            settlement_id: settlement.id,
            platform_name: settlement.platform_name,
            account_number: "历史组合",
            scheme_name: null,
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
  }, [settlements.data, invoices.data, corrections.data]);
  const selectedCandidate = candidateState.candidates.find((candidate) => candidate.key === candidateKey) ?? null;
  const pendingReplacementCorrection = useMemo(() => {
    if (!selected || selected.lifecycle_status !== "ISSUED") return null;
    const settlementById = new Map(settlements.data.map((settlement) => [settlement.id, settlement]));
    for (const correction of corrections.data) {
      if (correction.status !== "OPEN") continue;
      const original = invoices.data.find((invoice) => invoice.id === correction.original_invoice.id);
      if (!original
        || selected.id === original.id
        || selected.client_id !== original.client_id
        || selected.year !== original.year
        || selected.quarter !== original.quarter
        || selected.fee_plan_id !== original.fee_plan_id) continue;
      const originalSettlementIds = original.settlement_ids?.length
        ? original.settlement_ids
        : [original.settlement_id];
      const selectedSettlementIds = selected.settlement_ids?.length
        ? selected.settlement_ids
        : [selected.settlement_id];
      if (settlementSourcesFollowOriginal(originalSettlementIds, selectedSettlementIds, settlementById)) {
        return correction;
      }
    }
    return null;
  }, [corrections.data, invoices.data, selected, settlements.data]);
  const replacementCandidates = useMemo(() => {
    if (!selectedOriginalCorrection || selectedOriginalCorrection.status !== "OPEN") return [];
    const original = invoices.data.find((invoice) => invoice.id === selectedOriginalCorrection.original_invoice.id);
    if (!original) return [];
    const alreadyLinked = new Set(corrections.data.flatMap((correction) =>
      correction.replacement_invoice ? [correction.replacement_invoice.id] : []));
    const settlementById = new Map(settlements.data.map((settlement) => [settlement.id, settlement]));
    const originalSettlementIds = original.settlement_ids?.length
      ? original.settlement_ids
      : [original.settlement_id];
    return invoices.data.filter((invoice) =>
      invoice.id !== original.id
      && invoice.lifecycle_status === "ISSUED"
      && invoice.client_id === original.client_id
      && invoice.year === original.year
      && invoice.quarter === original.quarter
      && invoice.fee_plan_id === original.fee_plan_id
      && invoice.payment_status === "UNPAID"
      && !invoice.payments.length
      && moneyToCents(invoice.adjustment_amount) === 0
      && !alreadyLinked.has(invoice.id)
      && settlementSourcesFollowOriginal(
        originalSettlementIds,
        invoice.settlement_ids?.length ? invoice.settlement_ids : [invoice.settlement_id],
        settlementById,
      ));
  }, [corrections.data, invoices.data, selectedOriginalCorrection, settlements.data]);
  const selectedReplacementCandidate = replacementCandidates.find((invoice) =>
    invoice.id === Number(replacementInvoiceId)) ?? null;
  const retainedTotalCents = Object.values(retainedAmounts).reduce(
    (total, amount) => total + moneyToCents(amount), 0,
  );
  const correctionTargetBalanced = Boolean(
    selectedReplacementCandidate
    && retainedTotalCents + moneyToCents(correctionDifference) === moneyToCents(selectedReplacementCandidate.amount),
  );

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
    if (!selected || issueBusyRef.current || invoiceVoidBusyRef.current) return;
    const target = selected;
    const data = new FormData(event.currentTarget);
    issueBusyRef.current = true;
    try {
      setIssueBusy(true);
      setError("");
      await postJson<Invoice>(`/api/invoices/${target.id}/issue`, { issue_date: data.get("issue_date"), due_date: data.get("due_date") || null, language: data.get("language") });
      await invoices.reload();
      notify("Invoice已出具并锁定编号");
    } catch (err) {
      const message = err instanceof Error ? err.message : "出具失败";
      await invoices.reload();
      setError(message);
    } finally {
      issueBusyRef.current = false;
      setIssueBusy(false);
    }
  }

  async function addPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !correctionContextReady || pendingReplacementCorrection || paymentBusyRef.current || correctionBusyRef.current || invoiceVoidBusyRef.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    let uploadedProofId: number | null = null;
    paymentBusyRef.current = true;
    try {
      setError("");
      setPaymentBusy(true);
      const paymentDate = String(data.get("payment_date") || "").trim();
      const amount = String(data.get("amount") || "").trim();
      const companyDifference = String(data.get("company_difference") || "0.00").trim();
      const differenceReason = String(data.get("difference_reason") || "").trim();
      const amountCents = parseMoneyInput(amount);
      const differenceCents = parseMoneyInput(companyDifference);
      if (!paymentDate) throw new Error("必须填写Payment Date");
      if (amountCents == null || amountCents <= 0) throw new Error("实际现金必须为大于0且最多两位小数的金额");
      if (differenceCents == null || differenceCents < 0) throw new Error("公司承担差额必须为非负且最多两位小数的金额");
      if (amountCents + differenceCents !== moneyToCents(selected.amount)) {
        throw new Error(`实际现金与公司承担差额必须精确等于Invoice金额 HKD ${selected.amount}`);
      }
      if (differenceCents > 0 && !differenceReason) throw new Error("公司承担差额大于0时必须填写原因");
      const proof = data.get("proof");
      if (!(proof instanceof File) || !proof.size) throw new Error("必须选择付款凭证文件");
      const uploaded = await uploadUnclaimedProof(proof, "PAYMENT");
      uploadedProofId = uploaded.id;
      await postJson<Invoice>(`/api/invoices/${selected.id}/payments`, {
        payment_date: paymentDate,
        amount,
        method: String(data.get("method") || "BANK_TRANSFER"),
        proof_attachment_id: uploaded.id,
        company_difference: companyDifference,
        difference_reason: differenceReason || null,
        remark: String(data.get("remark") || "").trim() || null,
      });
      form.reset();
      setPaymentDifference("0.00");
      await Promise.all([invoices.reload(), corrections.reload()]);
      notify("付款已一次确认并锁定凭证");
    } catch (err) {
      const message = err instanceof Error ? err.message : "付款登记失败";
      if (uploadedProofId != null) {
        try {
          const refreshed = await api<Invoice>(`/api/invoices/${selected.id}`);
          if (refreshed.payments.some((payment) => payment.proof_attachment_id === uploadedProofId)) {
            form.reset();
            setPaymentDifference("0.00");
            await Promise.all([invoices.reload(), corrections.reload()]);
            notify("付款已一次确认并锁定凭证");
            return;
          }
        } catch {
          // Preserve the original error and the selected file for a safe retry.
        }
      }
      await Promise.all([invoices.reload(), corrections.reload()]);
      setError(message);
    } finally {
      paymentBusyRef.current = false;
      setPaymentBusy(false);
    }
  }

  async function startCorrection() {
    if (
      !selected
      || selected.lifecycle_status !== "ISSUED"
      || selectedOriginalCorrection
      || pendingReplacementCorrection
      || hasIncompleteHistoricalFunds
      || !correctionContextReady
      || correctionBusyRef.current
      || paymentBusyRef.current
      || invoiceVoidBusyRef.current
    ) return;
    const enteredReason = window.prompt("请输入Invoice更正原因。原编号、PDF及现金记录会永久保留：");
    const reason = enteredReason?.trim() ?? "";
    if (!reason) return;
    if (reason.length < 2 || reason.length > 500) {
      setError("更正原因必须为2至500个字符");
      return;
    }
    if (!window.confirm(
      `确认对 ${selected.invoice_number || `Invoice #${selected.id}`} 发起更正？\n\n` +
      "系统会作废原Invoice并冲回原现金分配；不会删除原PDF，也不会虚构新的现金收入。",
    )) return;
    correctionBusyRef.current = true;
    try {
      setCorrectionBusy(true);
      setError("");
      await postJson<InvoiceCorrection>(`/api/invoices/${selected.id}/corrections`, { reason });
      await Promise.all([invoices.reload(), corrections.reload(), settlements.reload()]);
      notify("Invoice更正已开启；请先按版本链更正Settlement，再建立替代Invoice");
    } catch (err) {
      const message = err instanceof Error ? err.message : "发起Invoice更正失败";
      try {
        const refreshed = await api<InvoiceCorrection[]>("/api/invoice-corrections");
        if (refreshed.some((correction) =>
          correction.original_invoice.id === selected.id && correction.reason === reason)) {
          await Promise.all([invoices.reload(), corrections.reload(), settlements.reload()]);
          notify("Invoice更正已开启；请先按版本链更正Settlement，再建立替代Invoice");
          return;
        }
      } catch {
        // Preserve the original mutation error when current state cannot be confirmed.
      }
      await Promise.all([invoices.reload(), corrections.reload(), settlements.reload()]);
      setError(message);
    } finally {
      correctionBusyRef.current = false;
      setCorrectionBusy(false);
    }
  }

  async function completeCorrection(event: FormEvent<HTMLFormElement>, correction: InvoiceCorrection) {
    event.preventDefault();
    if (!correctionContextReady || correctionBusyRef.current || paymentBusyRef.current || invoiceVoidBusyRef.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const replacementId = Number(data.get("replacement_invoice_id"));
    const replacement = replacementCandidates.find((invoice) => invoice.id === replacementId);
    if (!replacement) {
      setError("请选择已经出具且通过Settlement版本链校验的替代Invoice");
      return;
    }
    correctionBusyRef.current = true;
    try {
      setCorrectionBusy(true);
      setError("");
      const retainedAllocations: Array<{ payment_id: number; amount: string }> = [];
      const pendingRefunds: PendingRefund[] = [];
      let retainedTotalCents = 0;
      for (const payment of correction.payments) {
        const retained = String(data.get(`retained_${payment.id}`) || "0.00").trim();
        const refundAmount = String(data.get(`refund_${payment.id}`) || "0.00").trim();
        const retainedCents = parseMoneyInput(retained);
        const refundCents = parseMoneyInput(refundAmount);
        if (retainedCents == null || retainedCents < 0 || refundCents == null || refundCents < 0) {
          throw new Error(`Payment #${payment.id} 的留存分配与退款必须为非负且最多两位小数的金额`);
        }
        if (retainedCents + refundCents !== moneyToCents(payment.amount)) {
          throw new Error(`Payment #${payment.id} 的留存分配与退款必须精确等于本轮可处置现金 HKD ${payment.amount}`);
        }
        retainedTotalCents += retainedCents;
        retainedAllocations.push({ payment_id: payment.id, amount: retained });
        if (refundCents > 0) {
          const refundDate = String(data.get(`refund_date_${payment.id}`) || "").trim();
          const method = String(data.get(`refund_method_${payment.id}`) || "").trim();
          const reason = String(data.get(`refund_reason_${payment.id}`) || "").trim();
          const proof = data.get(`refund_proof_${payment.id}`);
          if (!refundDate) throw new Error(`Payment #${payment.id} 有退款金额时必须填写退款日期`);
          if (!method) throw new Error(`Payment #${payment.id} 有退款金额时必须选择退款方式`);
          if (reason.length < 2 || reason.length > 500) {
            throw new Error(`Payment #${payment.id} 的退款原因必须为2至500个字符`);
          }
          if (!(proof instanceof File) || !proof.size) {
            throw new Error(`Payment #${payment.id} 有退款金额时必须选择退款凭证`);
          }
          pendingRefunds.push({
            payment_id: payment.id,
            refund_date: refundDate,
            amount: refundAmount,
            method,
            reason,
            proof,
          });
        }
      }
      const companyDifference = String(data.get("company_difference") || "0.00").trim();
      const differenceReason = String(data.get("difference_reason") || "").trim();
      const differenceCents = parseMoneyInput(companyDifference);
      if (differenceCents == null || differenceCents < 0) {
        throw new Error("替代Invoice公司承担差额必须为非负且最多两位小数的金额");
      }
      if (!correction.payments.length && differenceCents !== 0) {
        throw new Error("原Invoice没有Payment时不能在更正完成步骤登记公司承担差额");
      }
      if (differenceCents > 0 && !differenceReason) throw new Error("公司承担差额大于0时必须填写原因");
      if (correction.payments.length && retainedTotalCents + differenceCents !== moneyToCents(replacement.amount)) {
        throw new Error(`留存现金与公司承担差额必须精确等于替代Invoice金额 HKD ${replacement.amount}`);
      }

      const refunds: RefundRequest[] = [];
      for (const pending of pendingRefunds) {
        const uploaded = await uploadUnclaimedProof(pending.proof, "PAYMENT_REFUND");
        refunds.push({
          payment_id: pending.payment_id,
          refund_date: pending.refund_date,
          amount: pending.amount,
          method: pending.method,
          reason: pending.reason,
          proof_attachment_id: uploaded.id,
        });
      }
      await postJson<InvoiceCorrection>(`/api/invoice-corrections/${correction.id}/complete`, {
        replacement_invoice_id: replacement.id,
        retained_allocations: retainedAllocations,
        refunds,
        company_difference: companyDifference,
        difference_reason: differenceReason || null,
      });
      form.reset();
      setReplacementInvoiceId("");
      setRetainedAmounts({});
      setRefundAmounts({});
      setCorrectionDifference("0.00");
      await Promise.all([invoices.reload(), corrections.reload(), settlements.reload()]);
      notify("Invoice更正已完成；留存现金、退款和差额均已锁定审计");
    } catch (err) {
      const message = err instanceof Error ? err.message : "完成Invoice更正失败";
      try {
        const refreshed = await api<InvoiceCorrection>(`/api/invoice-corrections/${correction.id}`);
        if (refreshed.status === "COMPLETED") {
          form.reset();
          setReplacementInvoiceId("");
          setRetainedAmounts({});
          setRefundAmounts({});
          setCorrectionDifference("0.00");
          await Promise.all([invoices.reload(), corrections.reload(), settlements.reload()]);
          notify("Invoice更正已完成；留存现金、退款和差额均已锁定审计");
          return;
        }
      } catch {
        // Preserve the original error and all selected refund files for retry.
      }
      await Promise.all([invoices.reload(), corrections.reload(), settlements.reload()]);
      setError(message);
    } finally {
      correctionBusyRef.current = false;
      setCorrectionBusy(false);
    }
  }

  async function voidInvoice() {
    if (!selected || (selected.lifecycle_status === "ISSUED" && !correctionContextReady)
      || invoiceVoidBusyRef.current || issueBusyRef.current || paymentBusyRef.current || correctionBusyRef.current) return;
    const target = selected;
    const enteredReason = window.prompt(target.lifecycle_status === "DRAFT" ? "请输入作废Draft的原因：" : "请输入作废原因。编号不会复用：");
    const reason = enteredReason?.trim() ?? "";
    if (!reason) return;
    if (reason.length < 2 || reason.length > 500) {
      setError("作废原因必须为2至500个字符");
      return;
    }
    const confirmed = window.confirm(target.lifecycle_status === "DRAFT"
      ? `最终确认作废 Draft #${target.id}？\n\nDraft会永久标记为VOID，但不会生成或占用Invoice编号。`
      : `最终确认作废 ${target.invoice_number || `Invoice #${target.id}`}？\n\n该操作不可撤销；原编号与归档PDF永久保留且编号不会复用。`);
    if (!confirmed) return;
    invoiceVoidBusyRef.current = true;
    try {
      setInvoiceVoidBusy(true);
      setError("");
      await postJson<Invoice>(`/api/invoices/${target.id}/void`, { reason });
      await Promise.all([invoices.reload(), corrections.reload()]);
      notify(target.invoice_number ? "Invoice已作废，原编号永久保留" : "Invoice Draft已作废，可按最新Settlement重新建立");
    } catch (err) {
      const message = err instanceof Error ? err.message : "作废失败";
      try {
        const refreshed = await api<Invoice>(`/api/invoices/${target.id}`);
        if (refreshed.lifecycle_status === "VOID" && refreshed.void_reason === reason) {
          await Promise.all([invoices.reload(), corrections.reload()]);
          notify(target.invoice_number ? "Invoice已作废，原编号永久保留" : "Invoice Draft已作废，可按最新Settlement重新建立");
          return;
        }
      } catch {
        // Preserve the original mutation error when current state cannot be confirmed.
      }
      await Promise.all([invoices.reload(), corrections.reload()]);
      setError(message);
    } finally {
      invoiceVoidBusyRef.current = false;
      setInvoiceVoidBusy(false);
    }
  }

  async function recoverIssuing(action: "COMPLETE" | "RETURN_TO_DRAFT") {
    if (!selected || selected.lifecycle_status !== "ISSUING" || recoveryBusyRef.current) return;
    recoveryBusyRef.current = true;
    try {
      setError("");
      setRecoveryBusy(action);
      await postJson<Invoice>(`/api/invoices/${selected.id}/recover-issuing`, { action });
      await invoices.reload();
      notify(action === "COMPLETE" ? "已核验归档文件并完成Invoice签发" : "Invoice已退回Draft；预留编号不会复用");
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复Invoice失败");
    } finally {
      recoveryBusyRef.current = false;
      setRecoveryBusy("");
    }
  }

  async function downloadInvoicePdf(language: "zh" | "en") {
    if (!selected?.invoice_number) return;
    try {
      setError("");
      await download(
        `/api/invoices/${selected.id}/pdf?language=${language}`,
        invoicePdfDownloadName(selected, language),
        { method: "POST" },
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invoice PDF下载失败");
    }
  }

  return (
    <>
      <PageHeader title="Invoice与收款" subtitle="按客户、季度和收费计划汇总已锁定的Sub Account收费；跨Platform只在Invoice阶段合并" />
      {error || invoices.error || settlements.error || corrections.error ? <ErrorBanner message={error || invoices.error || settlements.error || corrections.error} /> : null}
      <div className="split-layout invoices-top">
        <Panel title="建立Invoice Draft" subtitle="系统自动纳入该客户季度全部未占用的Finalized Settlement；不由前端传入金额或来源清单">
          <form className="form-grid" onSubmit={(e) => void createDraft(e)}>
            <Field group label="客户季度Invoice组合"><SearchableSelect label="客户季度Invoice组合" name="candidate_key" required disabled={invoices.loading || settlements.loading || !candidateState.candidates.length} value={selectedCandidate?.key ?? ""} onChange={setCandidateKey} options={candidateState.candidates.map((candidate) => ({ value: candidate.key, label: `${candidate.clientName} · ${candidate.year} Q${candidate.quarter} · ${candidate.feePlanName} · ${candidate.sourceCount}份Settlement · HKD ${moneyFromCents(candidate.totalCents)}` }))} /></Field>
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
          {selected ? <div className="invoice-summary"><header><ReceiptText /><div><strong>{selected.invoice_number || `Draft #${selected.id}`}</strong><span>{selected.client_name || "未识别Client"} · {selected.company_name || "未识别Company"} · {selected.year} Q{selected.quarter} · {selected.fee_plan_name || "未识别Fee Plan"}</span></div><InvoiceLifecycleBadge value={selected.lifecycle_status} /></header><div className="invoice-source-summary"><span><small>Settlement来源</small><strong>{selected.source_count} 份</strong></span><span><small>账户收费明细</small><strong>{selected.account_lines.length} 行</strong></span></div><div className="invoice-amount"><small>Service Fee合计</small><Money value={selected.amount} emphasis /></div><div className="invoice-balance"><span>计入本单现金 <Money value={selected.paid_amount} /></span><span>公司差额 <Money value={selected.adjustment_amount} /></span><span>未结 <Money value={selected.outstanding_amount} /></span><StatusBadge value={selected.payment_status} />{selected.is_overdue ? <span className="status status-overdue">已逾期</span> : null}</div>{selected.payments.length ? <div className="table-wrap compact-table payment-ledger"><table><thead><tr><th>Payment</th><th>Date / Method</th><th>计入本单现金</th><th>凭证</th></tr></thead><tbody>{selected.payments.map((payment) => <tr key={payment.id}><td>#{payment.id}</td><td>{payment.payment_date}<small className="cell-note">{payment.method}</small></td><td><Money value={payment.amount} /></td><td><a className="text-link" href={`/api/attachments/${payment.proof_attachment_id}/file`} target="_blank" rel="noreferrer" aria-label={`查看Payment #${payment.id}付款凭证`}>查看付款凭证</a></td></tr>)}</tbody></table></div> : null}<AccountLineTable lines={selected.account_lines} /></div> : <EmptyState title="尚未选择Invoice" detail="从下方列表选择一条记录。" />}
        </Panel>
      </div>

      {selected?.lifecycle_status === "DRAFT" ? <Panel title="正式出具" subtitle="Invoice编号在出具时生成：Company全名-中介人名字缩写-Issue Date-该Company与中介人的下一连续号；编号一经预留不会复用。"><form className="inline-form" onSubmit={(e) => void issue(e)}><Field label="Issue Date"><input name="issue_date" type="date" defaultValue={todayIso()} required disabled={invoiceMutationBusy} /></Field><Field label="Due Date" hint="留空则采用Company默认天数"><input name="due_date" type="date" disabled={invoiceMutationBusy} /></Field><Field label="Language"><select name="language" defaultValue={selected.language} disabled={invoiceMutationBusy}><option value="zh">中文</option><option value="en">English</option></select></Field><button className="primary" type="submit" disabled={invoiceMutationBusy}>{issueBusy ? "正在出具..." : "Issued并分配编号"}</button><button className="danger" type="button" disabled={invoiceMutationBusy} onClick={() => void voidInvoice()}><Ban size={17} />{invoiceVoidBusy ? "作废处理中..." : "作废Draft"}</button></form><small className="cell-note">请先核对：Company全名为“{selected.company_name || "未识别"}”，中介人为“{selected.fc_name || "未识别"}”，Fee Plan为“{selected.fee_plan_name || "未识别"}”，并逐行核对Platform、Sub Account及当前Scheme。若Draft建立后同组Settlement发生变化，服务端会拒绝漏项签发。</small></Panel> : null}
      {selected?.lifecycle_status === "ISSUING" ? <Panel title="恢复出具中的Invoice" subtitle="系统在预留编号后曾中断。请依据归档文件完整性完成签发，或退回Draft重新出具；已预留编号永久保留且不会复用。"><div className="invoice-recovery"><div><strong>{!selected.issue_recovery ? "恢复状态尚未就绪" : selected.issue_recovery.files_complete ? "中英文归档文件完整" : "归档文件不完整"}</strong><span>{!selected.issue_recovery ? "请刷新Invoice清单；恢复状态可用前不会开放任何操作。" : selected.issue_recovery.files_complete ? "可以完成签发，也可以退回Draft重新核对。" : "不能直接完成签发，请退回Draft后重新生成两份归档PDF。"}</span></div><div className="invoice-actions"><button className="primary" type="button" disabled={Boolean(recoveryBusy) || !selected.issue_recovery?.can_complete} onClick={() => void recoverIssuing("COMPLETE")}><CheckCircle2 size={17} />{recoveryBusy === "COMPLETE" ? "正在核验..." : "完成签发"}</button><button className="secondary" type="button" disabled={Boolean(recoveryBusy) || !selected.issue_recovery?.can_return_to_draft} onClick={() => void recoverIssuing("RETURN_TO_DRAFT")}><RotateCcw size={17} />{recoveryBusy === "RETURN_TO_DRAFT" ? "正在退回..." : "退回Draft"}</button></div></div></Panel> : null}
      {selected?.lifecycle_status === "ISSUED" ? <Panel title="PDF、付款与更正" subtitle="新付款只允许一次完整确认；实际现金与人工确认的公司承担差额必须精确结清Invoice，付款凭证是硬前置。">
        <div className="invoice-actions"><button className="secondary" type="button" onClick={() => void downloadInvoicePdf("zh")}><FileDown size={17} />中文PDF</button><button className="secondary" type="button" onClick={() => void downloadInvoicePdf("en")}><FileDown size={17} />English PDF</button><button className="danger" type="button" onClick={() => void startCorrection()} disabled={invoiceMutationBusy || !correctionContextReady || Boolean(selectedOriginalCorrection) || Boolean(pendingReplacementCorrection) || hasIncompleteHistoricalFunds} title={selectedOriginalCorrection ? "该Invoice已经作为原单发起过更正" : pendingReplacementCorrection ? `请先完成Invoice更正 #${pendingReplacementCorrection.id}` : hasIncompleteHistoricalFunds ? "历史资金台账未完整平账，必须先人工核对" : undefined}><RotateCcw size={17} />{correctionBusy ? "更正处理中..." : "发起更正"}</button>{!selected.payments.length && !selectedCorrection ? <button className="ghost" type="button" onClick={() => void voidInvoice()} disabled={invoiceMutationBusy || !correctionContextReady}><Ban size={17} />{invoiceVoidBusy ? "作废处理中..." : "直接作废（无替代）"}</button> : null}</div>
        {!correctionContextReady ? <div className="invoice-candidate-warning pending-replacement-warning">更正记录或Settlement版本链尚未读取完成，付款、更正和直接作废暂时停用；请等待读取完成或处理上方错误。</div> : pendingReplacementCorrection ? <div className="invoice-candidate-warning pending-replacement-warning">该Invoice已通过更正 #{pendingReplacementCorrection.id} 的Settlement版本链匹配，是待关联替代单。完成上一更正前不得登记新Payment或再次发起更正；如替代单有误且尚无资金，可直接作废后重建。</div> : selected.payment_status === "UNPAID" && !selected.payments.length ? <form className="inline-form payment-confirmation-form" onSubmit={(e) => void addPayment(e)}>
          <Field label="Payment Date"><input name="payment_date" type="date" defaultValue={todayIso()} required disabled={paymentBusy} /></Field>
          <Field label="实际现金 (HKD)"><input name="amount" type="number" min="0.01" step="0.01" required disabled={paymentBusy} /></Field>
          <Field label="公司承担差额 (HKD)" hint={`实际现金＋差额必须等于 HKD ${selected.amount}`}><input name="company_difference" type="number" min="0" step="0.01" value={paymentDifference} onChange={(event) => setPaymentDifference(event.target.value)} required disabled={paymentBusy} /></Field>
          {moneyToCents(paymentDifference) > 0 ? <Field label="差额原因" hint="差额大于0时必填"><input name="difference_reason" maxLength={500} required disabled={paymentBusy} /></Field> : null}
          <Field label="Method"><select name="method" defaultValue="BANK_TRANSFER" disabled={paymentBusy}><option value="BANK_TRANSFER">Bank Transfer</option><option value="CHEQUE">Cheque</option><option value="OTHER">Other</option></select></Field>
          <Field label="付款凭证"><input name="proof" type="file" accept=".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv" required disabled={paymentBusy} /></Field>
          <Field label="Remark"><input name="remark" disabled={paymentBusy} /></Field>
          <button className="primary" type="submit" disabled={invoiceMutationBusy}>{paymentBusy ? "正在核验并锁定..." : "确认已付款"}</button>
        </form> : <div className="invoice-candidate-warning">{selected.payment_status === "PAID" ? "该Invoice已结清。Payment、现金分配、公司差额及凭证均已锁定；如发生错单，请使用受控更正流程。" : "该Invoice已有历史资金台账但未完整结清，不能追加或部分付款；请先人工核对台账。"}</div>}
      </Panel> : null}
      {selected?.lifecycle_status === "VOID" ? <Panel title="作废Invoice档案" subtitle={`作废原因：${selected.void_reason || "未记录"}`}>{selected.invoice_number ? <div className="invoice-actions"><button className="secondary" onClick={() => void downloadInvoicePdf("zh")}><FileDown size={17} />原中文PDF</button><button className="secondary" onClick={() => void downloadInvoicePdf("en")}><FileDown size={17} />Original English PDF</button></div> : <EmptyState title="Draft在签发前已作废" detail="该记录未分配Invoice编号，因此没有正式归档PDF。" />}</Panel> : null}

      {selectedCorrection ? <Panel title={`Invoice更正 #${selectedCorrection.id}`} subtitle={`${selectedOriginalCorrection ? "当前Invoice作为本轮原单" : "当前Invoice作为上一轮替代单"} · 状态：${selectedCorrection.status === "OPEN" ? "处理中" : "已完成"} · 原因：${selectedCorrection.reason}`}>
        {selectedOriginalCorrection && selectedReplacementCorrection ? <div className="correction-continuity">连续更正：当前Invoice曾是更正 #{selectedReplacementCorrection.id} 的替代单，现在又是更正 #{selectedOriginalCorrection.id} 的原单；两轮关系均保留。</div> : null}
        <div className="correction-chain">
          <span><small>原Invoice</small><strong>{selectedCorrection.original_invoice.invoice_number || `#${selectedCorrection.original_invoice.id}`}</strong><StatusBadge value={selectedCorrection.original_invoice.lifecycle_status} /></span>
          <b>→</b>
          <span><small>替代Invoice</small><strong>{selectedCorrection.replacement_invoice?.invoice_number || "等待建立并出具"}</strong>{selectedCorrection.replacement_invoice ? <StatusBadge value={selectedCorrection.replacement_invoice.lifecycle_status} /> : null}</span>
        </div>
        {selectedCorrection.status === "OPEN" ? <form className="correction-form" onSubmit={(event) => void completeCorrection(event, selectedCorrection)}>
          <Field label="替代Invoice"><select name="replacement_invoice_id" required value={replacementInvoiceId} onChange={(event) => setReplacementInvoiceId(event.target.value)} disabled={correctionBusy || !replacementCandidates.length}><option value="" disabled>请选择通过完整Settlement版本链校验的同组Issued Invoice</option>{replacementCandidates.map((invoice) => <option key={invoice.id} value={invoice.id}>{invoice.invoice_number || `Invoice #${invoice.id}`} · HKD {invoice.amount}</option>)}</select></Field>
          {!replacementCandidates.length ? <div className="invoice-candidate-warning">请先在Settlement页按版本链作废并重建全部相关Settlement，再建立并出具空白替代Invoice。已有资金、差额、其他更正占用或版本链不完整的Invoice不会出现在候选中。</div> : null}
          {selectedCorrection.payments.length ? <>
            <div className="correction-payment-heading">逐笔处理本轮可处置现金；每笔必须满足“留存分配＋退款＝本轮可处置现金”。只有退款金额大于0时才要求退款日期、方式、原因及实体凭证。</div>
            {selectedCorrection.payments.map((payment) => {
              const retained = retainedAmounts[payment.id] ?? payment.amount;
              const refund = refundAmounts[payment.id] ?? "0.00";
              const hasRefund = moneyToCents(refund) > 0;
              const paymentBalanced = moneyToCents(retained) + moneyToCents(refund) === moneyToCents(payment.amount);
              const paymentLabelId = `correction-payment-${selectedCorrection.id}-${payment.id}`;
              return <div className="correction-payment" key={payment.id} role="group" aria-labelledby={paymentLabelId}>
                <div id={paymentLabelId}><strong>Payment #{payment.id}</strong><span>{payment.payment_date} · {payment.method}</span><small>本轮可处置现金</small><Money value={payment.amount} /></div>
                <Field label="留存分配到替代Invoice"><input name={`retained_${payment.id}`} type="number" min="0" max={payment.amount} step="0.01" value={retained} onChange={(event) => setRetainedAmounts((current) => ({ ...current, [payment.id]: event.target.value }))} required disabled={correctionBusy} /></Field>
                <Field label="退款金额"><input name={`refund_${payment.id}`} type="number" min="0" max={payment.amount} step="0.01" value={refund} onChange={(event) => setRefundAmounts((current) => ({ ...current, [payment.id]: event.target.value }))} required disabled={correctionBusy} /></Field>
                <div className={`correction-balance-note ${paymentBalanced ? "balanced" : "unbalanced"}`} role="status">{paymentBalanced ? "本笔现金守恒" : `留存＋退款须等于 HKD ${payment.amount}`}</div>
                {hasRefund ? <div className="correction-refund-fields">
                  <Field label="退款日期"><input name={`refund_date_${payment.id}`} type="date" defaultValue={todayIso()} required disabled={correctionBusy} /></Field>
                  <Field label="退款方式"><select name={`refund_method_${payment.id}`} defaultValue="BANK_TRANSFER" required disabled={correctionBusy}><option value="BANK_TRANSFER">Bank Transfer</option><option value="CHEQUE">Cheque</option><option value="OTHER">Other</option></select></Field>
                  <Field label="退款原因"><input name={`refund_reason_${payment.id}`} minLength={2} maxLength={500} required disabled={correctionBusy} /></Field>
                  <Field label="退款凭证"><input name={`refund_proof_${payment.id}`} type="file" accept=".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv" required disabled={correctionBusy} /></Field>
                </div> : null}
              </div>;
            })}
            <div className="correction-adjustment">
              <Field label="替代Invoice公司承担差额"><input name="company_difference" type="number" min="0" step="0.01" value={correctionDifference} onChange={(event) => setCorrectionDifference(event.target.value)} required disabled={correctionBusy} /></Field>
              {moneyToCents(correctionDifference) > 0 ? <Field label="差额原因"><input name="difference_reason" maxLength={500} required disabled={correctionBusy} /></Field> : null}
            </div>
            {selectedReplacementCandidate ? <div className={`correction-balance-note correction-target-balance ${correctionTargetBalanced ? "balanced" : "unbalanced"}`} role="status">{correctionTargetBalanced ? "留存现金与公司差额已精确覆盖替代Invoice" : `留存现金＋公司差额须等于替代Invoice HKD ${selectedReplacementCandidate.amount}`}</div> : null}
          </> : <div className="invoice-candidate-warning">原Invoice没有现金记录；完成后只建立旧新Invoice替代关系，新Invoice仍保持未付款。</div>}
          <button className="primary" type="submit" disabled={correctionBusy || !correctionContextReady || !replacementInvoiceId}><CheckCircle2 size={17} />{correctionBusy ? "正在核验守恒..." : "完成更正并锁定"}</button>
        </form> : <>
          <div className="correction-ledger">
            <span>现金分配 {selectedCorrection.allocations.filter((item) => item.entry_type === "APPLY").length} 条</span>
            <span>冲正 {selectedCorrection.allocations.filter((item) => item.entry_type === "REVERSAL").length} 条</span>
            <span>退款 {selectedCorrection.refunds.length} 条</span>
            <span>公司差额 {selectedCorrection.adjustments.length} 条</span>
          </div>
          {selectedCorrection.allocations.length ? <div className="table-wrap correction-record-table"><table><thead><tr><th>Payment</th><th>Entry</th><th>Invoice</th><th>金额</th></tr></thead><tbody>{selectedCorrection.allocations.map((allocation) => <tr key={allocation.id}><td>#{allocation.payment_id}</td><td>{allocation.entry_type === "APPLY" ? "留存分配" : "冲正"}</td><td>#{allocation.invoice_id}</td><td><Money value={allocation.amount} /></td></tr>)}</tbody></table></div> : null}
          {selectedCorrection.refunds.length ? <div className="table-wrap correction-record-table"><table><thead><tr><th>Payment</th><th>退款日期 / 方式</th><th>金额 / 原因</th><th>凭证</th></tr></thead><tbody>{selectedCorrection.refunds.map((refund) => <tr key={refund.id}><td>#{refund.payment_id}</td><td>{refund.refund_date}<small className="cell-note">{refund.method}</small></td><td><Money value={refund.amount} /><small className="cell-note">{refund.reason}</small></td><td><a className="text-link" href={`/api/attachments/${refund.proof_attachment_id}/file`} target="_blank" rel="noreferrer" aria-label={`查看Payment #${refund.payment_id}退款凭证`}>查看退款凭证</a></td></tr>)}</tbody></table></div> : null}
          {selectedCorrection.adjustments.length ? <div className="table-wrap correction-record-table"><table><thead><tr><th>Invoice</th><th>差额类型</th><th>金额</th><th>原因</th></tr></thead><tbody>{selectedCorrection.adjustments.map((adjustment) => <tr key={adjustment.id}><td>#{adjustment.invoice_id}</td><td>公司承担差额</td><td><Money value={adjustment.amount} /></td><td>{adjustment.reason}</td></tr>)}</tbody></table></div> : null}
        </>}
      </Panel> : null}

      <Panel title="Invoice更正清单" subtitle="OPEN记录可直接进入原Invoice完成更正；连续更正的每一轮独立保留，不需要手工输入Correction或Payment ID。">{corrections.loading ? <Loading /> : corrections.data.length ? <div className="table-wrap"><table><thead><tr><th>Correction</th><th>原Invoice</th><th>替代Invoice</th><th>现金处理</th><th>Status</th><th>操作</th></tr></thead><tbody>{corrections.data.map((correction) => <tr key={correction.id}><td>#{correction.id}<small className="cell-note">{correction.opened_at.slice(0, 10)}</small></td><td>{correction.original_invoice.invoice_number || `#${correction.original_invoice.id}`}</td><td>{correction.replacement_invoice?.invoice_number || "等待替代单"}</td><td>{correction.payments.length}笔可处置Payment<small className="cell-note">{correction.refunds.length}笔退款 · {correction.adjustments.length}笔差额</small></td><td><span className={`status ${correction.status === "OPEN" ? "status-draft" : "status-confirmed"}`}>{correction.status === "OPEN" ? "处理中" : "已完成"}</span></td><td><button className="ghost correction-open-button" type="button" disabled={invoiceMutationBusy} onClick={() => setSelectedId(correction.original_invoice.id)}>{correction.status === "OPEN" ? "继续处理" : "查看记录"}</button></td></tr>)}</tbody></table></div> : <EmptyState title="暂无Invoice更正" detail="只有受控错单更正会出现在这里。" />}</Panel>

      <Panel title="Invoice清单"><div className="list-search"><Field label="搜索Invoice客户或编号"><input type="search" value={invoiceSearch} onChange={(event) => setInvoiceSearch(event.target.value)} placeholder="输入客户名称或账单编号…" /></Field><small role="status">显示 {visibleInvoices.length} / {invoices.data.length} 张Invoice</small></div>{invoices.loading ? <Loading /> : invoices.error ? null : visibleInvoices.length ? <div className="table-wrap"><table><thead><tr><th>Invoice No.</th><th>Client / Period</th><th>来源</th><th>Issue / Due</th><th>Amount</th><th>Payment</th><th>Lifecycle</th></tr></thead><tbody>{visibleInvoices.map((item) => <tr key={item.id} className={selectedId === item.id ? "selected clickable" : "clickable"} onClick={() => { if (!invoiceMutationBusy) setSelectedId(item.id); }} onKeyDown={(event) => { if (!invoiceMutationBusy && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); setSelectedId(item.id); } }} role="button" tabIndex={invoiceMutationBusy ? -1 : 0} aria-selected={selectedId === item.id} aria-label={`查看${item.invoice_number || `Draft #${item.id}`}`}><td><strong>{item.invoice_number || `Draft #${item.id}`}</strong></td><td>{item.client_name || "未识别Client"}<small className="cell-note">{item.year} Q{item.quarter} · {item.fc_name || "未识别FC"}</small></td><td>{item.source_count}份Settlement<small className="cell-note">{item.account_lines.length}行账户明细</small></td><td>{item.issue_date || "-"}<small className="cell-note">Due {item.due_date || "-"}</small></td><td><Money value={item.amount} /></td><td><StatusBadge value={item.payment_status} />{item.is_overdue ? <small className="cell-note overdue-note">已逾期</small> : null}</td><td><InvoiceLifecycleBadge value={item.lifecycle_status} /></td></tr>)}</tbody></table></div> : <EmptyState title={invoiceSearch.trim() ? "未找到匹配的Invoice" : "暂无Invoice"} detail={invoiceSearch.trim() ? "请更换客户名称、编号或清空搜索。" : "先Finalized一个产生Service Fee的客户季度组合。"} />}</Panel>
    </>
  );
}
