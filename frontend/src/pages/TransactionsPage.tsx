import { type FormEvent, lazy, Suspense, useMemo, useState } from "react";
import SearchableSelect from "../SearchableSelect";
import DocumentPreviewDialog, { type PreviewDocument } from "../DocumentPreviewDialog";
import RecordFilters from "../RecordFilters";
import { defaultRecordFilters, dateInPeriod } from "../periodFilters";
import { periodSnapshots, transactionLabels } from "../cashRecords";
import { api, patchJson, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Money, PageHeader, Panel, SectionNav, Loading } from "../components";
import { todayIso, useApiList } from "../hooks";
import { useFormAction } from "../useFormAction";
import { accountIdentityLabel, accountIdentityDetail, formatDate, formatDateTime } from "../types";
import type { Account, BalanceSnapshot, Client, FC, FeePlan, Settlement } from "../types";

const ImportsPage = lazy(() => import("./ImportsPage"));
type Transaction = { id: number; account_id: number; account_number: string; transaction_date: string; transaction_type: string; amount: string; remark?: string; remark_note?: string; attachment_ids?: number[]; superseded_attachment_ids?: number[]; evidence_complete: boolean; evidence_count: number; correction_allowed: boolean; locked_settlement_id: number | null };
type Attachment = { id: number; entity_type: string; entity_id: number | null; original_name: string; size_bytes: number; created_at: string; superseded?: boolean };
type Supplement = { type: "SNAPSHOT" | "TRANSACTION"; id: number; label: string };

async function uploadProofs(data: FormData, entityType: string, required: boolean): Promise<number[]> {
  const files = data.getAll("files").filter((file): file is File => file instanceof File && file.size > 0);
  if (required && !files.length) throw new Error("请至少选择一份凭证，再保存记录。");
  if (entityType === "SNAPSHOT" && files.some((file) => !/\.(png|jpe?g)$/i.test(file.name))) throw new Error("手动导入季度结余只接受 JPG 或 PNG 图片，每张图片单独上传。");
  const ids: number[] = [];
  for (const file of files) {
    const payload = new FormData(); payload.set("entity_type", entityType); payload.set("file", file);
    const saved = await api<Attachment>("/api/attachments", { method: "POST", body: payload });
    ids.push(saved.id);
  }
  return [...new Set(ids)];
}

function TransactionType({ value, onChange, disabled }: { value: string; onChange: (value: string) => void; disabled: boolean }) {
  return <Field label="资金类型"><select name="type" value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>{Object.entries(transactionLabels).map(([type, label]) => <option key={type} value={type}>{label}</option>)}</select></Field>;
}

