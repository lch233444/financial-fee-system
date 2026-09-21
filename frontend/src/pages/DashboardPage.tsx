import { useEffect, useState } from "react";
import { AlertTriangle, Banknote, BriefcaseBusiness, CircleDollarSign, UsersRound } from "lucide-react";
import { api } from "../api";
import { ErrorBanner, Field, Loading, Money, PageHeader } from "../components";
import SearchableSelect from "../SearchableSelect";

type Dashboard = {
  managed_clients: number;
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
        <article className="stat-card"><span className="stat-icon blue"><UsersRound /></span><div><small>在管客户</small><strong><a className="stat-value-link" aria-label={`查看${year}年${quarter ? `第${quarter}季度` : "全年"}在管客户：${dashboard.managed_clients}位`} href={`#/clients?year=${year}${quarter ? `&quarter=${quarter}` : ""}`}>{dashboard.managed_clients}</a></strong><span>所选期间实际受管 · 按客户去重</span></div></article>
        <article className="stat-card"><span className="stat-icon amber"><CircleDollarSign /></span><div><small>产生服务费</small><strong><Money value={dashboard.generated_service_fee} /></strong><span>Service Fee</span></div></article>
        <article className="stat-card"><span className="stat-icon green"><Banknote /></span><div><small>已收金额</small><strong><Money value={dashboard.paid_amount} /></strong><span>Paid Amount</span></div></article>
        <article className="stat-card"><span className="stat-icon violet"><BriefcaseBusiness /></span><div><small>未收金额</small><strong><Money value={dashboard.outstanding_amount} /></strong><span>Outstanding</span></div></article>
        <article className="stat-card"><span className="stat-icon red"><AlertTriangle /></span><div><small>逾期账单</small><strong>{dashboard.overdue_invoices}</strong><span>Overdue Invoice</span></div></article>
      </div>

      {dashboard.accounts_with_incomplete_management_dates > 0 ? <p role="status">有{dashboard.accounts_with_incomplete_management_dates}个账户缺少完整管理日期，未纳入期间统计；请在客户与账户中补齐后核对。</p> : null}
      </> : null}
    </>
  );
}
