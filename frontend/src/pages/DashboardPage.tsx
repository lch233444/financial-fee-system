import { useEffect, useState } from "react";
import { AlertTriangle, ArrowUpRight, Banknote, BriefcaseBusiness, Calculator, CircleDollarSign, FileScan, ReceiptText, UsersRound, WalletCards } from "lucide-react";
import { api } from "../api";
import { ErrorBanner, Field, Loading, Money, PageHeader, Panel } from "../components";

type Dashboard = {
  active_clients: number;
  active_accounts: number;
  generated_service_fee: string;
  paid_amount: string;
  outstanding_amount: string;
  overdue_invoices: number;
  period: { year: number; quarter: number | null };
};

type FCReport = {
  fc_id: number;
  fc_name: string;
  fc_code: string;
  company_name: string;
  active_client_count: number;
  charged_client_count: number;
  service_fee_generated: string;
};

export default function DashboardPage({ navigate }: { navigate?: (page: "imports" | "settlements" | "invoices") => void }) {
  const currentYear = new Date().getFullYear();
  const [year, setYear] = useState(currentYear);
  const [quarter, setQuarter] = useState("");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [fcs, setFcs] = useState<FCReport[]>([]);
  const [error, setError] = useState("");
  const periodLabel = `${year}年度${quarter ? ` Q${quarter}` : "全年"}`;

  useEffect(() => {
    let cancelled = false;
    const params = new URLSearchParams({ year: String(year) });
    if (quarter) params.set("quarter", quarter);
    const query = params.toString();
    setError("");
    setDashboard(null);
    setFcs([]);
    Promise.all([api<Dashboard>(`/api/dashboard?${query}`), api<FCReport[]>(`/api/reports/fc?${query}`)])
      .then(([summary, rows]) => {
        if (cancelled) return;
        setDashboard(summary);
        setFcs(rows);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => { cancelled = true; };
  }, [quarter, year]);

  return (
    <>
      <PageHeader title="经营概览" subtitle="掌握服务费、收款进度与客户管理情况。" />

      <div className="dashboard-period"><div><strong>{periodLabel}</strong><span>期间服务费及收款统计</span></div>
        <div className="settlement-controls dashboard-period-controls">
          <Field label="统计年度"><select value={year} onChange={(event) => setYear(Number(event.target.value))}>{Array.from({ length: 201 }, (_, index) => 2200 - index).map((value) => <option key={value} value={value}>{value}年</option>)}</select></Field>
          <Field label="统计季度"><select value={quarter} onChange={(event) => setQuarter(event.target.value)}><option value="">全年 · 全部季度</option><option value="1">Q1 · 第一季度</option><option value="2">Q2 · 第二季度</option><option value="3">Q3 · 第三季度</option><option value="4">Q4 · 第四季度</option></select></Field>
        </div>
      </div>
      {error ? <ErrorBanner message={error} /> : null}
      {!dashboard && !error ? <Loading /> : null}
      {dashboard ? <><div className="stats-grid">
        <article className="stat-card"><span className="stat-icon blue"><UsersRound /></span><div><small>管理客户</small><strong>{dashboard.active_clients}</strong><span>Active Client</span></div></article>
        <article className="stat-card"><span className="stat-icon teal"><WalletCards /></span><div><small>活跃账户</small><strong>{dashboard.active_accounts}</strong><span>Sub Account</span></div></article>
        <article className="stat-card"><span className="stat-icon amber"><CircleDollarSign /></span><div><small>产生服务费</small><strong><Money value={dashboard.generated_service_fee} /></strong><span>Service Fee</span></div></article>
        <article className="stat-card"><span className="stat-icon green"><Banknote /></span><div><small>已收金额</small><strong><Money value={dashboard.paid_amount} /></strong><span>Paid Amount</span></div></article>
        <article className="stat-card"><span className="stat-icon violet"><BriefcaseBusiness /></span><div><small>未收金额</small><strong><Money value={dashboard.outstanding_amount} /></strong><span>Outstanding</span></div></article>
        <article className="stat-card"><span className="stat-icon red"><AlertTriangle /></span><div><small>逾期账单</small><strong>{dashboard.overdue_invoices}</strong><span>Overdue Invoice</span></div></article>
      </div>

      {navigate ? <div className="quick-actions">
        <button onClick={() => navigate("imports")}><FileScan size={23} aria-hidden="true" /><span><strong>导入客户账单</strong><small>上传文件，核对余额与账户</small></span><ArrowUpRight size={18} aria-hidden="true" /></button>
        <button onClick={() => navigate("settlements")}><Calculator size={23} aria-hidden="true" /><span><strong>处理季度结算</strong><small>按账户计算并确认服务费</small></span><ArrowUpRight size={18} aria-hidden="true" /></button>
        <button onClick={() => navigate("invoices")}><ReceiptText size={23} aria-hidden="true" /><span><strong>开票与收款</strong><small>出具缴费单，登记付款</small></span><ArrowUpRight size={18} aria-hidden="true" /></button>
      </div> : null}

      <Panel title="FC服务统计" subtitle={`${periodLabel} · Service Fee按Settlement锁定时的FC归属汇总，不把Company收款列为FC业绩`}>
        {fcs.length ? (
          <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap">
            <table>
              <thead><tr><th>FC</th><th>Company</th><th>当前管理客户</th><th>期间收费客户</th><th>产生Service Fee</th></tr></thead>
              <tbody>
                {fcs.map((row) => (
                  <tr key={row.fc_id}>
                    <td><strong>{row.fc_name}</strong><small className="cell-note">{row.fc_code}</small></td>
                    <td>{row.company_name}</td>
                    <td>{row.active_client_count}</td>
                    <td>{row.charged_client_count}</td>
                    <td><Money value={row.service_fee_generated} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="empty-state"><strong>尚无FC资料</strong><span>请先在基础设置中建立Company和FC。</span></div>}
      </Panel></> : null}
    </>
  );
}
