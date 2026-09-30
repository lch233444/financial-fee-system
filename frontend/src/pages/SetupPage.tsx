import { matchesSearch } from "../SearchableSelect";
import { FormEvent, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { api, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, PageHeader, Panel, Loading, WorkflowSection } from "../components";
import { useApiList } from "../hooks";
import { useFormAction } from "../useFormAction";
import type { Company, FC, FeePlan, Platform } from "../types";

type Tab = "company" | "fc" | "platform" | "plan";
type DeletableMaster = "Company" | "FC" | "Platform" | "Fee Plan";

export default function SetupPage({ notify }: { notify: (message: string) => void }) {
  const [tab, setTab] = useState<Tab>("company");
  const companies = useApiList<Company>("/api/companies");
  const fcs = useApiList<FC>("/api/fcs");
  const platforms = useApiList<Platform>("/api/platforms");
  const plans = useApiList<FeePlan>("/api/fee-plans");
  const [search, setSearch] = useState("");
  const [localError, setLocalError] = useState("");
  const formAction = useFormAction(setLocalError, notify);
  const [deletingKey, setDeletingKey] = useState("");
  const deleteInFlight = useRef(false);
  const error = localError || companies.error || fcs.error || platforms.error || plans.error;
  const visibleCompanies = tab === "company" ? companies.data.filter((x) => matchesSearch(`${x.name} ${x.code}`, search)) : [];
  const visibleFcs = tab === "fc" ? fcs.data.filter((x) => matchesSearch(x.name, search)) : [];
  const visiblePlatforms = tab === "platform" ? platforms.data.filter((x) => matchesSearch(`${x.name} ${x.code}`, search)) : [];
  const visiblePlans = tab === "plan" ? plans.data.filter((x) => matchesSearch(x.name, search)) : [];

  async function deleteMaster(kind: DeletableMaster, path: string, id: number, label: string) {
    const key = `${path}:${id}`;
    if (deleteInFlight.current || !window.confirm(`确认删除${kind}“${label}”？\n\n仅未被引用的资料可以删除；此操作不可撤销，系统不会级联删除任何业务数据。`)) return;
    deleteInFlight.current = true;
    setDeletingKey(key);
    setLocalError("");
    try {
      await api<{ status: string; id: number }>(`${path}/${id}`, { method: "DELETE" });
      await Promise.all([companies.reload(), fcs.reload(), platforms.reload(), plans.reload()]);
      notify(`${kind}已删除`);
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : `${kind}删除失败`);
    } finally {
      deleteInFlight.current = false;
      setDeletingKey("");
    }
  }

  function deleteButton(kind: DeletableMaster, path: string, id: number, label: string) {
    const key = `${path}:${id}`;
    const deleting = deletingKey === key;
    return (
      <button
        className="danger master-delete-button"
        type="button"
        title={`删除${kind}`}
        aria-label={`删除${kind} ${label}`}
        disabled={Boolean(deletingKey)}
        onClick={() => void deleteMaster(kind, path, id, label)}
      >
        <Trash2 size={14} />
        {deleting ? "删除中..." : "删除"}
      </button>
    );
  }

  function submitCompany(event: FormEvent<HTMLFormElement>) {
    return formAction.submit(event, {
      save: (data) => postJson("/api/companies", {
        name: data.get("name"), code: data.get("code"), address: data.get("address") || null,
        contact: data.get("contact") || null, bank_information: data.get("bank") || null,
        cheque_information: data.get("cheque") || null, payment_terms_days: Number(data.get("terms") || 14),
      }),
      refresh: companies.reload,
      message: "Company已保存",
    });
  }

  function submitFC(event: FormEvent<HTMLFormElement>) {
    return formAction.submit(event, {
      save: (data) => postJson("/api/fcs", { name: data.get("name") }),
      refresh: fcs.reload,
      message: "FC已保存",
    });
  }

  function submitPlatform(event: FormEvent<HTMLFormElement>) {
    return formAction.submit(event, {
      save: (data) => postJson("/api/platforms", { name: data.get("name"), code: data.get("code"), trustee: data.get("trustee") || null }),
      refresh: platforms.reload,
      message: "Platform已保存",
    });
  }

  function submitPlan(event: FormEvent<HTMLFormElement>) {
    return formAction.submit(event, {
      save: (data) => postJson("/api/fee-plans", {
        name: data.get("name"),
        fee_rate_percent: data.get("rate"), calculation_method: "HIGH_WATER_MARK",
      }),
      refresh: plans.reload,
      message: "Fee Plan已保存",
    });
  }

  return (
    <div className="workflow-page setup-workflow">
      <PageHeader title="基础设置" subtitle="FC与收费计划独立建立；Company用于出账单时选择收款公司" />
      <div className="workflow-intro"><strong>先查已有资料，缺少时再新增</strong>FC、平台和收费计划分别建立后用于客户及账户；收款公司在出账单时选择，不用于绑定FC或收费计划。已有资料可直接使用，无需重复创建。</div>
      {error ? <ErrorBanner message={error} /> : null}
      <WorkflowSection id="setup-category" title="第1步 · 选择资料类别" description="按本次需要选择收款公司、中介人、投资平台或收费计划；切换类别会清空搜索及未保存表单，填写后请先保存。">
      <div className="tabs workflow-tabs" role="group" aria-label="基础资料类别">
        <button aria-pressed={tab === "company"} className={tab === "company" ? "active" : ""} onClick={() => { setTab("company"); setSearch(""); }}>收款公司 Company</button>
        <button aria-pressed={tab === "fc"} className={tab === "fc" ? "active" : ""} onClick={() => { setTab("fc"); setSearch(""); }}>中介人 FC</button>
        <button aria-pressed={tab === "platform"} className={tab === "platform" ? "active" : ""} onClick={() => { setTab("platform"); setSearch(""); }}>投资平台 Platform</button>
        <button aria-pressed={tab === "plan"} className={tab === "plan" ? "active" : ""} onClick={() => { setTab("plan"); setSearch(""); }}>收费计划 Fee Plan</button>
      </div>
      </WorkflowSection>

      {tab === "company" && <>
        <Panel id="setup-existing" step="第2步 · 查询已有资料" title="现有Company"><div className="list-search"><Field label="搜索Company"><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称或编号…" /></Field></div>{companies.loading ? <Loading /> : companies.data.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>付款期限</th><th className="master-actions-column">操作</th></tr></thead><tbody>{visibleCompanies.map((x) => <tr key={x.id}><td><strong>{x.code}</strong></td><td>{x.name}</td><td>{x.payment_terms_days}天</td><td className="master-actions-column">{deleteButton("Company", "/api/companies", x.id, `${x.code} · ${x.name}`)}</td></tr>)}</tbody></table>{visibleCompanies.length === 0 ? <EmptyState title="没有匹配项" detail="请更换关键词或清空搜索。" /> : null}</div> : <EmptyState title="暂无Company" detail="请先建立公司主体。" />}</Panel>
        <Panel id="setup-create" step="第3步 · 补建缺少的资料" title="新增Company" subtitle="Company全名用于账单收款资料；Code仅作内部标识"><form className="form-grid" onSubmit={(e) => void submitCompany(e)}><Field label="Company Name（收款公司全名）"><input name="name" required /></Field><Field label="Company Code（仅内部标识）"><input name="code" required maxLength={20} /></Field><Field label="地址 Address"><input name="address" /></Field><Field label="联系方式 Contact"><input name="contact" /></Field><Field label="默认付款天数"><input name="terms" type="number" defaultValue="14" min="0" max="365" /></Field><Field label="银行收款信息"><textarea name="bank" rows={3} /></Field><Field label="支票信息"><textarea name="cheque" rows={3} /></Field><p className="workflow-next">确认现有资料中没有本项后再保存。保存成功后，到第2步搜索核对新记录，再继续客户建档或账单出具。</p><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Company"}</button></form></Panel>
      </>}
      {tab === "fc" && <>
        <Panel id="setup-existing" step="第2步 · 查询已有资料" title="现有FC"><div className="list-search"><Field label="搜索FC"><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称…" /></Field></div>{fcs.loading ? <Loading /> : fcs.data.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap"><table><thead><tr><th>FC</th><th className="master-actions-column">操作</th></tr></thead><tbody>{visibleFcs.map((x) => <tr key={x.id}><td>{x.name}<small className="cell-note">档案 #{x.id}</small></td><td className="master-actions-column">{deleteButton("FC", "/api/fcs", x.id, x.name)}</td></tr>)}</tbody></table>{visibleFcs.length === 0 ? <EmptyState title="没有匹配项" detail="请更换关键词或清空搜索。" /> : null}</div> : <EmptyState title="暂无FC" detail="建立后可关联客户并统计Service Fee。" />}</Panel>
        <Panel id="setup-create" step="第3步 · 补建缺少的资料" title="新增FC"><form className="form-grid" onSubmit={(e) => void submitFC(e)}><Field label="FC Name" hint="填写完整英文姓名，例如Tony Wu；账单编号自动取各单词首字母TW"><input name="name" required /></Field><p className="workflow-next">确认现有资料中没有本项后再保存。保存成功后，到第2步搜索核对新记录，再继续客户建档或账单出具。</p><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存FC"}</button></form></Panel>
      </>}
      {tab === "platform" && <>
        <Panel id="setup-existing" step="第2步 · 查询已有资料" title="现有Platform"><div className="list-search"><Field label="搜索Platform"><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称或编号…" /></Field></div>{platforms.loading ? <Loading /> : platforms.data.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap"><table><thead><tr><th>Code</th><th>Platform</th><th>Trustee（MPF受托机构）</th><th className="master-actions-column">操作</th></tr></thead><tbody>{visiblePlatforms.map((x) => <tr key={x.id}><td>{x.code}</td><td>{x.name}</td><td>{x.trustee || "-"}</td><td className="master-actions-column">{deleteButton("Platform", "/api/platforms", x.id, `${x.code} · ${x.name}`)}</td></tr>)}</tbody></table>{visiblePlatforms.length === 0 ? <EmptyState title="没有匹配项" detail="请更换关键词或清空搜索。" /> : null}</div> : <EmptyState title="暂无Platform" detail="账单导入也可创建待确认Platform。" />}</Panel>
        <Panel id="setup-create" step="第3步 · 补建缺少的资料" title="新增Platform" subtitle="Trustee是MPF计划受托机构，不是FC/中介人，也不参与Invoice编号"><form className="form-grid" onSubmit={(e) => void submitPlatform(e)}><Field label="Platform Name"><input name="name" required /></Field><Field label="Platform Code"><input name="code" required /></Field><Field label="Trustee（MPF计划受托机构）"><input name="trustee" /></Field><p className="workflow-next">确认现有资料中没有本项后再保存。保存成功后，到第2步搜索核对新记录，再继续客户建档或账单出具。</p><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Platform"}</button></form></Panel>
      </>}
      {tab === "plan" && <>
        <Panel id="setup-existing" step="第2步 · 查询已有资料" title="现有Fee Plan"><div className="list-search"><Field label="搜索Fee Plan"><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称…" /></Field></div>{plans.loading ? <Loading /> : plans.data.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap"><table><thead><tr><th>收费计划</th><th>Rate</th><th className="master-actions-column">操作</th></tr></thead><tbody>{visiblePlans.map((x) => <tr key={x.id}><td>{x.name}<small className="cell-note">档案 #{x.id}</small></td><td>{Number(x.fee_rate_percent).toFixed(2)}%</td><td className="master-actions-column">{deleteButton("Fee Plan", "/api/fee-plans", x.id, x.name)}</td></tr>)}</tbody></table>{visiblePlans.length === 0 ? <EmptyState title="没有匹配项" detail="请更换关键词或清空搜索。" /> : null}</div> : <EmptyState title="暂无Fee Plan" detail="当前计划可建立为20%高水位线收费。" />}</Panel>
        <Panel id="setup-create" step="第3步 · 补建缺少的资料" title="新增Fee Plan"><form className="form-grid" onSubmit={(e) => void submitPlan(e)}><Field label="Plan Name"><input name="name" required placeholder="Profit Sharing 20%" /></Field><Field label="Fee Rate (%)"><input name="rate" type="number" step="0.01" min="0" max="100" defaultValue="20" required /></Field><p className="workflow-next">确认现有资料中没有本项后再保存。保存成功后，到第2步搜索核对新记录，再继续客户建档或账单出具。</p><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Fee Plan"}</button></form></Panel>
      </>}
      <div className="workflow-intro"><strong>资料就绪后继续</strong>FC、平台和收费计划用于客户与账户建档；收款公司用于账单出具。
        <div className="workflow-link-actions"><a className="text-link" href="#/clients">前往客户与账户</a><a className="text-link" href="#/invoices">前往账单出具</a></div>
      </div>
    </div>
  );
}
