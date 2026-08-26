import { FormEvent, useState } from "react";
import { postJson } from "../api";
import { EmptyState, ErrorBanner, Field, PageHeader, Panel, StatusBadge } from "../components";
import { useApiList } from "../hooks";
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
  const [localError, setLocalError] = useState("");
  const error = localError || clients.error || accounts.error || companies.error || fcs.error || platforms.error || plans.error;
  const selectedAccountClient = clients.data.find((item) => item.id === Number(accountClientId));

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
        scheme_name: data.get("scheme_name") || null, start_date: data.get("start_date") || null, status: "ACTIVE",
      });
      event.currentTarget.reset();
      setAccountClientId("");
      await accounts.reload();
      notify("Sub Account已建立");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Sub Account保存失败"); }
  }

  return (
    <>
      <PageHeader title="客户与账户" subtitle="Client可对应多个Platform；同Platform、同Fee Plan的账户会合并结算" />
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
            <Field label="开始管理日期"><input name="start_date" type="date" /></Field>
            <button className="primary" type="submit">保存Sub Account</button>
          </form>
        </Panel>
      </div>

      <Panel title="客户与账户清单" subtitle={`${clients.data.length}位客户 · ${accounts.data.length}个账户`}>
        {clients.data.length ? (
          <div className="client-list">
            {clients.data.map((client) => {
              const rows = accounts.data.filter((account) => account.client_id === client.id);
              return <article className="client-card" key={client.id}><header><div><strong>{client.name}</strong><span>{client.company_name || "待确认Company"} · {client.fc_name || "待确认FC"}</span></div><StatusBadge value={client.status} /></header><div className="account-chips">{rows.length ? rows.map((account) => <span key={account.id}><b>{account.account_number}</b>{account.platform_name || "待确认平台"} / {account.fee_plan_name || "待确认计划"}<StatusBadge value={account.status} /></span>) : <small>尚无账户</small>}</div></article>;
            })}
          </div>
        ) : <EmptyState title="暂无客户" detail="可手工建立，也可由eMPF账单识别创建待确认档案。" />}
      </Panel>
    </>
  );
}
