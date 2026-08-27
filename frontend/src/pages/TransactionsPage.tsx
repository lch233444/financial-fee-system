import { FormEvent, useMemo, useState } from "react";
import { api, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Money, PageHeader, Panel } from "../components";
import { todayIso, useApiList } from "../hooks";
import type { Account } from "../types";

type Transaction = { id: number; account_id: number; account_number: string; transaction_date: string; transaction_type: string; amount: string; remark?: string; evidence_complete: boolean; evidence_count: number };
type Snapshot = { id: number; account_id: number; account_number: string; as_of_date: string; total_balance: string; eligible_for_closing: boolean; source_type: string; evidence_complete: boolean; evidence_count: number };
type Attachment = { id: number; entity_type: string; entity_id: number; original_name: string; size_bytes: number; created_at: string; duplicate?: boolean };

export default function TransactionsPage({ notify }: { notify: (message: string) => void }) {
  const accounts = useApiList<Account>("/api/accounts");
  const transactions = useApiList<Transaction>("/api/transactions");
  const snapshots = useApiList<Snapshot>("/api/balance-snapshots");
  const attachments = useApiList<Attachment>("/api/attachments");
  const [proofType, setProofType] = useState<"SNAPSHOT" | "TRANSACTION">("SNAPSHOT");
  const [localError, setLocalError] = useState("");
  const error = localError || accounts.error || transactions.error || snapshots.error || attachments.error;
  const proofTargets = useMemo(() => proofType === "SNAPSHOT"
    ? snapshots.data.map((item) => ({ id: item.id, label: `${item.account_number} · ${item.as_of_date} · HKD ${item.total_balance}` }))
    : transactions.data.map((item) => ({ id: item.id, label: `${item.account_number} · ${item.transaction_date} · ${item.transaction_type} · HKD ${item.amount}` })), [proofType, snapshots.data, transactions.data]);

  async function submitTransaction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/transactions", {
        account_id: Number(data.get("account_id")), transaction_date: data.get("date"),
        transaction_type: data.get("type"), amount: data.get("amount"), remark: data.get("remark") || null,
      });
      event.currentTarget.reset();
      await transactions.reload();
      notify("资金流水已保存；请补交该流水凭证后再Finalized");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "资金流水保存失败"); }
  }

  async function submitSnapshot(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/balance-snapshots", {
        account_id: Number(data.get("account_id")), as_of_date: data.get("date"), total_balance: data.get("balance"),
        eligible_for_closing: data.get("eligible") === "on", remark: data.get("remark") || null,
      });
      event.currentTarget.reset();
      await snapshots.reload();
      notify("余额快照已保存；请补交快照凭证后再Finalized");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "余额快照保存失败"); }
  }

  async function submitAttachment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLocalError("");
    try {
      const data = new FormData(event.currentTarget);
      data.set("entity_type", proofType);
      const result = await api<Attachment>("/api/attachments", { method: "POST", body: data });
      event.currentTarget.reset();
      await Promise.all([attachments.reload(), transactions.reload(), snapshots.reload()]);
      notify(result.duplicate ? "该凭证已经归档" : "凭证已关联到具体记录并安全归档");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "凭证上传失败"); }
  }

  function attachmentTarget(item: Attachment): string {
    if (item.entity_type === "SNAPSHOT") {
      const snapshot = snapshots.data.find((candidate) => candidate.id === item.entity_id);
      return snapshot ? `${snapshot.account_number} · ${snapshot.as_of_date}` : `Snapshot #${item.entity_id}`;
    }
    if (item.entity_type === "TRANSACTION") {
      const transaction = transactions.data.find((candidate) => candidate.id === item.entity_id);
      return transaction ? `${transaction.account_number} · ${transaction.transaction_date}` : `Transaction #${item.entity_id}`;
    }
    const account = accounts.data.find((candidate) => candidate.id === item.entity_id);
    return account ? `${account.account_number} · 历史账户级凭证` : `${item.entity_type} #${item.entity_id}`;
  }

  return (
    <>
      <PageHeader title="资金与余额" subtitle="Draft允许先保存；Beginning、Closing、Contribution和Withdrawal缺少凭证时不能Finalized" />
      {error ? <ErrorBanner message={error} /> : null}
      <div className="split-layout">
        <Panel title="新增资金流水"><form className="form-grid" onSubmit={(e) => void submitTransaction(e)}><Field label="Sub Account"><select name="account_id" required defaultValue=""><option value="" disabled>请选择</option>{accounts.data.map((x) => <option key={x.id} value={x.id}>{x.client_name} · {x.account_number}</option>)}</select></Field><Field label="Date"><input name="date" type="date" defaultValue={todayIso()} required /></Field><Field label="Type"><select name="type" defaultValue="CONTRIBUTION"><option value="CONTRIBUTION">Contribution 加款</option><option value="WITHDRAWAL">Withdrawal 提款</option></select></Field><Field label="Amount (HKD)"><input name="amount" type="number" min="0.01" step="0.01" required /></Field><Field label="Remark"><textarea name="remark" rows={2} /></Field><button className="primary" type="submit">保存流水</button></form></Panel>
        <Panel title="手工余额快照" subtitle="非季末且非退出日不可作为Closing"><form className="form-grid" onSubmit={(e) => void submitSnapshot(e)}><Field label="Sub Account"><select name="account_id" required defaultValue=""><option value="" disabled>请选择</option>{accounts.data.map((x) => <option key={x.id} value={x.id}>{x.client_name} · {x.account_number}</option>)}</select></Field><Field label="As-of Date"><input name="date" type="date" defaultValue={todayIso()} required /></Field><Field label="Total Balance (HKD)"><input name="balance" type="number" min="0" step="0.01" required /></Field><label className="check-field"><input name="eligible" type="checkbox" /> 季末或实际退出日，可作为Closing</label><Field label="Remark"><textarea name="remark" rows={2} /></Field><button className="secondary" type="submit">保存快照</button></form></Panel>
      </div>
      <Panel title="结算凭证归档" subtitle="凭证必须关联到具体Snapshot或资金流水；旧的账户级凭证继续保留，但不作为正式结算的充分依据"><form className="inline-form evidence-form" onSubmit={(e) => void submitAttachment(e)}><Field label="凭证类型"><select value={proofType} onChange={(e) => setProofType(e.target.value as "SNAPSHOT" | "TRANSACTION")}><option value="SNAPSHOT">Balance Snapshot</option><option value="TRANSACTION">Contribution / Withdrawal</option></select></Field><Field label="关联记录"><select name="entity_id" required defaultValue=""><option value="" disabled>请选择具体记录</option>{proofTargets.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></Field><Field label="凭证文件"><input name="file" type="file" accept=".jpg,.jpeg,.png,.pdf,.xlsx,.xls,.csv" required /></Field><button className="secondary" type="submit">上传并关联</button></form>{attachments.data.length ? <div className="table-wrap compact-table"><table><thead><tr><th>关联记录</th><th>文件</th><th>归档时间</th><th></th></tr></thead><tbody>{attachments.data.map((item) => <tr key={item.id}><td>{attachmentTarget(item)}</td><td>{item.original_name}</td><td>{new Date(item.created_at).toLocaleString("zh-CN")}</td><td><a className="text-link" href={`/api/attachments/${item.id}/file`}>查看原件</a></td></tr>)}</tbody></table></div> : null}</Panel>
      <Panel title="最近资金流水">{transactions.data.length ? <div className="table-wrap"><table><thead><tr><th>Date</th><th>A/C</th><th>Type</th><th>Amount</th><th>凭证</th><th>Remark</th></tr></thead><tbody>{transactions.data.map((x) => <tr key={x.id}><td>{x.transaction_date}</td><td>{x.account_number}</td><td>{x.transaction_type}</td><td><Money value={x.amount} /></td><td>{x.evidence_complete ? `完整 (${x.evidence_count})` : "待补"}</td><td>{x.remark || "-"}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无流水" detail="系统会按账户及结算日期自动汇总。" />}</Panel>
      <Panel title="余额快照">{snapshots.data.length ? <div className="table-wrap"><table><thead><tr><th>As-of Date</th><th>A/C</th><th>Total Balance</th><th>来源</th><th>Closing资格</th><th>凭证</th></tr></thead><tbody>{snapshots.data.map((x) => <tr key={x.id}><td>{x.as_of_date}</td><td>{x.account_number}</td><td><Money value={x.total_balance} /></td><td>{x.source_type}</td><td>{x.eligible_for_closing ? "可使用" : "仅快照"}</td><td>{x.evidence_complete ? `完整 (${x.evidence_count})` : "待补"}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无快照" detail="可手工录入或从eMPF账单确认生成。" />}</Panel>
    </>
  );
}