export default function TransactionsPage({ notify }: { notify: (message: string) => void }) {
  const clients = useApiList<Client>("/api/clients");
  const fcs = useApiList<FC>("/api/fcs");
  const plans = useApiList<FeePlan>("/api/fee-plans");
  const accounts = useApiList<Account>("/api/accounts");
  const transactions = useApiList<Transaction>("/api/transactions");
  const snapshots = useApiList<BalanceSnapshot>("/api/balance-snapshots");
  const attachments = useApiList<Attachment>("/api/attachments");
  const settlements = useApiList<Settlement>("/api/settlements");
  const [filters, setFilters] = useState(defaultRecordFilters);
  const [platformId, setPlatformId] = useState("");
  const [accountId, setAccountId] = useState("");
  const [transactionAccountId, setTransactionAccountId] = useState("");
  const [snapshotAccountId, setSnapshotAccountId] = useState("");
  const [localError, setLocalError] = useState("");
  const [previewDocuments, setPreviewDocuments] = useState<PreviewDocument[]>([]);
  const [importMode, setImportMode] = useState<"auto" | "manual">("auto");
  const [showImporter, setShowImporter] = useState(false);
  const [transactionType, setTransactionType] = useState("MONTHLY_CONTRIBUTION");
  const [editingTransaction, setEditingTransaction] = useState<Transaction | null>(null);
  const [editType, setEditType] = useState("MONTHLY_CONTRIBUTION");
  const [supplement, setSupplement] = useState<Supplement | null>(null);
  const transactionAction = useFormAction(setLocalError, notify);
  const snapshotAction = useFormAction(setLocalError, notify);
  const correctionAction = useFormAction(setLocalError, notify);
  const supplementAction = useFormAction(setLocalError, notify);
  const busy = transactionAction.pending || snapshotAction.pending || correctionAction.pending || supplementAction.pending;
  const error = localError || clients.error || fcs.error || plans.error || accounts.error || transactions.error || snapshots.error || attachments.error || settlements.error;
  const accountById = useMemo(() => new Map(accounts.data.map((item) => [item.id, item])), [accounts.data]);
  const selectedClient = clients.data.find((item) => item.id === Number(filters.clientId));
  const clientAccounts = accounts.data.filter((item) => Boolean(filters.clientId) && item.client_id === Number(filters.clientId)
    && (!filters.fcId || selectedClient?.fc_id === Number(filters.fcId)) && (!filters.feePlanId || item.fee_plan_id === Number(filters.feePlanId)));
  const platformOptions = [...new Map(clientAccounts.filter((item) => item.platform_id != null).map((item) => [item.platform_id!, item.platform_name || "待确认Platform"])).entries()];
  const availableAccounts = clientAccounts.filter((item) => !platformId || item.platform_id === Number(platformId));
  const filteredAccounts = availableAccounts.filter((item) => !accountId || item.id === Number(accountId));
  const filteredIds = new Set(filteredAccounts.map((item) => item.id));
  const visibleTransactions = transactions.data.filter((item) => filteredIds.has(item.account_id) && dateInPeriod(item.transaction_date, filters.year, filters.quarter));
  const visibleSnapshots = periodSnapshots(filteredAccounts, snapshots.data, settlements.data, filters.year, filters.quarter);
  const transactionIds = new Set(visibleTransactions.map((item) => item.id));
  const snapshotIds = new Set(visibleSnapshots.map((item) => item.id));
  const transactionProofIds = new Set(visibleTransactions.flatMap((item) => [...(item.attachment_ids || []), ...(item.superseded_attachment_ids || [])]));
  const visibleAttachments = attachments.data.filter((item) => transactionProofIds.has(item.id) || (item.entity_id != null && ((item.entity_type === "TRANSACTION" && transactionIds.has(item.entity_id))
    || (item.entity_type === "SNAPSHOT" && snapshotIds.has(item.entity_id)) || (item.entity_type === "ACCOUNT" && filteredIds.has(item.entity_id)))));
  const refresh = () => Promise.all([transactions.reload(), snapshots.reload(), attachments.reload()]);

  function clearEntryAccounts() { setTransactionAccountId(""); setSnapshotAccountId(""); }
  function clearDependentSelection() { setAccountId(""); clearEntryAccounts(); setPlatformId(""); setEditingTransaction(null); setSupplement(null); }
  function recordProofs(type: string, id: number) {
    const transaction = type === "TRANSACTION" ? transactions.data.find((item) => item.id === id) : undefined;
    const relatedIds = new Set([...(transaction?.attachment_ids || []), ...(transaction?.superseded_attachment_ids || [])]);
    return attachments.data.filter((item) => (item.entity_type === type && item.entity_id === id) || relatedIds.has(item.id))
      .sort((a, b) => Number(Boolean(a.superseded)) - Number(Boolean(b.superseded)));
  }
  function showProofs(type: "SNAPSHOT" | "TRANSACTION", id: number, statementImportId?: number | null) {
    const files: PreviewDocument[] = recordProofs(type, id).map((item) => ({ path: `/api/attachments/${item.id}/file`,
      title: type === "SNAPSHOT" ? "原始凭证" : "结算凭证原件", label: `${item.superseded ? "旧凭证 · " : ""}${item.original_name}`,
      ...(type === "TRANSACTION" ? { filename: item.original_name } : {}) }));
    if (statementImportId) files.unshift({ path: `/api/statement-imports/${statementImportId}/file`, title: "原始凭证", label: "导入原件" });
    if (!files.length) { setLocalError("该记录的凭证索引未能读取，请刷新后核对。"); return; }
    setPreviewDocuments(files);
  }
  function submitTransaction(event: FormEvent<HTMLFormElement>) {
    return transactionAction.submit(event, {
      save: async (data) => {
        if (!filteredIds.has(Number(data.get("account_id")))) throw new Error("请核对所选客户与账户。");
        const attachmentIds = await uploadProofs(data, "TRANSACTION", true);
        return postJson("/api/transactions", { account_id: Number(data.get("account_id")), transaction_date: data.get("date"),
          transaction_type: data.get("type"), amount: data.get("amount"), remark: data.get("remark") || null, attachment_ids: attachmentIds });
      }, afterSave: () => { setTransactionType("MONTHLY_CONTRIBUTION"); setTransactionAccountId(""); }, refresh, message: "资金记录与凭证已一并保存",
    });
  }
  function submitSnapshot(event: FormEvent<HTMLFormElement>) {
    return snapshotAction.submit(event, {
      save: async (data) => {
        if (!filteredIds.has(Number(data.get("account_id")))) throw new Error("请核对所选客户与账户。");
        const attachmentIds = await uploadProofs(data, "SNAPSHOT", true);
        return postJson("/api/balance-snapshots", { account_id: Number(data.get("account_id")), as_of_date: data.get("date"),
          total_balance: data.get("balance"), remark: data.get("remark") || null, attachment_ids: attachmentIds });
      }, afterSave: () => setSnapshotAccountId(""), refresh, message: "季度结余与图片凭证已一并保存",
    });
  }
  function submitCorrection(event: FormEvent<HTMLFormElement>) {
    if (!editingTransaction) { event.preventDefault(); return; }
    return correctionAction.submit(event, {
      save: async (data) => {
        const attachmentIds = await uploadProofs(data, "TRANSACTION", !editingTransaction.evidence_complete);
        return patchJson(`/api/transactions/${editingTransaction.id}`, { transaction_date: data.get("date"), transaction_type: data.get("type"),
          amount: data.get("amount"), remark: data.get("remark") || null, correction_reason: data.get("correction_reason"),
          ...(attachmentIds.length ? { attachment_ids: attachmentIds } : {}) });
      }, afterSave: () => setEditingTransaction(null), refresh, message: "更正已保存，原凭证及更正记录继续保留",
    });
  }
  function submitSupplement(event: FormEvent<HTMLFormElement>) {
    if (!supplement) { event.preventDefault(); return; }
    return supplementAction.submit(event, {
      save: async (data) => {
        const file = data.get("file");
        if (!(file instanceof File) || !file.size) throw new Error("请选择原始凭证。");
        data.set("entity_type", supplement.type); data.set("entity_id", String(supplement.id));
        return api("/api/attachments", { method: "POST", body: data });
      }, afterSave: () => setSupplement(null), refresh, message: "历史记录的凭证已补存",
    });
  }
  const accountSelect = (kind: "transaction" | "snapshot", disabled: boolean) => <Field group label="Sub Account"><SearchableSelect name="account_id" label={kind === "transaction" ? "资金记录账户" : "季度结余账户"} required value={kind === "transaction" ? transactionAccountId : snapshotAccountId} onChange={kind === "transaction" ? setTransactionAccountId : setSnapshotAccountId} disabled={disabled || !filters.clientId} placeholder="搜索并选择账户" searchPlaceholder="搜索账户…" options={filteredAccounts.map((item) => ({ value: String(item.id), label: accountIdentityLabel(item) }))} /></Field>;
  const identity = (id: number, fallback: string) => { const account = accountById.get(id); return <><strong>{account?.client_name || "客户资料待核对"}</strong><small className="cell-note">{account ? accountIdentityDetail(account) : fallback}</small></>; };
  const remarkField = (type: string, disabled: boolean, initial = "") => <Field label={type === "CONTRIBUTION" ? "加款备注（必填）" : "备注"} hint={type === "CONTRIBUTION" ? "请填写加款说明，至少2字；系统自动附上到账日期、户口和金额，总长度不超过500字。" : undefined}><textarea name="remark" rows={2} defaultValue={initial} required={type === "CONTRIBUTION"} minLength={type === "CONTRIBUTION" ? 2 : undefined} maxLength={type === "CONTRIBUTION" ? 500 : undefined} disabled={disabled} /></Field>;

  return <>
    <PageHeader title="资金与余额" subtitle="查询资金记录与历史结余，导入时一并保存凭证；结算仍须人工核对并锁定。" />
    <SectionNav items={[{ id: "cash-records", label: "记录与历史结余" }, { id: "balance-import", label: "导入季度结余" }, { id: "cash-create", label: "导入供款加款取款" }]} />
    {error ? <ErrorBanner message={error} /> : null}
    <Panel id="cash-records" title="供款、加款、取款记录与历史结余" subtitle="按客户及期间查询，本季历史结余同时带出适用的期初结余。">
      <RecordFilters value={filters} onChange={(next) => { setFilters(next); clearDependentSelection(); }} clients={clients.data} accounts={accounts.data} fcs={fcs.data} plans={plans.data} disabled={busy} clientLabel="资金与余额客户">
        <Field label="Platform"><select disabled={!filters.clientId || busy} value={platformId} onChange={(event) => { setPlatformId(event.target.value); setAccountId(""); clearEntryAccounts(); setEditingTransaction(null); setSupplement(null); }}><option value="">全部Platform</option>{platformOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></Field>
        <Field label="Sub Account"><select disabled={!filters.clientId || busy} value={accountId} onChange={(event) => { setAccountId(event.target.value); clearEntryAccounts(); setEditingTransaction(null); setSupplement(null); }}><option value="">全部账户</option>{availableAccounts.map((item) => <option key={item.id} value={item.id}>{accountIdentityLabel(item)}</option>)}</select></Field>
      </RecordFilters>
      <section className="record-section" aria-labelledby="cash-ledger-heading"><h3 id="cash-ledger-heading">供款、加款、取款记录</h3>
        {transactions.loading || accounts.loading ? <Loading /> : visibleTransactions.length ? <div tabIndex={0} role="region" aria-label="供款、加款、取款记录表" className="table-wrap cash-ledger-table"><table><thead><tr><th>日期</th><th>Client / Platform / A/C</th><th>类型</th><th>金额</th><th>凭证</th><th>备注</th><th>操作</th></tr></thead><tbody>{visibleTransactions.map((item) => <tr key={item.id}>
          <td>{formatDate(item.transaction_date)}</td><td>{identity(item.account_id, item.account_number)}</td><td>{transactionLabels[item.transaction_type] || item.transaction_type}</td><td><Money value={item.amount} /></td>
          <td>{recordProofs("TRANSACTION", item.id).length ? <button className="text-link" type="button" onClick={() => showProofs("TRANSACTION", item.id)}>查看原件</button> : "待补凭证"}</td>
          <td className="record-remark">{item.remark?.replace(/到账日期：(\d{4}-\d{2}-\d{2})/, (_, date: string) => `到账日期：${formatDate(date)}`) || "—"}</td>
          <td>{item.correction_allowed ? <button className="ghost" type="button" disabled={busy} onClick={() => { setEditingTransaction(item); setEditType(item.transaction_type); setSupplement(null); setLocalError(""); }}>更正</button> : <small className="cell-note">已由结算 #{item.locked_settlement_id} 锁定</small>}
            {!item.evidence_complete ? <button className="ghost" type="button" disabled={busy} onClick={() => setSupplement({ type: "TRANSACTION", id: item.id, label: `${formatDate(item.transaction_date)} · ${item.account_number} · HKD ${item.amount}` })}>补存凭证</button> : null}</td>
        </tr>)}</tbody></table></div> : <EmptyState title={filters.clientId ? "当前筛选没有资金记录" : "请先搜索并选定客户"} detail="选定客户和期间后查看供款、加款、取款记录。" />}
      </section>
      <section className="record-section" aria-labelledby="balance-ledger-heading"><h3 id="balance-ledger-heading">历史结余</h3>
        {snapshots.loading || accounts.loading || settlements.loading ? <Loading /> : visibleSnapshots.length ? <div tabIndex={0} role="region" aria-label="历史结余表" className="table-wrap balance-ledger-table"><table><thead><tr><th>结余日期</th><th>Client / Platform / A/C</th><th>结余金额</th><th>凭证</th></tr></thead><tbody>{visibleSnapshots.map((item) => <tr key={item.id}>
          <td>{formatDate(item.as_of_date)}{item.opening ? <small className="cell-note">期初结余</small> : null}</td><td>{identity(item.account_id, item.account_number)}</td><td><Money value={item.total_balance} /></td>
          <td>{item.statement_import_id || recordProofs("SNAPSHOT", item.id).length ? <button className="text-link snapshot-source-link" type="button" onClick={() => showProofs("SNAPSHOT", item.id, item.statement_import_id)}>查看原始凭证</button> : <><span>待补凭证</span><button className="ghost" type="button" disabled={busy} onClick={() => setSupplement({ type: "SNAPSHOT", id: item.id, label: `${formatDate(item.as_of_date)} · ${item.account_number} · HKD ${item.total_balance}` })}>补存凭证</button></>}</td>
        </tr>)}</tbody></table></div> : <EmptyState title={filters.clientId ? "当前筛选没有历史结余" : "请先搜索并选定客户"} detail="显示本季结余及按账户结算规则适用的期初结余。" />}
      </section>
      {visibleAttachments.length ? <details className="record-archive"><summary>结算凭证归档 · {visibleAttachments.length}份</summary><div tabIndex={0} role="region" aria-label="结算凭证归档" className="table-wrap"><table><thead><tr><th>记录</th><th>文件</th><th>归档时间</th><th>操作</th></tr></thead><tbody>{visibleAttachments.map((item) => <tr key={item.id}><td>{item.entity_type === "SNAPSHOT" ? "历史结余" : item.entity_type === "TRANSACTION" ? "资金记录" : "历史账户凭证"} #{item.entity_id}{item.superseded ? <small className="cell-note">旧凭证（已替换，保留追溯）</small> : null}</td><td>{item.original_name}</td><td>{formatDateTime(item.created_at)}</td><td><button className="text-link" type="button" onClick={() => setPreviewDocuments([{ path: `/api/attachments/${item.id}/file`, title: "结算凭证原件", filename: item.original_name }])}>查看原件</button></td></tr>)}</tbody></table></div></details> : null}
      {editingTransaction ? <section className="record-section" aria-labelledby="correction-heading"><h3 id="correction-heading">更正资金记录</h3><p>原凭证继续保留；如选择新凭证，保存成功后作为当前凭证。</p><form className="form-grid" key={editingTransaction.id} onSubmit={(event) => void submitCorrection(event)}>
        <Field label="资金生效日期"><input name="date" type="date" defaultValue={editingTransaction.transaction_date} required disabled={busy} /></Field>
        <TransactionType value={editType} onChange={setEditType} disabled={busy} />
        <Field label="金额 (HKD)"><input name="amount" type="number" min="0.01" step="0.01" defaultValue={editingTransaction.amount} required disabled={busy} /></Field>
        {remarkField(editType, busy, editingTransaction.remark_note ?? editingTransaction.remark ?? "")}
        <Field label="更正原因"><textarea name="correction_reason" rows={2} minLength={2} maxLength={500} required disabled={busy} /></Field>
        <Field label={editingTransaction.evidence_complete ? "替换凭证（可选、多份）" : "原始凭证（必填）"}><input name="files" type="file" multiple accept=".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv" required={!editingTransaction.evidence_complete} disabled={busy} /></Field>
        <div className="form-actions"><button className="primary" type="submit" disabled={busy}>{correctionAction.pending ? "保存更正中…" : "保存更正"}</button><button className="ghost" type="button" disabled={busy} onClick={() => setEditingTransaction(null)}>取消</button></div>
      </form></section> : null}
      {supplement ? <section className="record-section" aria-labelledby="supplement-heading"><h3 id="supplement-heading">补存历史凭证</h3><p>{supplement.label}</p><form className="inline-form" onSubmit={(event) => void submitSupplement(event)}><Field label="原始凭证"><input name="file" type="file" accept={supplement.type === "SNAPSHOT" ? ".jpg,.jpeg,.png" : ".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv"} required disabled={busy} /></Field><button className="secondary" disabled={busy} type="submit">上传到本条记录</button><button className="ghost" type="button" disabled={busy} onClick={() => setSupplement(null)}>取消</button></form></section> : null}
    </Panel>
    <Panel id="balance-import" title="导入季度结余" subtitle="上传原始账单识别并人工确认，或填写结余并附上图片凭证。">
      <div className="tabs" role="group" aria-label="结余导入方式"><button type="button" aria-pressed={importMode === "auto"} className={importMode === "auto" ? "active" : ""} disabled={busy} onClick={() => setImportMode("auto")}>原件识别导入</button><button type="button" aria-pressed={importMode === "manual"} className={importMode === "manual" ? "active" : ""} disabled={busy} onClick={() => setImportMode("manual")}>手动填写并附图</button></div>
      {importMode === "auto" ? showImporter ? <Suspense fallback={<Loading />}><ImportsPage notify={notify} embedded onConfirmed={() => { void refresh(); void accounts.reload(); void clients.reload(); }} /></Suspense> : <div className="import-entry"><p>上传原件后，逐项核对客户、账户、日期、币种和结余金额，再确认保存。</p><button className="primary" type="button" onClick={() => setShowImporter(true)}>开始导入季度结余</button></div> : <form className="form-grid" onSubmit={(event) => void submitSnapshot(event)}>
        {accountSelect("snapshot", busy)}<Field label="结余日期"><input name="date" type="date" defaultValue={todayIso()} required disabled={busy || !filters.clientId} /></Field>
        <Field label="结余金额 (HKD)"><input name="balance" type="number" min="0" step="0.01" required disabled={busy || !filters.clientId} /></Field>
        <Field label="备注"><textarea name="remark" rows={2} disabled={busy || !filters.clientId} /></Field>
        <Field label="图片凭证（必填，可选多张）" hint="每份附件为一张 JPG 或 PNG 图片。"><input name="files" type="file" multiple accept=".jpg,.jpeg,.png" required disabled={busy || !filters.clientId} /></Field>
        <button className="primary" type="submit" disabled={busy || !filters.clientId}>{snapshotAction.pending ? "保存中…" : "保存结余与凭证"}</button>
      </form>}
    </Panel>
    <Panel id="cash-create" title="导入供款加款取款" subtitle="资金生效日期填写实际转账到账日；记录与凭证核对后一起提交。">
      <form className="form-grid" onSubmit={(event) => void submitTransaction(event)}>
        {accountSelect("transaction", busy)}<Field label="资金生效日期"><input name="date" type="date" defaultValue={todayIso()} required disabled={busy || !filters.clientId} /></Field>
        <TransactionType value={transactionType} onChange={setTransactionType} disabled={busy || !filters.clientId} />
        <Field label="金额 (HKD)"><input name="amount" type="number" min="0.01" step="0.01" required disabled={busy || !filters.clientId} /></Field>
        {remarkField(transactionType, busy || !filters.clientId)}
        <Field label="原始凭证（必填，可选多份）"><input name="files" type="file" multiple accept=".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv" required disabled={busy || !filters.clientId} /></Field>
        <button className="primary" type="submit" disabled={busy || !filters.clientId}>{transactionAction.pending ? "保存中…" : "保存记录与凭证"}</button>
      </form>
    </Panel>
    {previewDocuments.length ? <DocumentPreviewDialog key={previewDocuments[0].path} document={previewDocuments[0]} documents={previewDocuments} onClose={() => setPreviewDocuments([])} /> : null}
  </>;
}
