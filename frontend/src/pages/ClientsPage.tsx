import ChineseDateInput from "../ChineseDateInput";
import SearchableSelect from "../SearchableSelect";
import { FormEvent, useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { api, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, PageHeader, Panel, StatusBadge, Pagination, Loading, WorkflowStep, WorkflowSection } from "../components";
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
  const [queryScope, setQueryScope] = useState<"period" | "all">("period");
  const [clientStatus, setClientStatus] = useState("");
  useEffect(() => { setQueryScope("period"); setFilters((current) => ({ ...current, year: initialYear, quarter: initialQuarter })); }, [initialYear, initialQuarter]);
  const managedAccounts = accounts.data.filter((account) => accountManagedInPeriod(account, filters.year, filters.quarter));
  const filteredAccounts = (queryScope === "all" ? accounts.data : managedAccounts).filter((account) => !filters.feePlanId || account.fee_plan_id === Number(filters.feePlanId));
  const managedClientIds = new Set(managedAccounts.map((account) => account.client_id));
  const filteredClientIds = new Set(filteredAccounts.map((account) => account.client_id));
  const visibleClients = clients.data.filter((item) => (filteredClientIds.has(item.id) || (queryScope === "all" && !filters.feePlanId)) && (!filters.clientId || item.id === Number(filters.clientId)) && (!filters.fcId || item.fc_id === Number(filters.fcId)) && (!clientStatus || item.status === clientStatus));
  const clientPages = usePagination(visibleClients, `${JSON.stringify(filters)}:${clientStatus}:${queryScope}`, 10);
  const [localError, setLocalError] = useState("");
  const formAction = useFormAction(setLocalError, notify);
  const [deletingKey, setDeletingKey] = useState("");
  const deleteBusyRef = useRef(false);
  const error = localError || clients.error || accounts.error || fcs.error || platforms.error || plans.error;
  const clientOption = (client: Client) => ({ value: String(client.id), label: clientIdentityLabel(client, clients.data, accounts.data) });

  function submitClient(event: FormEvent<HTMLFormElement>) {
    return formAction.submit(event, {
      save: (data) => postJson("/api/clients", {
        fc_id: Number(data.get("fc_id")), name: data.get("name"),
        management_start_date: data.get("start_date"), contact: data.get("contact") || null,
        remark: data.get("remark") || null, status: "ACTIVE",
      }),
      refresh: clients.reload,
      message: "客户已建立",
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
      message: "子账户已建立",
    });
  }

  async function deleteProfile(kind: "客户" | "子账户", path: string, id: number, label: string) {
    if (deleteBusyRef.current) return;
    const warning = kind === "客户"
      ? `确认删除客户“${label}”？\n\n仅误建或确认不再需要、且没有任何子账户、结算、账单、附件或导出记录引用的客户可以删除；系统不会级联删除其他数据，此操作不可撤销。`
      : `确认删除子账户“${label}”？\n\n仅误建或确认不再需要、且没有账单入账、历史结余、供款加款取款记录、凭证、导出记录或结算引用的账户可以删除；系统不会级联删除其他数据，此操作不可撤销。\n\n若账户由误入账记录建立，请先在“导入季度结余”撤销并删除对应记录。`;
    if (!window.confirm(warning)) return;

    const key = `${path}:${id}`;
    deleteBusyRef.current = true;
    setDeletingKey(key);
    setLocalError("");
    try {
      await api<Record<string, unknown>>(`${path}/${id}`, { method: "DELETE" });
      if (kind === "客户") {
        if (accountClientId === String(id)) setAccountClientId("");
        if (filters.clientId === String(id)) setFilters((current) => ({ ...current, clientId: "" }));
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

  function deleteButton(kind: "客户" | "子账户", path: string, id: number, label: string) {
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
    return rows.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap client-account-table"><table><thead><tr><th>投资平台</th><th>账户号码</th><th>平台计划</th><th>收费计划</th><th>管理期间</th><th>状态</th><th>备注</th><th className="master-actions-column">操作</th></tr></thead><tbody>{rows.map((account) => <tr key={account.id}><td>{account.platform_name || "待确认投资平台"}</td><td><strong>{account.account_number}</strong></td><td>{account.scheme_name || "-"}</td><td>{account.fee_plan_name || "待确认收费计划"}</td><td>{account.start_date ? formatDate(account.start_date) : "待补全"}<small className="cell-note">至 {account.end_date ? formatDate(account.end_date) : account.status === "CLOSED" ? "待补全结束日期" : "持续管理"}</small></td><td><StatusBadge value={account.status} /></td><td>{account.remark || "-"}</td><td className="master-actions-column">{deleteButton("子账户", "/api/accounts", account.id, accountIdentityLabel(account))}</td></tr>)}</tbody></table></div> : <div className="client-account-empty">尚无子账户</div>;
  }

  return (
    <div className="workflow-page">
      <PageHeader title="客户与账户" subtitle="查已有资料请选择“查询信息”；建立客户及账户请选择“新增客户”。一个客户可有多个独立收费账户。" />
      <div className="tabs workflow-tabs" role="group" aria-label="客户功能"><button type="button" className={view === "query" ? "active" : ""} aria-pressed={view === "query"} disabled={formAction.pending || Boolean(deletingKey)} onClick={() => { setView("query"); setAccountClientId(""); }}>查询信息</button><button type="button" className={view === "create" ? "active" : ""} aria-pressed={view === "create"} disabled={formAction.pending || Boolean(deletingKey)} onClick={() => { setView("create"); setFilters((current) => ({ ...current, clientId: "" })); }}>新增客户</button></div>
      {error ? <ErrorBanner message={error} /> : null}
      {view === "query" ? <Panel id="client-directory" title="客户与账户清单" subtitle={queryScope === "all" ? "全部客户档案，不受年／季度限制；选择客户后查看账户，空档案仍按原有规则删除。" : "所选期间实际受管客户；中介人及收费计划显示当前档案关系，无收费或尚未出账单也计入。"}>
        <WorkflowStep number={1} title="选择查询范围与条件" detail="查看当前在管客户可按年季筛选；查找无账户或待补全客户，请切换到全部档案。" />
        <Field label="查询范围"><select value={queryScope} disabled={Boolean(deletingKey)} onChange={(event) => { setQueryScope(event.target.value as "period" | "all"); setFilters((current) => ({ ...current, clientId: "" })); }}><option value="period">所选期间在管客户</option><option value="all">全部档案</option></select></Field>
        <RecordFilters showPeriod={queryScope === "period"} value={filters} onChange={setFilters} clients={clients.data} accounts={accounts.data} fcs={fcs.data} plans={plans.data} clientLabel="查询客户账户" disabled={Boolean(deletingKey)}>
          <Field label="客户状态"><select value={clientStatus} disabled={Boolean(deletingKey)} onChange={(event) => setClientStatus(event.target.value)}><option value="">全部状态</option><option value="ACTIVE">已启用</option><option value="DRAFT">待补全</option><option value="CLOSED">已结束</option></select></Field>
          <small role="status">显示 {visibleClients.length} / {queryScope === "all" ? clients.data.length : managedClientIds.size} 位{queryScope === "all" ? "客户" : "在管客户"}</small>
        </RecordFilters>
        <WorkflowStep number={2} title="查看客户与账户" detail="点击客户的“查看账户”，展开该客户在当前筛选条件下的账户资料。" />
        {clients.loading || accounts.loading ? <Loading /> : visibleClients.length ? (
          <div className="client-list">
            {clientPages.rows.map((client) => {
              const rows = filteredAccounts.filter((account) => account.client_id === client.id);
              const planNames = [...new Set(rows.map((account) => account.fee_plan_name || "待补全收费计划"))];
              const periods = [...new Set(rows.map((account) => `${formatDate(account.start_date)} 至 ${account.end_date ? formatDate(account.end_date) : "持续管理"}`))];
              return <article className="client-card" key={client.id}>
                <header><div className="client-card-copy"><strong>{client.name}</strong><span>中介人：{client.fc_name || "待确认中介人"}</span><span>收费计划：{planNames.join("、") || "尚无账户"}</span>{periods.map((period) => <span key={period}>管理期间：{period}</span>)}</div><div className="client-card-actions"><StatusBadge value={client.status} /><button type="button" onClick={() => setFilters((current) => ({ ...current, clientId: String(client.id) }))}>查看账户</button>{queryScope === "all" ? deleteButton("客户", "/api/clients", client.id, clientIdentityLabel(client, clients.data, accounts.data)) : null}</div></header>
                {filters.clientId === String(client.id) ? accountTable(rows) : null}
              </article>;
            })}
          </div>
        ) : <EmptyState title={queryScope === "all" ? "所选条件下没有客户档案" : "所选条件下没有在管客户"} detail={queryScope === "all" ? "请调整客户、中介人、收费计划或状态筛选。" : "客户须有管理期间与所选年季重叠的受管账户；待补全、未开始管理及无账户客户可切换到全部档案查看。"} />}
        <Pagination {...clientPages} />
      </Panel> : null}

      <div hidden={view !== "create"}>
      <p className="workflow-intro"><strong>先选择一种建档方式</strong>有原始账单可使用自动导入；需要直接填写资料可使用手动导入。已有客户新增账户可直接进入手动导入第2步。</p>
      {view === "create" ? <WorkflowSection id="client-import" title="自动导入" description="上传原件 → 选择导入记录 → 核对资料并确认。确认成功后，可在查询信息中查看客户及账户。"><ImportsPage notify={notify} embedded guided title="新增客户自动导入" onConfirmed={async () => { await Promise.all([clients.reload(), accounts.reload()]); }} /></WorkflowSection> : null}
      <WorkflowSection id="client-manual" title="手动导入" description="新客户先建立客户档案，再建立账户；已有客户可直接选择其档案新增账户。">
      <div className="split-layout">
        <Panel id="client-create" title="新增客户" step="第1步 · 建立客户档案" subtitle="填写中介人、姓名及管理开始日期；建立受管账户后才计为在管客户。">
          <form className="form-grid" onSubmit={(e) => void submitClient(e)}>
            <Field label="中介人"><select name="fc_id" defaultValue="" required><option value="" disabled>请选择</option>{fcs.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="客户姓名"><input name="name" required /></Field>
            <Field label="开始管理日期"><ChineseDateInput aria-label="开始管理日期" name="start_date"  required /></Field>
            <Field label="联系方式"><input name="contact" /></Field>
            <Field label="备注"><textarea name="remark" rows={2} /></Field>
            <button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存客户"}</button>
            <p className="workflow-next">保存成功后：在第2步选择该客户，继续建立账户。</p>
          </form>
        </Panel>
        <Panel id="account-create" title="新增子账户" step="第2步 · 建立客户账户" subtitle="先选择已有客户，再填写平台、收费计划及账户资料；币种为港币。">
          <form className="form-grid" onSubmit={(e) => void submitAccount(e)}>
            <Field group label="客户"><SearchableSelect label="新增账户客户" name="client_id" value={accountClientId} required onChange={setAccountClientId} options={clients.data.filter((x) => x.status === "ACTIVE").map(clientOption)} /></Field>
            <Field label="投资平台"><select name="platform_id" defaultValue="" required><option value="" disabled>请选择</option>{platforms.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="收费计划"><select key={accountClientId} name="fee_plan_id" defaultValue="" required><option value="" disabled>请选择</option>{plans.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="账户号码"><input name="account_number" required /></Field>
            <Field label="平台计划名称"><input name="scheme_name" /></Field>
            <Field label="开始管理日期"><ChineseDateInput aria-label="开始管理日期" name="start_date"  required /></Field>
            <Field label="实际结束日期（如适用）"><ChineseDateInput aria-label="实际结束日期（如适用）" name="end_date"  /></Field>
            <button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存子账户"}</button>
            <p className="workflow-next">保存成功后：在“查询信息”核对账户，再到“资金与余额”录入结余及资金记录。</p>
          </form>
        </Panel>
      </div>
      </WorkflowSection>
      </div>
    </div>
  );
}
