import SearchableSelect from "../SearchableSelect";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { api, patchJson, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, PageHeader, Panel, StatusBadge, SectionNav, Pagination, Loading } from "../components";
import { useApiList, usePagination } from "../hooks";
import { useFormAction } from "../useFormAction";
import { accountIdentityLabel, clientIdentityLabel, formatDate } from "../types";
import type { Account, Client, FC, FeePlan, Platform } from "../types";
import RecordFilters from "../RecordFilters";
import { accountManagedInPeriod, defaultRecordFilters } from "../periodFilters";
import ImportsPage from "./ImportsPage";

export default function ClientsPage({ notify, initialYear = "2026", initialQuarter = "" }: { notify: (message: string) => void; initialYear?: string; initialQuarter?: string }) {
  const clients = useApiList<Client>("/api/clients");
  const accounts = useApiList<Account>("/api/accounts");
  const fcs = useApiList<FC>("/api/fcs");
  const platforms = useApiList<Platform>("/api/platforms");
  const plans = useApiList<FeePlan>("/api/fee-plans");
  const [accountClientId, setAccountClientId] = useState("");
  const [view, setView] = useState<"query" | "create">("query");
  const [filters, setFilters] = useState({ ...defaultRecordFilters, year: initialYear, quarter: initialQuarter });
  const [profileClientId, setProfileClientId] = useState("");
  const [draftAccountClientId, setDraftAccountClientId] = useState("");
  const [clientStatus, setClientStatus] = useState("");
  useEffect(() => { setFilters((current) => ({ ...current, year: initialYear, quarter: initialQuarter })); }, [initialYear, initialQuarter]);
  const managedAccounts = accounts.data.filter((account) => accountManagedInPeriod(account, filters.year, filters.quarter));
  const filteredAccounts = managedAccounts.filter((account) => !filters.feePlanId || account.fee_plan_id === Number(filters.feePlanId));
  const managedClientIds = new Set(managedAccounts.map((account) => account.client_id));
  const filteredClientIds = new Set(filteredAccounts.map((account) => account.client_id));
  const visibleClients = clients.data.filter((item) => filteredClientIds.has(item.id) && (!filters.clientId || item.id === Number(filters.clientId)) && (!filters.fcId || item.fc_id === Number(filters.fcId)) && (!clientStatus || item.status === clientStatus));
  const clientPages = usePagination(visibleClients, `${JSON.stringify(filters)}:${clientStatus}`, 10);
  const [draftClientId, setDraftClientId] = useState("");
  const [draftAccountId, setDraftAccountId] = useState("");
  const [localError, setLocalError] = useState("");
  const formAction = useFormAction(setLocalError, notify);
  const [deletingKey, setDeletingKey] = useState("");
  const deleteBusyRef = useRef(false);
  const error = localError || clients.error || accounts.error || fcs.error || platforms.error || plans.error;
  const selectedDraftClient = clients.data.find((item) => item.id === Number(draftClientId));
  const selectedDraftAccount = accounts.data.find((item) => item.id === Number(draftAccountId) && item.client_id === Number(draftAccountClientId));
  const selectedDraftAccountClient = clients.data.find((item) => item.id === selectedDraftAccount?.client_id);
  const clientOption = (client: Client) => ({ value: String(client.id), label: clientIdentityLabel(client, clients.data, accounts.data) });

  function submitClient(event: FormEvent<HTMLFormElement>) {
    return formAction.submit(event, {
      save: (data) => postJson("/api/clients", {
        fc_id: Number(data.get("fc_id")), name: data.get("name"),
        management_start_date: data.get("start_date"), contact: data.get("contact") || null,
        remark: data.get("remark") || null, status: "ACTIVE",
      }),
      refresh: clients.reload,
      message: "Client已建立",
    });
  }

  function submitAccount(event: FormEvent<HTMLFormElement>) {
    return formAction.submit(event, {
      save: (data) => postJson("/api/accounts", {
        client_id: Number(data.get("client_id")), platform_id: Number(data.get("platform_id")),
        fee_plan_id: Number(data.get("fee_plan_id")), account_number: data.get("account_number"),
        scheme_name: data.get("scheme_name") || null, start_date: data.get("start_date") || null,
        end_date: data.get("end_date") || null, status: "ACTIVE",
      }),
      afterSave: () => setAccountClientId(""),
      refresh: accounts.reload,
      message: "Sub Account已建立",
    });
  }

  function completeDraftClient(event: FormEvent<HTMLFormElement>) {
    if (!selectedDraftClient) { event.preventDefault(); return; }
    return formAction.submit(event, {
      save: (data) => patchJson(`/api/clients/${selectedDraftClient.id}`, {
        fc_id: Number(data.get("fc_id")),
        name: data.get("name"), management_start_date: data.get("start_date"),
        contact: data.get("contact") || null, remark: data.get("remark") || null, status: "ACTIVE",
      }),
      afterSave: () => { setDraftClientId(""); },
      refresh: () => Promise.all([clients.reload(), accounts.reload()]),
      message: "待确认Client已补全并激活",
    });
  }

  function completeDraftAccount(event: FormEvent<HTMLFormElement>) {
    if (!selectedDraftAccount) { event.preventDefault(); return; }
    return formAction.submit(event, {
      save: (data) => patchJson(`/api/accounts/${selectedDraftAccount.id}`, {
        platform_id: Number(data.get("platform_id")), fee_plan_id: Number(data.get("fee_plan_id")),
        scheme_name: data.get("scheme_name") || null, start_date: data.get("start_date") || null,
        end_date: data.get("end_date") || null, remark: data.get("remark") || null, status: "ACTIVE",
      }),
      afterSave: () => setDraftAccountId(""),
      refresh: accounts.reload,
      message: "待确认Sub Account已补全并激活",
    });
  }

  async function deleteProfile(kind: "Client" | "Sub Account", path: string, id: number, label: string) {
    if (deleteBusyRef.current) return;
    const warning = kind === "Client"
      ? `确认删除Client“${label}”？\n\n仅误建或确认不再需要、且没有任何Sub Account、Settlement、Invoice、附件或导出记录引用的Client可以删除；系统不会级联删除其他数据，此操作不可撤销。`
      : `确认删除Sub Account“${label}”？\n\n仅误建或确认不再需要、且没有账单入账、历史结余、供款加款取款记录、凭证、导出记录或Settlement引用的账户可以删除；系统不会级联删除其他数据，此操作不可撤销。\n\n若账户由误入账记录建立，请先在“导入季度结余”撤销并删除对应记录。`;
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

        }
        if (accountClientId === String(id)) setAccountClientId("");
        if (filters.clientId === String(id)) setFilters((current) => ({ ...current, clientId: "" }));
        if (profileClientId === String(id)) setProfileClientId("");
        if (draftAccountClientId === String(id)) { setDraftAccountClientId(""); setDraftAccountId(""); }
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

  function accountTable(rows: Account[]) {
    return rows.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap client-account-table"><table><thead><tr><th>Platform</th><th>Account Number</th><th>Scheme</th><th>Fee Plan</th><th>管理期间</th><th>Status</th><th>备注</th><th className="master-actions-column">操作</th></tr></thead><tbody>{rows.map((account) => <tr key={account.id}><td>{account.platform_name || "待确认Platform"}</td><td><strong>{account.account_number}</strong></td><td>{account.scheme_name || "-"}</td><td>{account.fee_plan_name || "待确认Fee Plan"}</td><td>{account.start_date ? formatDate(account.start_date) : "待补全"}<small className="cell-note">至 {account.end_date ? formatDate(account.end_date) : account.status === "CLOSED" ? "待补全结束日期" : "持续管理"}</small></td><td><StatusBadge value={account.status} /></td><td>{account.remark || "-"}</td><td className="master-actions-column">{deleteButton("Sub Account", "/api/accounts", account.id, accountIdentityLabel(account))}</td></tr>)}</tbody></table></div> : <div className="client-account-empty">尚无Sub Account</div>;
  }

  return (
    <>
      <PageHeader title="客户与账户" subtitle="一个Client可有多个Sub Account；每个账户独立计算HWM，组合层只汇总结果" />
      <div className="tabs" role="group" aria-label="客户功能"><button type="button" className={view === "query" ? "active" : ""} aria-pressed={view === "query"} disabled={formAction.pending || Boolean(deletingKey)} onClick={() => { setView("query"); setAccountClientId(""); setDraftClientId(""); setDraftAccountClientId(""); setDraftAccountId(""); setProfileClientId(""); }}>查询信息</button><button type="button" className={view === "create" ? "active" : ""} aria-pressed={view === "create"} disabled={formAction.pending || Boolean(deletingKey)} onClick={() => { setView("create"); setFilters((current) => ({ ...current, clientId: "" })); }}>新增客户（自动导入）</button></div>
      {view === "create" ? <SectionNav items={[{ id: "client-import", label: "自动导入" }, { id: "client-create", label: "手工新增客户" }, { id: "account-create", label: "新增账户" }, { id: "client-activate", label: "补全待确认资料" }, { id: "profile-maintenance", label: "档案维护" }]} /> : null}
      {error ? <ErrorBanner message={error} /> : null}
      {view === "query" ? <Panel id="client-directory" title="客户与账户清单" subtitle="所选期间实际受管客户；FC及收费计划显示当前档案关系，无收费或尚未出账单也计入。">
        <RecordFilters value={filters} onChange={setFilters} clients={clients.data} accounts={accounts.data} fcs={fcs.data} plans={plans.data} clientLabel="查询客户账户" disabled={Boolean(deletingKey)}>
          <Field label="客户状态"><select value={clientStatus} disabled={Boolean(deletingKey)} onChange={(event) => setClientStatus(event.target.value)}><option value="">全部状态</option><option value="ACTIVE">已启用</option><option value="DRAFT">待补全</option><option value="CLOSED">已结束</option></select></Field>
          <small role="status">显示 {visibleClients.length} / {managedClientIds.size} 位在管客户</small>
        </RecordFilters>
        {clients.loading || accounts.loading ? <Loading /> : visibleClients.length ? (
          <div className="client-list">
            {clientPages.rows.map((client) => {
              const rows = filteredAccounts.filter((account) => account.client_id === client.id);
              const planNames = [...new Set(rows.map((account) => account.fee_plan_name || "待补全收费计划"))];
              const periods = [...new Set(rows.map((account) => `${formatDate(account.start_date)} 至 ${account.end_date ? formatDate(account.end_date) : "持续管理"}`))];
              return <article className="client-card" key={client.id}>
                <header><div className="client-card-copy"><strong>{client.name}</strong><span>FC：{client.fc_name || "待确认FC"}</span><span>收费计划：{planNames.join("、")}</span>{periods.map((period) => <span key={period}>管理期间：{period}</span>)}</div><div className="client-card-actions"><StatusBadge value={client.status} /><button type="button" onClick={() => setFilters((current) => ({ ...current, clientId: String(client.id) }))}>查看账户</button></div></header>
                {filters.clientId === String(client.id) ? accountTable(rows) : null}
              </article>;
            })}
          </div>
        ) : <EmptyState title="所选条件下没有在管客户" detail="客户须有管理期间与所选年季重叠的受管账户；待补全及未开始管理的资料可在新增客户中维护。" />}
        <Pagination {...clientPages} />
      </Panel> : null}

      <div hidden={view !== "create"}>
      {view === "create" ? <div id="client-import"><ImportsPage notify={notify} embedded onConfirmed={async () => { await Promise.all([clients.reload(), accounts.reload()]); }} /></div> : null}
      <div className="split-layout">
        <Panel id="client-create" title="新增Client" subtitle="补全FC和管理开始日期；建立受管子账户后才计为在管客户">
          <form className="form-grid" onSubmit={(e) => void submitClient(e)}>
            <Field label="FC"><select name="fc_id" defaultValue="" required><option value="" disabled>请选择</option>{fcs.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Client Name"><input name="name" required /></Field>
            <Field label="Management Start Date"><input name="start_date" type="date" required /></Field>
            <Field label="联系方式"><input name="contact" /></Field>
            <Field label="备注"><textarea name="remark" rows={2} /></Field>
            <button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Client"}</button>
          </form>
        </Panel>
        <Panel id="account-create" title="新增Sub Account" subtitle="币种固定为HKD">
          <form className="form-grid" onSubmit={(e) => void submitAccount(e)}>
            <Field group label="Client"><SearchableSelect label="新增账户客户" name="client_id" value={accountClientId} required onChange={setAccountClientId} options={clients.data.filter((x) => x.status === "ACTIVE").map(clientOption)} /></Field>
            <Field label="Platform"><select name="platform_id" defaultValue="" required><option value="" disabled>请选择</option>{platforms.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Fee Plan"><select key={accountClientId} name="fee_plan_id" defaultValue="" required><option value="" disabled>请选择</option>{plans.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Account Number"><input name="account_number" required /></Field>
            <Field label="Scheme Name"><input name="scheme_name" /></Field>
            <Field label="开始管理日期"><input name="start_date" type="date" required /></Field>
            <Field label="实际结束日期（如适用）"><input name="end_date" type="date" /></Field>
            <button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Sub Account"}</button>
          </form>
        </Panel>
      </div>

      <div className="split-layout">
        <Panel id="client-activate" title="补全待确认Client" subtitle="OCR建立的Draft必须补齐归属和开始日期后才能激活">
          <Field group label="选择Draft Client">
            <SearchableSelect label="待确认客户" value={draftClientId}
              onChange={(value) => {
                setDraftClientId(value);
              }}
              options={clients.data.filter((item) => item.status === "DRAFT").map(clientOption)} />
          </Field>
          {selectedDraftClient ? (
            <form className="form-grid" key={selectedDraftClient.id} onSubmit={(event) => void completeDraftClient(event)}>
              <Field label="FC"><select name="fc_id" defaultValue={selectedDraftClient.fc_id ?? ""} required><option value="" disabled>请选择</option>{fcs.data.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
              <Field label="Client Name"><input name="name" defaultValue={selectedDraftClient.name} required /></Field>
              <Field label="Management Start Date"><input name="start_date" type="date" defaultValue={selectedDraftClient.management_start_date ?? ""} required /></Field>
              <Field label="联系方式"><input name="contact" defaultValue={selectedDraftClient.contact ?? ""} /></Field>
              <Field label="备注"><textarea name="remark" rows={2} defaultValue={selectedDraftClient.remark ?? ""} /></Field>
              <button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中…" : "补全并激活Client"}</button>
            </form>
          ) : <small>当前共有 {clients.data.filter((item) => item.status === "DRAFT").length} 个待确认Client。</small>}
        </Panel>

        <Panel title="补全待确认Sub Account" subtitle="所属Client必须先激活，再补齐Platform和Fee Plan">
          <Field group label="待确认账户所属客户"><SearchableSelect label="待确认账户客户" value={draftAccountClientId} onChange={(value) => { setDraftAccountClientId(value); setDraftAccountId(""); }} placeholder="搜索并选定客户" options={clients.data.map(clientOption)} /></Field>
          <Field label="选择Draft Sub Account"><select disabled={!draftAccountClientId} value={draftAccountId} onChange={(event) => setDraftAccountId(event.target.value)}><option value="">请选择</option>{accounts.data.filter((item) => item.status === "DRAFT" && item.client_id === Number(draftAccountClientId)).map((item) => <option key={item.id} value={item.id}>{accountIdentityLabel(item)}</option>)}</select></Field>
          {selectedDraftAccount ? (
            <form className="form-grid" key={selectedDraftAccount.id} onSubmit={(event) => void completeDraftAccount(event)}>
              <Field label="Client"><input value={`${selectedDraftAccount.client_name} (${selectedDraftAccountClient?.status ?? "UNKNOWN"})`} disabled /></Field>
              <Field label="Platform"><select name="platform_id" defaultValue={selectedDraftAccount.platform_id ?? ""} required><option value="" disabled>请选择</option>{platforms.data.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
              <Field label="Fee Plan"><select name="fee_plan_id" defaultValue={selectedDraftAccount.fee_plan_id ?? ""} required><option value="" disabled>请选择</option>{plans.data.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></Field>
              <Field label="Scheme Name"><input name="scheme_name" defaultValue={selectedDraftAccount.scheme_name ?? ""} /></Field>
              <Field label="开始管理日期"><input name="start_date" type="date" defaultValue={selectedDraftAccount.start_date ?? ""} required /></Field>
              <Field label="实际结束日期（如适用）" hint="填写账户实际退出管理的日期"><input name="end_date" type="date" defaultValue={selectedDraftAccount.end_date ?? ""} /></Field>
              <Field label="备注"><textarea name="remark" rows={2} defaultValue={selectedDraftAccount.remark ?? ""} /></Field>
              <button className="primary" type="submit" disabled={formAction.pending || selectedDraftAccountClient?.status !== "ACTIVE"}>{formAction.pending ? "保存中…" : "补全并激活Sub Account"}</button>
            </form>
          ) : <small>当前共有 {accounts.data.filter((item) => item.status === "DRAFT").length} 个待确认Sub Account。</small>}
        </Panel>
      </div>
      <Panel id="profile-maintenance" title="档案维护" subtitle="查看待补全、未开始管理及已结束的档案；删除仍须符合无引用条件。">
        <div className="list-search"><Field group label="选择客户档案"><SearchableSelect label="维护客户档案" value={profileClientId} onChange={setProfileClientId} options={clients.data.map(clientOption)} /></Field></div>
        {clients.data.filter((client) => String(client.id) === profileClientId).map((client) => <article className="client-card" key={client.id}>
          <header><div className="client-card-copy"><strong>{client.name}</strong><span>{client.fc_name || "待补全FC"} · 登记管理开始 {client.management_start_date ? formatDate(client.management_start_date) : "待补全"}</span></div><div className="client-card-actions"><StatusBadge value={client.status} />{deleteButton("Client", "/api/clients", client.id, client.name)}</div></header>
          {accountTable(accounts.data.filter((account) => account.client_id === client.id))}
        </article>)}
      </Panel>
      </div>


    </>
  );
}
