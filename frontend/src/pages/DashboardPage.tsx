import { useEffect, useState } from "react";
import { AlertTriangle, Banknote, BriefcaseBusiness, CircleDollarSign, UsersRound } from "lucide-react";
import { api } from "../api";
import { ErrorBanner, Field, Loading, Money, PageHeader, Panel } from "../components";
import SearchableSelect from "../SearchableSelect";

type Dashboard = {
  managed_clients: number;
  client_overview: Array<{ client_id: number; client_name: string; fc_id: number | null; fc_name: string | null; fee_plans: Array<{ id: number; name: string; code: string }> }>;
  accounts_with_incomplete_management_dates: number;
  generated_service_fee: string;
  paid_amount: string;
  outstanding_amount: string;
  overdue_invoices: number;
  period: { year: number; quarter: number | null };
};

export default function DashboardPage() {
  const years = Array.from({ length: 201 }, (_, index) => 2000 + index);
  const [year, setYear] = useState(2026);
  const [quarter, setQuarter] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ year: String(year) });
    if (quarter) params.set("quarter", quarter);
    const query = params.toString();
    setError("");
    setDashboard(null);
    api<Dashboard>(`/api/dashboard?${query}`)
      .then((summary) => {
        if (cancelled) return;
        setDashboard(summary);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => { cancelled = true; };
  }, [quarter, year]);

  return (
    <>
      <PageHeader title="经营概览" subtitle="掌握服务费、收款进度与客户管理情况。" />

      <div className="dashboard-period">
        <div className="settlement-controls dashboard-period-controls">
          <Field group label="统计年度"><SearchableSelect label="统计年度" required value={String(year)} onChange={(value) => setYear(Number(value))} options={years.map((value) => ({ value: String(value), label: `${value}年` }))} optionLimit={years.length} centerSelectedOnOpen searchPlaceholder="输入年份搜索" /></Field>
          <Field label="统计季度"><select value={quarter} onChange={(event) => setQuarter(event.target.value)}><option value="">全年 · 全部季度</option><option value="1">Q1 · 第一季度</option><option value="2">Q2 · 第二季度</option><option value="3">Q3 · 第三季度</option><option value="4">Q4 · 第四季度</option></select></Field>
        </div>
      </div>
      {error ? <ErrorBanner message={error} /> : null}
      {!dashboard && !error ? <Loading /> : null}
      {dashboard ? <><div className="stats-grid dashboard-stats">
        <article className="stat-card"><span className="stat-icon blue"><UsersRound /></span><div><small>在管客户</small><strong>{dashboard.managed_clients}</strong><span>所选期间实际受管 · 按客户去重</span></div></article>
        <article className="stat-card"><span className="stat-icon amber"><CircleDollarSign /></span><div><small>产生服务费</small><strong><Money value={dashboard.generated_service_fee} /></strong><span>Service Fee</span></div></article>
        <article className="stat-card"><span className="stat-icon green"><Banknote /></span><div><small>已收金额</small><strong><Money value={dashboard.paid_amount} /></strong><span>Paid Amount</span></div></article>
        <article className="stat-card"><span className="stat-icon violet"><BriefcaseBusiness /></span><div><small>未收金额</small><strong><Money value={dashboard.outstanding_amount} /></strong><span>Outstanding</span></div></article>
        <article className="stat-card"><span className="stat-icon red"><AlertTriangle /></span><div><small>逾期账单</small><strong>{dashboard.overdue_invoices}</strong><span>Overdue Invoice</span></div></article>
      </div>

      {dashboard.accounts_with_incomplete_management_dates > 0 ? <p role="status">有{dashboard.accounts_with_incomplete_management_dates}个账户缺少完整管理日期，未纳入期间统计；请在客户与账户中补齐后核对。</p> : null}
      <Panel title="客户总览" subtitle="所选期间内实际受管的客户；FC及收费计划显示当前档案关系，无收费或尚未出账单也计入。">
        {dashboard.client_overview.length ? (
          <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap">
            <table>
              <thead><tr><th>客户</th><th>FC</th><th>收费计划</th></tr></thead>
              <tbody>
                {dashboard.client_overview.map((row) => (
                  <tr key={row.client_id}>
                    <td><strong>{row.client_name}</strong><small className="cell-note">客户 #{row.client_id}</small></td>
                    <td>{row.fc_name || "待补全FC"}</td>
                    <td>{row.fee_plans.length ? row.fee_plans.map((plan) => <small className="cell-note" key={plan.id}>{plan.name} · {plan.code}</small>) : "待补全收费计划"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="empty-state"><strong>所选期间没有在管客户</strong><span>有受管子账户且管理期间与所选期间重叠的客户才计入。</span></div>}
      </Panel></> : null}
    </>
  );
}
