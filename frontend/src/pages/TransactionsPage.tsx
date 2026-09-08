import SearchableSelect from "../SearchableSelect";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { api, patchJson, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Money, PageHeader, Panel } from "../components";
import { todayIso, useApiList } from "../hooks";
import { useFormAction } from "../useFormAction";
import { accountIdentityLabel } from "../types";
import type { Account, BalanceSnapshot } from "../types";

type Transaction = { id: number; account_id: number; account_number: string; transaction_date: string; transaction_type: string; amount: string; remark?: string; evidence_complete: boolean; evidence_count: number; correction_allowed: boolean; locked_settlement_id: number | null };
type Attachment = { id: number; entity_type: string; entity_id: number; original_name: string; size_bytes: number; created_at: string; duplicate?: boolean };

export default function TransactionsPage({ notify }: { notify: (message: string) => void }) {
  const accounts = useApiList<Account>("/api/accounts");
  const transactions = useApiList<Transaction>("/api/transactions");
  const snapshots = useApiList<BalanceSnapshot>("/api/balance-snapshots");
  const attachments = useApiList<Attachment>("/api/attachments");
  const [proofType, setProofType] = useState<"SNAPSHOT" | "TRANSACTION">("SNAPSHOT");
  const [proofTargetId, setProofTargetId] = useState("");
  const [filterClientId, setFilterClientId] = useState("");
  const [filterPlatformId, setFilterPlatformId] = useState("");
  const [filterAccountId, setFilterAccountId] = useState("");
  const [localError, setLocalError] = useState("");
  const attachmentAction = useFormAction(setLocalError, notify);
  const [transactionBusy, setTransactionBusy] = useState(false);
  const [snapshotBusy, setSnapshotBusy] = useState(false);
  const [editingTransaction, setEditingTransaction] = useState<Transaction | null>(null);
  const [correctionBusy, setCorrectionBusy] = useState(false);
  const transactionBusyRef = useRef(false);
  const snapshotBusyRef = useRef(false);
  const error = localError || accounts.error || transactions.error || snapshots.error || attachments.error;
  const accountById = useMemo(() => new Map(accounts.data.map((item) => [item.id, item])), [accounts.data]);
  const clientOptions = useMemo(() => Array.from(new Map(accounts.data.map((item) => [item.client_id, item.client_name])).entries())
    .map(([id, name]) => ({ id, name })).sort((left, right) => left.name.localeCompare(right.name, "zh-Hans-CN")), [accounts.data]);
  const platformOptions = useMemo(() => Array.from(new Map(accounts.data.filter((item) => item.platform_id != null).map((item) => [item.platform_id as number, item.platform_name || "待确认Platform"])).entries())
    .map(([id, name]) => ({ id, name })).sort((left, right) => left.name.localeCompare(right.name, "zh-Hans-CN")), [accounts.data]);
  const filteredAccounts = useMemo(() => accounts.data.filter((item) =>
    (!filterClientId || item.client_id === Number(filterClientId))
    && (!filterPlatformId || item.platform_id === Number(filterPlatformId))
    && (!filterAccountId || item.id === Number(filterAccountId))), [accounts.data, filterAccountId, filterClientId, filterPlatformId]);
  const filteredAccountIds = useMemo(() => new Set(filteredAccounts.map((item) => item.id)), [filteredAccounts]);
  const visibleTransactions = useMemo(() => transactions.data.filter((item) => filteredAccountIds.has(item.account_id)), [filteredAccountIds, transactions.data]);
  const visibleSnapshots = useMemo(() => snapshots.data.filter((item) => filteredAccountIds.has(item.account_id)), [filteredAccountIds, snapshots.data]);
  const visibleTransactionIds = useMemo(() => new Set(visibleTransactions.map((item) => item.id)), [visibleTransactions]);
  const visibleSnapshotIds = useMemo(() => new Set(visibleSnapshots.map((item) => item.id)), [visibleSnapshots]);
  const visibleAttachments = useMemo(() => attachments.data.filter((item) =>
    (item.entity_type === "SNAPSHOT" && visibleSnapshotIds.has(item.entity_id))
    || (item.entity_type === "TRANSACTION" && visibleTransactionIds.has(item.entity_id))
    || (item.entity_type === "ACCOUNT" && filteredAccountIds.has(item.entity_id))), [attachments.data, filteredAccountIds, visibleSnapshotIds, visibleTransactionIds]);
  const proofTargets = useMemo(() => proofType === "SNAPSHOT"
    ? visibleSnapshots.map((item) => ({ id: item.id, label: `${accountById.has(item.account_id) ? accountIdentityLabel(accountById.get(item.account_id)!) : item.account_number} · ${item.as_of_date} · HKD ${item.total_balance}` }))
    : visibleTransactions.map((item) => ({ id: item.id, label: `${accountById.has(item.account_id) ? accountIdentityLabel(accountById.get(item.account_id)!) : item.account_number} · ${item.transaction_date} · ${item.transaction_type} · HKD ${item.amount}` })), [accountById, proofType, visibleSnapshots, visibleTransactions]);

  useEffect(() => {
    if (proofTargetId && !proofTargets.some((item) => item.id === Number(proofTargetId))) {
      setProofTargetId("");
    }
  }, [proofTargetId, proofTargets]);

  async function submitTransaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (transactionBusyRef.current) return;
    transactionBusyRef.current = true;
    setTransactionBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    setLocalError("");
    try {
      await postJson("/api/transactions", {
        account_id: Number(data.get("account_id")), transaction_date: data.get("date"),
        transaction_type: data.get("type"), amount: data.get("amount"), remark: data.get("remark") || null,
      });
      form.reset();
      await transactions.reload();
      notify("资金流水已保存；请补交该流水凭证后再Finalized");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "资金流水保存失败");
    } finally {
      transactionBusyRef.current = false;
      setTransactionBusy(false);
    }
  }

  async function submitSnapshot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (snapshotBusyRef.current) return;
    snapshotBusyRef.current = true;
    setSnapshotBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    setLocalError("");
    try {
      await postJson("/api/balance-snapshots", {
        account_id: Number(data.get("account_id")), as_of_date: data.get("date"), total_balance: data.get("balance"),
        eligible_for_closing: data.get("eligible") === "on", remark: data.get("remark") || null,
      });
      form.reset();
      await snapshots.reload();
      notify("余额快照已保存；请补交快照凭证后再Finalized");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "余额快照保存失败");
    } finally {
      snapshotBusyRef.current = false;
      setSnapshotBusy(false);
    }
  }

  async function submitTransactionCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingTransaction || correctionBusy) return;
    setCorrectionBusy(true);
    setLocalError("");
    const data = new FormData(event.currentTarget);
    try {
      await patchJson(`/api/transactions/${editingTransaction.id}`, {
        transaction_date: data.get("date"), transaction_type: data.get("type"),
        amount: data.get("amount"), remark: data.get("remark") || null,
        correction_reason: data.get("correction_reason"),
      });
      setEditingTransaction(null);
      await transactions.reload();
      notify("资金流水已更正；原凭证继续保留，更正前后内容及原因已写入审计");
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : "资金流水更正失败");
    } finally {
      setCorrectionBusy(false);
    }
  }

  function submitAttachment(event: FormEvent<HTMLFormElement>) {
    return attachmentAction.submit(event, {
      save: (data) => {
        data.set("entity_type", proofType);
        data.set("entity_id", proofTargetId);
        return api<Attachment>("/api/attachments", { method: "POST", body: data });
      },
      afterSave: () => setProofTargetId(""),
      refresh: () => Promise.all([attachments.reload(), transactions.reload(), snapshots.reload()]),
      message: "凭证已关联到具体记录并安全归档",
    });
  }

  function attachmentTarget(item: Attachment): string {
    if (item.entity_type === "SNAPSHOT") {
      const snapshot = snapshots.data.find((candidate) => candidate.id === item.entity_id);
      const account = snapshot ? accountById.get(snapshot.account_id) : null;
      return snapshot ? `${account ? accountIdentityLabel(account) : snapshot.account_number} · ${snapshot.as_of_date}` : `Snapshot #${item.entity_id}`;
    }
    if (item.entity_type === "TRANSACTION") {
      const transaction = transactions.data.find((candidate) => candidate.id === item.entity_id);
      const account = transaction ? accountById.get(transaction.account_id) : null;
      return transaction ? `${account ? accountIdentityLabel(account) : transaction.account_number} · ${transaction.transaction_date}` : `Transaction #${item.entity_id}`;
    }
    if (item.entity_type === "ACCOUNT") {
      const account = accountById.get(item.entity_id);
      return account ? `${accountIdentityLabel(account)} · 历史账户级凭证` : `Account #${item.entity_id}`;
    }
    return `${item.entity_type} #${item.entity_id}`;
  }

  function snapshotHoldings(snapshot: BalanceSnapshot) {
    return snapshot.holdings.map((holding, index) => {
      const fund = String(holding.fund_name || `持仓 #${index + 1}`);
      const marketValue = holding.market_value == null || holding.market_value === "" ? "" : ` · HKD ${String(holding.market_value)}`;
      return <span className="cell-note" key={`${fund}-${index}`}>{fund}{marketValue}</span>;
    });
  }

  return (
    <>
      <PageHeader title="资金与余额" subtitle="Draft允许先保存；Beginning、Closing、Contribution和Withdrawal缺少凭证时不能Finalized" />
      {error ? <ErrorBanner message={error} /> : null}
      <div className="split-layout">
        <Panel title="新增资金流水" subtitle="日期请按公司确认的实际入账／基金分配口径填写，不要填写供款所属月份的截止日"><form className="form-grid" onSubmit={(e) => void submitTransaction(e)}><Field label="Sub Account"><select name="account_id" required defaultValue="" disabled={transactionBusy}><option value="" disabled>请选择</option>{accounts.data.map((x) => <option key={x.id} value={x.id}>{accountIdentityLabel(x)}</option>)}</select></Field><Field label="资金生效日期"><input name="date" type="date" defaultValue={todayIso()} required disabled={transactionBusy} /></Field><Field label="Type"><select name="type" defaultValue="CONTRIBUTION" disabled={transactionBusy}><option value="CONTRIBUTION">Contribution 加款</option><option value="WITHDRAWAL">Withdrawal 提款</option></select></Field><Field label="Amount (HKD)"><input name="amount" type="number" min="0.01" step="0.01" required disabled={transactionBusy} /></Field><Field label="Remark"><textarea name="remark" rows={2} disabled={transactionBusy} /></Field><button className="primary" type="submit" disabled={transactionBusy}>{transactionBusy ? "保存处理中..." : "保存流水"}</button></form></Panel>
        <Panel title="手工余额快照" subtitle="非季末且非退出日不可作为Closing"><form className="form-grid" onSubmit={(e) => void submitSnapshot(e)}><Field label="Sub Account"><select name="account_id" required defaultValue="" disabled={snapshotBusy}><option value="" disabled>请选择</option>{accounts.data.map((x) => <option key={x.id} value={x.id}>{accountIdentityLabel(x)}</option>)}</select></Field><Field label="As-of Date"><input name="date" type="date" defaultValue={todayIso()} required disabled={snapshotBusy} /></Field><Field label="Total Balance (HKD)"><input name="balance" type="number" min="0" step="0.01" required disabled={snapshotBusy} /></Field><label className="check-field"><input name="eligible" type="checkbox" disabled={snapshotBusy} /> 季末或实际退出日，可作为Closing</label><Field label="Remark"><textarea name="remark" rows={2} disabled={snapshotBusy} /></Field><button className="secondary" type="submit" disabled={snapshotBusy}>{snapshotBusy ? "保存处理中..." : "保存快照"}</button></form></Panel>
      </div>
      {editingTransaction ? <Panel title={`更正资金流水 #${editingTransaction.id}`} subtitle="只更正尚未进入Finalized结算的记录；原凭证不会删除，系统会保留更正前后内容和原因"><form className="form-grid" key={editingTransaction.id} onSubmit={(event) => void submitTransactionCorrection(event)}><Field label="资金生效日期"><input name="date" type="date" defaultValue={editingTransaction.transaction_date} required disabled={correctionBusy} /></Field><Field label="Type"><select name="type" defaultValue={editingTransaction.transaction_type} disabled={correctionBusy}><option value="CONTRIBUTION">Contribution 加款</option><option value="WITHDRAWAL">Withdrawal 提款</option></select></Field><Field label="Amount (HKD)"><input name="amount" type="number" min="0.01" step="0.01" defaultValue={editingTransaction.amount} required disabled={correctionBusy} /></Field><Field label="Remark"><textarea name="remark" rows={2} defaultValue={editingTransaction.remark || ""} disabled={correctionBusy} /></Field><Field label="更正原因"><textarea name="correction_reason" rows={2} minLength={2} maxLength={500} required disabled={correctionBusy} placeholder="例如：原记录误用了供款月份截止日，现按实际资金生效日期更正" /></Field><div className="form-actions"><button className="primary" type="submit" disabled={correctionBusy}>{correctionBusy ? "保存更正中..." : "保存更正"}</button><button className="ghost" type="button" disabled={correctionBusy} onClick={() => setEditingTransaction(null)}>取消</button></div></form></Panel> : null}
      <Panel title="账户数据筛选" subtitle="以下筛选同时作用于资金流水、余额快照及凭证关联记录">
        <div className="settlement-controls transaction-filters">
          <Field group label="Client"><SearchableSelect label="资金与余额客户" value={filterClientId} onChange={(value) => { setFilterClientId(value); setFilterAccountId(""); setProofTargetId(""); }} placeholder="全部客户" options={clientOptions.map((item) => ({ value: String(item.id), label: item.name }))} /></Field>
          <Field label="Platform"><select value={filterPlatformId} onChange={(event) => { setFilterPlatformId(event.target.value); setFilterAccountId(""); setProofTargetId(""); }}><option value="">全部Platform</option>{platformOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
          <Field label="Sub Account"><select value={filterAccountId} onChange={(event) => { setFilterAccountId(event.target.value); setProofTargetId(""); }}><option value="">全部账户</option>{accounts.data.filter((item) => (!filterClientId || item.client_id === Number(filterClientId)) && (!filterPlatformId || item.platform_id === Number(filterPlatformId))).map((item) => <option key={item.id} value={item.id}>{accountIdentityLabel(item)}</option>)}</select></Field>
        </div>
      </Panel>
      <Panel title="结算凭证归档" subtitle="凭证必须关联到具体Snapshot或资金流水；切换类型或筛选会清空关联目标，避免错挂"><form className="inline-form evidence-form" onSubmit={(e) => void submitAttachment(e)}><Field label="凭证类型"><select value={proofType} onChange={(e) => { setProofType(e.target.value as "SNAPSHOT" | "TRANSACTION"); setProofTargetId(""); }}><option value="SNAPSHOT">Balance Snapshot</option><option value="TRANSACTION">Contribution / Withdrawal</option></select></Field><Field label="关联记录"><select name="entity_id" required value={proofTargetId} onChange={(event) => setProofTargetId(event.target.value)}><option value="" disabled>请选择具体记录</option>{proofTargets.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></Field><Field label="凭证文件"><input name="file" type="file" accept=".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv" required /></Field><button className="secondary" type="submit" disabled={attachmentAction.pending}>{attachmentAction.pending ? "保存中..." : "上传并关联"}</button></form>{visibleAttachments.length ? <div className="table-wrap compact-table"><table><thead><tr><th>关联记录</th><th>文件</th><th>归档时间</th><th></th></tr></thead><tbody>{visibleAttachments.map((item) => <tr key={item.id}><td>{attachmentTarget(item)}</td><td>{item.original_name}</td><td>{new Date(item.created_at).toLocaleString("zh-CN")}</td><td><a className="text-link" href={`/api/attachments/${item.id}/file`}>查看原件</a></td></tr>)}</tbody></table></div> : <EmptyState title="当前筛选没有结算凭证" detail="调整筛选，或先为具体Snapshot/资金流水上传凭证。" />}</Panel>
      <Panel title="最近资金流水">{visibleTransactions.length ? <div className="table-wrap"><table><thead><tr><th>Date</th><th>Client / Platform / A/C</th><th>Type</th><th>Amount</th><th>凭证</th><th>Remark</th><th>操作</th></tr></thead><tbody>{visibleTransactions.map((x) => { const account = accountById.get(x.account_id); return <tr key={x.id}><td>{x.transaction_date}</td><td><strong>{account?.client_name || "未知Client"}</strong><small className="cell-note">{account ? [account.platform_name || "待确认Platform", account.account_number, account.scheme_name].filter(Boolean).join(" · ") : x.account_number}</small></td><td>{x.transaction_type}</td><td><Money value={x.amount} /></td><td>{x.evidence_complete ? `完整 (${x.evidence_count})` : "待补"}</td><td>{x.remark || "-"}</td><td>{x.correction_allowed ? <button className="ghost" type="button" disabled={correctionBusy} onClick={() => { setEditingTransaction(x); setLocalError(""); }}>更正</button> : <small className="cell-note">已由 Settlement #{x.locked_settlement_id} 锁定</small>}</td></tr>; })}</tbody></table></div> : <EmptyState title="当前筛选没有资金流水" detail="调整筛选，或为具体Sub Account登记Contribution/Withdrawal。" />}</Panel>
      <Panel title="余额快照">{visibleSnapshots.length ? <div className="table-wrap"><table><thead><tr><th>As-of Date</th><th>Client / Platform / A/C</th><th>Total Balance</th><th>持仓明细</th><th>来源</th><th>Closing资格</th><th>凭证</th></tr></thead><tbody>{visibleSnapshots.map((x) => { const account = accountById.get(x.account_id); return <tr key={x.id}><td>{x.as_of_date}</td><td><strong>{x.client_name || account?.client_name || "未知Client"}</strong><small className="cell-note">{[x.platform_name || account?.platform_name || "待确认Platform", x.account_number, x.scheme_name || account?.scheme_name].filter(Boolean).join(" · ")}</small></td><td><Money value={x.total_balance} /></td><td>{x.holdings.length ? <details><summary>{x.holdings.length}项</summary>{snapshotHoldings(x)}</details> : "无"}</td><td>{x.source_type === "STATEMENT_IMPORT" && x.statement_import_id ? <a className="text-link" href={`/api/statement-imports/${x.statement_import_id}/file`} target="_blank" rel="noreferrer">Statement Import · 查看原账单</a> : x.source_type === "STATEMENT_IMPORT" ? "Statement Import · 原账单索引缺失" : "手工录入"}</td><td>{x.eligible_for_closing ? "可作为Closing" : "普通快照"}</td><td>{x.evidence_complete ? `完整 (${x.evidence_count || 0})` : "待补"}</td></tr>; })}</tbody></table></div> : <EmptyState title="当前筛选没有余额快照" detail="调整筛选；账单确认入账或手工保存后会在这里出现。" />}</Panel>
    </>
  );
}
