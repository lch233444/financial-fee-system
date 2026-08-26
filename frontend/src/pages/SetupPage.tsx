import { FormEvent, useState } from "react";
import { postJson } from "../api";
import { EmptyState, ErrorBanner, Field, PageHeader, Panel } from "../components";
import { useApiList } from "../hooks";
import type { Company, FC, FeePlan, Platform } from "../types";

type Tab = "company" | "fc" | "platform" | "plan";

export default function SetupPage({ notify }: { notify: (message: string) => void }) {
  const [tab, setTab] = useState<Tab>("company");
  const companies = useApiList<Company>("/api/companies");
  const fcs = useApiList<FC>("/api/fcs");
  const platforms = useApiList<Platform>("/api/platforms");
  const plans = useApiList<FeePlan>("/api/fee-plans");
  const [localError, setLocalError] = useState("");
  const error = localError || companies.error || fcs.error || platforms.error || plans.error;

  async function submitCompany(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/companies", {
        name: data.get("name"), code: data.get("code"), address: data.get("address") || null,
        contact: data.get("contact") || null, bank_information: data.get("bank") || null,
        cheque_information: data.get("cheque") || null, payment_terms_days: Number(data.get("terms") || 14),
      });
      event.currentTarget.reset();
      await companies.reload();
      notify("Company已保存");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Company保存失败"); }
  }

  async function submitFC(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/fcs", { company_id: Number(data.get("company_id")), name: data.get("name"), code: data.get("code") });
      event.currentTarget.reset();
      await fcs.reload();
      notify("FC已保存");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "FC保存失败"); }
  }

  async function submitPlatform(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/platforms", { name: data.get("name"), code: data.get("code"), trustee: data.get("trustee") || null });
      event.currentTarget.reset();
      await platforms.reload();
      notify("Platform已保存");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Platform保存失败"); }
  }

  async function submitPlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setLocalError("");
    try {
      await postJson("/api/fee-plans", {
        company_id: Number(data.get("company_id")), name: data.get("name"), code: data.get("code"),
        fee_rate_percent: data.get("rate"), calculation_method: "HIGH_WATER_MARK",
      });
      event.currentTarget.reset();
      await plans.reload();
      notify("Fee Plan已保存");
    } catch (err) { setLocalError(err instanceof Error ? err.message : "Fee Plan保存失败"); }
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

      {tab === "company" && <div className="split-layout"><Panel title="新增Company" subtitle="Invoice前缀、收款资料及默认付款期限"><form className="form-grid" onSubmit={(e) => void submitCompany(e)}><Field label="Company Name"><input name="name" required /></Field><Field label="Company Code"><input name="code" required maxLength={20} placeholder="AAA" /></Field><Field label="地址 Address"><input name="address" /></Field><Field label="联系方式 Contact"><input name="contact" /></Field><Field label="默认付款天数"><input name="terms" type="number" defaultValue="14" min="0" max="365" /></Field><Field label="银行收款信息"><textarea name="bank" rows={3} /></Field><Field label="支票信息"><textarea name="cheque" rows={3} /></Field><button className="primary" type="submit">保存Company</button></form></Panel><Panel title="现有Company">{companies.data.length ? <div className="table-wrap"><table><thead><tr><th>Code</th><th>Name</th><th>付款期限</th></tr></thead><tbody>{companies.data.map((x) => <tr key={x.id}><td><strong>{x.code}</strong></td><td>{x.name}</td><td>{x.payment_terms_days}天</td></tr>)}</tbody></table></div> : <EmptyState title="暂无Company" detail="请先建立公司主体。" />}</Panel></div>}
      {tab === "fc" && <div className="split-layout"><Panel title="新增FC"><form className="form-grid" onSubmit={(e) => void submitFC(e)}><Field label="Company"><select name="company_id" required defaultValue=""><option value="" disabled>请选择</option>{companies.data.map((x) => <option key={x.id} value={x.id}>{x.code} · {x.name}</option>)}</select></Field><Field label="FC Name"><input name="name" required /></Field><Field label="Initial Code"><input name="code" required placeholder="TW" /></Field><button className="primary" type="submit">保存FC</button></form></Panel><Panel title="现有FC">{fcs.data.length ? <div className="table-wrap"><table><thead><tr><th>Company</th><th>FC</th><th>Code</th></tr></thead><tbody>{fcs.data.map((x) => <tr key={x.id}><td>{x.company_name}</td><td>{x.name}</td><td>{x.code}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无FC" detail="建立后可关联客户并统计Service Fee。" />}</Panel></div>}
      {tab === "platform" && <div className="split-layout"><Panel title="新增Platform"><form className="form-grid" onSubmit={(e) => void submitPlatform(e)}><Field label="Platform Name"><input name="name" required /></Field><Field label="Platform Code"><input name="code" required /></Field><Field label="Trustee"><input name="trustee" /></Field><button className="primary" type="submit">保存Platform</button></form></Panel><Panel title="现有Platform">{platforms.data.length ? <div className="table-wrap"><table><thead><tr><th>Code</th><th>Platform</th><th>Trustee</th></tr></thead><tbody>{platforms.data.map((x) => <tr key={x.id}><td>{x.code}</td><td>{x.name}</td><td>{x.trustee || "-"}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无Platform" detail="账单导入也可创建待确认Platform。" />}</Panel></div>}
      {tab === "plan" && <div className="split-layout"><Panel title="新增Fee Plan"><form className="form-grid" onSubmit={(e) => void submitPlan(e)}><Field label="Company"><select name="company_id" required defaultValue=""><option value="" disabled>请选择</option>{companies.data.map((x) => <option key={x.id} value={x.id}>{x.code} · {x.name}</option>)}</select></Field><Field label="Plan Name"><input name="name" required placeholder="Profit Sharing 20%" /></Field><Field label="Plan Code"><input name="code" required placeholder="PS20" /></Field><Field label="Fee Rate (%)"><input name="rate" type="number" step="0.01" min="0" max="100" defaultValue="20" required /></Field><button className="primary" type="submit">保存Fee Plan</button></form></Panel><Panel title="现有Fee Plan">{plans.data.length ? <div className="table-wrap"><table><thead><tr><th>Company</th><th>Plan</th><th>Rate</th></tr></thead><tbody>{plans.data.map((x) => <tr key={x.id}><td>{x.company_name}</td><td>{x.name}</td><td>{Number(x.fee_rate_percent).toFixed(2)}%</td></tr>)}</tbody></table></div> : <EmptyState title="暂无Fee Plan" detail="当前计划可建立为20%高水位线收费。" />}</Panel></div>}
    </>
  );
}
