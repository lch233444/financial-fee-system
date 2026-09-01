import { FormEvent, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { api, patchJson, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, PageHeader, Panel, StatusBadge } from "../components";
import { useApiList } from "../hooks";
import { accountIdentityLabel } from "../types";
import type { Account, Client, Company, FC, FeePlan, Platform } from "../types";

export default function ClientsPage({ notify }: { notify: (message: string) => void }) {
  const clients = useApiList<Client>("/api/clients");
  const accounts = useApiList<Account>("/api/accounts");
  const companies = useApiList<Company>("/api/companies");
  const fcs = useApiList<FC>("/api/fcs");
  const platforms = useApiList<Platform>("/api/platforms");
  const plans = useApiList<FeePlan>("/api/fee-plans");
  const [clientCompanyId, setClientCompanyId] = useState("");
  const [accountClientId, setAccountClientId] = useState("");
  const [draftClientId, setDraftClientId] = useState("");
  const [draftClientCompanyId, setDraftClientCompanyId] = useState("");
  const [draftAccountId, setDraftAccountId] = useState("");
  const [localError, setLocalError] = useState("");
  const [deletingKey, setDeletingKey] = useState("");
  const deleteBusyRef = useRef(false);
  const error = localError || clients.error || accounts.error || companies.error || fcs.error || platforms.error || plans.error;
  const selectedAccountClient = clients.data.find((item) => item.id === Number(accountClientId));
  const selectedDraftClient = clients.data.find((item) => item.id === Number(draftClientId));
  const selectedDraftAccount = accounts.data.find((item) => item.id === Number(draftAccountId));
  const selectedDraftAccountClient = clients.data.find((item) => item.id === selectedDraftAccount?.client_id);

  async function submitClient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/clients", {
        company_id: Number(data.get("company_id")), fc_id: Number(data.get("fc_id")), name: data.get("name"),
        management_start_date: data.get("start_date"), contact: data.get("contact") || null,
        remark: data.get("remark") || null, status: "ACTIVE",
      });
      event.currentTarget.reset();
      setClientCompanyId("");
      await clients.reload();
      notify("Client已建立");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Client保存失败"); }
  }

  async function submitAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/accounts", {
        client_id: Number(data.get("client_id")), platform_id: Number(data.get("platform_id")),
        fee_plan_id: Number(data.get("fee_plan_id")), account_number: data.get("account_number"),
        scheme_name: data.get("scheme_name") || null, start_date: data.get("start_date") || null,
        end_date: data.get("end_date") || null, status: "ACTIVE",
      });
      event.currentTarget.reset();
      setAccountClientId("");
      await accounts.reload();
      notify("Sub Account已建立");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Sub Account保存失败"); }
  }

  async function completeDraftClient(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedDraftClient) return;
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await patchJson(`/api/clients/${selectedDraftClient.id}`, {
        company_id: Number(data.get("company_id")), fc_id: Number(data.get("fc_id")),
        name: data.get("name"), management_start_date: data.get("start_date"),
        contact: data.get("contact") || null, remark: data.get("remark") || null, status: "ACTIVE",
      });
      setDraftClientId("");
      setDraftClientCompanyId("");
      await Promise.all([clients.reload(), accounts.reload()]);
      notify("待确认Client已补全并激活");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Client补全失败"); }
  }

  async function completeDraftAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedDraftAccount) return;
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await patchJson(`/api/accounts/${selectedDraftAccount.id}`, {
        platform_id: Number(data.get("platform_id")), fee_plan_id: Number(data.get("fee_plan_id")),
        scheme_name: data.get("scheme_name") || null, start_date: data.get("start_date") || null,
        end_date: data.get("end_date") || null, remark: data.get("remark") || null, status: "ACTIVE",
      });
      setDraftAccountId("");
      await accounts.reload();
      notify("待确认Sub Account已补全并激活");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Sub Account补全失败"); }
  }

  async function deleteProfile(kind: "Client" | "Sub Account", path: string, id: number, label: string) {
    if (deleteBusyRef.current) return;
    const warning = kind === "Client"
      ? `确认删除Client“${label}”？\n\n仅误建或确认不再需要、且没有任何Sub Account、Settlement、Invoice、附件或导出记录引用的Client可以删除；系统不会级联删除其他数据，此操作不可撤销。`
      : `确认删除Sub Account“${label}”？\n\n仅误建或确认不再需要、且没有账单入账、余额快照、资金流水、凭证、导出记录或Settlement引用的账户可以删除；系统不会级联删除其他数据，此操作不可撤销。\n\n若账户由误入账记录建立，请先在“账单导入”撤销并删除对应记录。`;
    if (!window.confirm(warning)) return;

    const key = `${path}:${id}`;
    deleteBusyRef.current = true;
    setDeletingKey(key);
    setLocalError("");
    try {
      await api<Record<string, unknown>>(`${path}/${id}`, { method: "DELETE" });
      if (kind === "Client") {
        if (draftClientId === String(id)) {
          setDraftClientId("");
          setDraftClientCompanyId("");
        }
        if (accountClientId === String(id)) setAccountClientId("");
      } else if (draftAccountId === String(id)) {
        setDraftAccountId("");
      }
      const refreshResults = await Promise.all([clients.reload(), accounts.reload()]);
      if (refreshResults.some((refreshed) => !refreshed)) {
        setLocalError(`${kind}已删除，但页面资料刷新失败，请重新载入页面确认最新状态`);
        notify(`${kind}已删除，但清单刷新失败`);
      } else {
        notify(`${kind}已删除`);
      }
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : `${kind}删除失败`);
    } finally {
      deleteBusyRef.current = false;
      setDeletingKey("");
    }
  }

  function deleteButton(kind: "Client" | "Sub Account", path: string, id: number, label: string) {
    const key = `${path}:${id}`;
    const deleting = deletingKey === key;
    return <button
      className="danger master-delete-button"
      type="button"
      title={`删除${kind}`}
      aria-label={`删除${kind} ${label}`}
      disabled={Boolean(deletingKey)}
      onClick={() => void deleteProfile(kind, path, id, label)}
    >
      <Trash2 size={14} />
      {deleting ? "删除中..." : "删除"}
    </button>;
  }

  return (
    <>
      <PageHeader title="客户与账户" subtitle="一个Client可有多个Sub Account；每个账户独立计算HWM，组合层只汇总结果" />
      {error ? <ErrorBanner message={error} /> : null}
      <div className="split-layout">
        <Panel title="新增Client" subtitle="正式启用前必须关联Company、FC和管理开始日期">
          <form className="form-grid" onSubmit={(e) => void submitClient(e)}>
            <Field label="Company"><select name="company_id" value={clientCompanyId} required onChange={(e) => setClientCompanyId(e.target.value)}><option value="" disabled>请选择</option>{companies.data.map((x) => <option key={x.id} value={x.id}>{x.code} · {x.name}</option>)}</select></Field>
            <Field label="FC"><select name="fc_id" defaultValue="" required><option value="" disabled>请选择</option>{fcs.data.filter((x) => !clientCompanyId || x.company_id === Number(clientCompanyId)).map((x) => <option key={x.id} value={x.id}>{x.name} ({x.code})</option>)}</select></Field>
            <Field label="Client Name"><input name="name" required /></Field>
            <Field label="Management Start Date"><input name="start_date" type="date" required /></Field>
            <Field label="联系方式"><input name="contact" /></Field>
            <Field label="备注"><textarea name="remark" rows={2} /></Field>
            <button className="primary" type="submit">保存Client</button>
          </form>
        </Panel>
        <Panel title="新增Sub Account" subtitle="币种固定为HKD">
          <form className="form-grid" onSubmit={(e) => void submitAccount(e)}>
            <Field label="Client"><select name="client_id" value={accountClientId} required onChange={(e) => setAccountClientId(e.target.value)}><option value="" disabled>请选择</option>{clients.data.filter((x) => x.status === "ACTIVE").map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Platform"><select name="platform_id" defaultValue="" required><option value="" disabled>请选择</option>{platforms.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Fee Plan"><select name="fee_plan_id" defaultValue="" required><option value="" disabled>请选择</option>{plans.data.filter((x) => !selectedAccountClient?.company_id || x.company_id === selectedAccountClient.company_id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Account Number"><input name="account_number" required /></Field>
            <Field label="Scheme Name"><input name="scheme_name" /></Field>
            <Field label="开始管理日期"><input name="start_date" type="date" required /></Field>
            <Field label="实际结束日期（如适用）"><input name="end_date" type="date" /></Field>
            <button className="primary" type="submit">保存Sub Account</button>
          </form>
        </Panel>
      </div>

      <div className="split-layout">
        <Panel title="补全待确认Client" subtitle="OCR建立的Draft必须补齐归属和开始日期后才能激活">
          <Field label="选择Draft Client">
            <select
              value={draftClientId}
              onChange={(event) => {
                const next = clients.data.find((item) => item.id === Number(event.target.value));
                setDraftClientId(event.target.value);
                setDraftClientCompanyId(next?.company_id ? String(next.company_id) : "");
              }}
            >
              <option value="">请选择</option>
              {clients.data.filter((item) => item.status === "DRAFT").map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </Field>
          {selectedDraftClient ? (
            <form className="form-grid" key={selectedDraftClient.id} onSubmit={(event) => void completeDraftClient(event)}>
              <Field label="Company"><select name="company_id" value={draftClientCompanyId} required onChange={(event) => setDraftClientCompanyId(event.target.value)}><option value="" disabled>请选择</option>{companies.data.map((item) => <option key={item.id} value={item.id}>{item.code} · {item.name}</option>)}</select></Field>
              <Field label="FC"><select name="fc_id" defaultValue={selectedDraftClient.fc_id ?? ""} required><option value="" disabled>请选择</option>{fcs.data.filter((item) => item.company_id === Number(draftClientCompanyId)).map((item) => <option key={item.id} value={item.id}>{item.name} ({item.code})</option>)}</select></Field>
              <Field label="Client Name"><input name="name" defaultValue={selectedDraftClient.name} required /></Field>
              <Field label="Management Start Date"><input name="start_date" type="date" defaultValue={selectedDraftClient.management_start_date ?? ""} required /></Field>
              <Field label="联系方式"><input name="contact" defaultValue={selectedDraftClient.contact ?? ""} /></Field>
              <Field label="备注"><textarea name="remark" rows={2} defaultValue={selectedDraftClient.remark ?? ""} /></Field>
              <button className="primary" type="submit">补全并激活Client</button>
            </form>
          ) : <small>当前共有 {clients.data.filter((item) => item.status === "DRAFT").length} 个待确认Client。</small>}
        </Panel>

        <Panel title="补全待确认Sub Account" subtitle="所属Client必须先激活，再补齐Platform和Fee Plan">
          <Field label="选择Draft Sub Account"><select value={draftAccountId} onChange={(event) => setDraftAccountId(event.target.value)}><option value="">请选择</option>{accounts.data.filter((item) => item.status === "DRAFT").map((item) => <option key={item.id} value={item.id}>{accountIdentityLabel(item)}</option>)}</select></Field>
          {selectedDraftAccount ? (
            <form className="form-grid" key={selectedDraftAccount.id} onSubmit={(event) => void completeDraftAccount(event)}>
              <Field label="Client"><input value={`${selectedDraftAccount.client_name} (${selectedDraftAccountClient?.status ?? "UNKNOWN"})`} disabled /></Field>
              <Field label="Platform"><select name="platform_id" defaultValue={selectedDraftAccount.platform_id ?? ""} required><option value="" disabled>请选择</option>{platforms.data.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
              <Field label="Fee Plan"><select name="fee_plan_id" defaultValue={selectedDraftAccount.fee_plan_id ?? ""} required><option value="" disabled>请选择</option>{plans.data.filter((item) => !selectedDraftAccountClient?.company_id || item.company_id === selectedDraftAccountClient.company_id).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
              <Field label="Scheme Name"><input name="scheme_name" defaultValue={selectedDraftAccount.scheme_name ?? ""} /></Field>
              <Field label="开始管理日期"><input name="start_date" type="date" defaultValue={selectedDraftAccount.start_date ?? ""} required /></Field>
              <Field label="实际结束日期（如适用）" hint="若账单日期等于退出日，保存后系统会重核该Snapshot的Closing资格"><input name="end_date" type="date" defaultValue={selectedDraftAccount.end_date ?? ""} /></Field>
              <Field label="备注"><textarea name="remark" rows={2} defaultValue={selectedDraftAccount.remark ?? ""} /></Field>
              <button className="primary" type="submit" disabled={selectedDraftAccountClient?.status !== "ACTIVE"}>补全并激活Sub Account</button>
            </form>
          ) : <small>当前共有 {accounts.data.filter((item) => item.status === "DRAFT").length} 个待确认Sub Account。</small>}
        </Panel>
      </div>

      <Panel title="客户与账户清单" subtitle={`${clients.data.length}位客户 · ${accounts.data.length}个账户`}>
        {clients.data.length ? (
          <div className="client-list">
            {clients.data.map((client) => {
              const rows = accounts.data.filter((account) => account.client_id === client.id);
              return <article className="client-card" key={client.id}>
                <header><div className="client-card-copy"><strong>{client.name}</strong><span>{client.company_name || "待确认Company"} · {client.fc_name || "待确认FC"} · 管理开始 {client.management_start_date || "待补全"}</span></div><div className="client-card-actions"><StatusBadge value={client.status} />{deleteButton("Client", "/api/clients", client.id, client.name)}</div></header>
                {rows.length ? <div className="table-wrap client-account-table"><table><thead><tr><th>Platform</th><th>Account Number</th><th>Scheme</th><th>Fee Plan</th><th>管理期间</th><th>Status</th><th>备注</th><th className="master-actions-column">操作</th></tr></thead><tbody>{rows.map((account) => <tr key={account.id}><td>{account.platform_name || "待确认Platform"}</td><td><strong>{account.account_number}</strong></td><td>{account.scheme_name || "-"}</td><td>{account.fee_plan_name || "待确认Fee Plan"}</td><td>{account.start_date || "待补全"}<small className="cell-note">至 {account.end_date || "持续管理"}</small></td><td><StatusBadge value={account.status} /></td><td>{account.remark || "-"}</td><td className="master-actions-column">{deleteButton("Sub Account", "/api/accounts", account.id, accountIdentityLabel(account))}</td></tr>)}</tbody></table></div> : <div className="client-account-empty">尚无Sub Account</div>}
              </article>;
            })}
          </div>
        ) : <EmptyState title="暂无客户" detail="可手工建立，也可由eMPF账单识别创建待确认档案。" />}
      </Panel>
    </>
  );
}
