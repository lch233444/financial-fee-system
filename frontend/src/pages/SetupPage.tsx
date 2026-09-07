import { FormEvent, useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { api, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, PageHeader, Panel } from "../components";
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
  const [localError, setLocalError] = useState("");
  const formAction = useFormAction(setLocalError, notify);
  const [deletingKey, setDeletingKey] = useState("");
  const deleteInFlight = useRef(false);
  const error = localError || companies.error || fcs.error || platforms.error || plans.error;

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
      save: (data) => postJson("/api/fcs", { company_id: Number(data.get("company_id")), name: data.get("name"), code: data.get("code") }),
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
        company_id: Number(data.get("company_id")), name: data.get("name"), code: data.get("code"),
        fee_rate_percent: data.get("rate"), calculation_method: "HIGH_WATER_MARK",
      }),
      refresh: plans.reload,
      message: "Fee Plan已保存",
    });
  }

  return (
    <>
      <PageHeader title="基础设置" subtitle="建立Company、FC、Platform和Fee Plan后才能启用客户账户" />
      {error ? <ErrorBanner message={error} /> : null}
      <div className="tabs">
        <button className={tab === "company" ? "active" : ""} onClick={() => setTab("company")}>Company</button>
        <button className={tab === "fc" ? "active" : ""} onClick={() => setTab("fc")}>FC</button>
        <button className={tab === "platform" ? "active" : ""} onClick={() => setTab("platform")}>Platform</button>
        <button className={tab === "plan" ? "active" : ""} onClick={() => setTab("plan")}>Fee Plan</button>
      </div>

      {tab === "company" && <div className="split-layout"><Panel title="新增Company" subtitle="Company全名用于Invoice编号第一段；Code仅作基础资料识别"><form className="form-grid" onSubmit={(e) => void submitCompany(e)}><Field label="Company Name（Invoice编号第一段）"><input name="name" required /></Field><Field label="Company Code（仅内部标识）"><input name="code" required maxLength={20} /></Field><Field label="地址 Address"><input name="address" /></Field><Field label="联系方式 Contact"><input name="contact" /></Field><Field label="默认付款天数"><input name="terms" type="number" defaultValue="14" min="0" max="365" /></Field><Field label="银行收款信息"><textarea name="bank" rows={3} /></Field><Field label="支票信息"><textarea name="cheque" rows={3} /></Field><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Company"}</button></form></Panel><Panel title="现有Company">{companies.data.length ? <div className="table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>付款期限</th><th className="master-actions-column">操作</th></tr></thead><tbody>{companies.data.map((x) => <tr key={x.id}><td><strong>{x.code}</strong></td><td>{x.name}</td><td>{x.payment_terms_days}天</td><td className="master-actions-column">{deleteButton("Company", "/api/companies", x.id, `${x.code} · ${x.name}`)}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无Company" detail="请先建立公司主体。" />}</Panel></div>}
      {tab === "fc" && <div className="split-layout"><Panel title="新增FC"><form className="form-grid" onSubmit={(e) => void submitFC(e)}><Field label="Company"><select name="company_id" required defaultValue=""><option value="" disabled>请选择</option>{companies.data.map((x) => <option key={x.id} value={x.id}>{x.code} · {x.name}</option>)}</select></Field><Field label="FC Name"><input name="name" required /></Field><Field label="中介人名字缩写"><input name="code" required /></Field><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存FC"}</button></form></Panel><Panel title="现有FC">{fcs.data.length ? <div className="table-wrap"><table><thead><tr><th>Company</th><th>FC</th><th>中介人缩写</th><th className="master-actions-column">操作</th></tr></thead><tbody>{fcs.data.map((x) => <tr key={x.id}><td>{x.company_name}</td><td>{x.name}</td><td>{x.code}</td><td className="master-actions-column">{deleteButton("FC", "/api/fcs", x.id, `${x.code} · ${x.name}`)}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无FC" detail="建立后可关联客户并统计Service Fee。" />}</Panel></div>}
      {tab === "platform" && <div className="split-layout"><Panel title="新增Platform" subtitle="Trustee是MPF计划受托机构，不是FC/中介人，也不参与Invoice编号"><form className="form-grid" onSubmit={(e) => void submitPlatform(e)}><Field label="Platform Name"><input name="name" required /></Field><Field label="Platform Code"><input name="code" required /></Field><Field label="Trustee（MPF计划受托机构）"><input name="trustee" /></Field><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Platform"}</button></form></Panel><Panel title="现有Platform">{platforms.data.length ? <div className="table-wrap"><table><thead><tr><th>Code</th><th>Platform</th><th>Trustee（MPF受托机构）</th><th className="master-actions-column">操作</th></tr></thead><tbody>{platforms.data.map((x) => <tr key={x.id}><td>{x.code}</td><td>{x.name}</td><td>{x.trustee || "-"}</td><td className="master-actions-column">{deleteButton("Platform", "/api/platforms", x.id, `${x.code} · ${x.name}`)}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无Platform" detail="账单导入也可创建待确认Platform。" />}</Panel></div>}
      {tab === "plan" && <div className="split-layout"><Panel title="新增Fee Plan"><form className="form-grid" onSubmit={(e) => void submitPlan(e)}><Field label="Company"><select name="company_id" required defaultValue=""><option value="" disabled>请选择</option>{companies.data.map((x) => <option key={x.id} value={x.id}>{x.code} · {x.name}</option>)}</select></Field><Field label="Plan Name"><input name="name" required placeholder="Profit Sharing 20%" /></Field><Field label="Plan Code"><input name="code" required placeholder="PS20" /></Field><Field label="Fee Rate (%)"><input name="rate" type="number" step="0.01" min="0" max="100" defaultValue="20" required /></Field><button className="primary" type="submit" disabled={formAction.pending}>{formAction.pending ? "保存中..." : "保存Fee Plan"}</button></form></Panel><Panel title="现有Fee Plan">{plans.data.length ? <div className="table-wrap"><table><thead><tr><th>Company</th><th>Plan</th><th>Rate</th><th className="master-actions-column">操作</th></tr></thead><tbody>{plans.data.map((x) => <tr key={x.id}><td>{x.company_name}</td><td>{x.name}</td><td>{Number(x.fee_rate_percent).toFixed(2)}%</td><td className="master-actions-column">{deleteButton("Fee Plan", "/api/fee-plans", x.id, `${x.code} · ${x.name}`)}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无Fee Plan" detail="当前计划可建立为20%高水位线收费。" />}</Panel></div>}
    </>
  );
}
