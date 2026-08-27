import { useEffect, useState } from "react";
import { AlertTriangle, Banknote, BriefcaseBusiness, CircleDollarSign, UsersRound, WalletCards } from "lucide-react";
import { api } from "../api";
import { ErrorBanner, Loading, Money, PageHeader, Panel } from "../components";

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
  service_fee_generated: string;
  paid_amount: string;
  outstanding_amount: string;
};

export default function DashboardPage() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [fcs, setFcs] = useState<FCReport[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    const year = new Date().getFullYear();
    Promise.all([api<Dashboard>(`/api/dashboard?year=${year}`), api<FCReport[]>(`/api/reports/fc?year=${year}`)])
      .then(([summary, rows]) => {
        setDashboard(summary);
        setFcs(rows);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) return <ErrorBanner message={error} />;
  if (!dashboard) return <Loading />;
  return (
    <>
      <PageHeader title="经营概览" subtitle={`${dashboard.period.year}年度 · Finalized结算、Invoice与Payment统一口径`} />
      <div className="stats-grid">
        <article className="stat-card"><span className="stat-icon blue"><UsersRound /></span><div><small>管理客户</small><strong>{dashboard.active_clients}</strong><span>Active Client</span></div></article>
        <article className="stat-card"><span className="stat-icon teal"><WalletCards /></span><div><small>活跃账户</small><strong>{dashboard.active_accounts}</strong><span>Sub Account</span></div></article>
        <article className="stat-card"><span className="stat-icon amber"><CircleDollarSign /></span><div><small>产生服务费</small><strong><Money value={dashboard.generated_service_fee} /></strong><span>Service Fee</span></div></article>
        <article className="stat-card"><span className="stat-icon green"><Banknote /></span><div><small>已收金额</small><strong><Money value={dashboard.paid_amount} /></strong><span>Paid Amount</span></div></article>
        <article className="stat-card"><span className="stat-icon violet"><BriefcaseBusiness /></span><div><small>未收金额</small><strong><Money value={dashboard.outstanding_amount} /></strong><span>Outstanding</span></div></article>
        <article className="stat-card"><span className="stat-icon red"><AlertTriangle /></span><div><small>逾期账单</small><strong>{dashboard.overdue_invoices}</strong><span>Overdue Invoice</span></div></article>
      </div>

      <Panel title="FC业绩概览" subtitle={`${dashboard.period.year}年度 · Service Fee按结算锁定时的负责人归集`}>
        {fcs.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>FC</th><th>Company</th><th>管理客户</th><th>产生Service Fee</th><th>已收</th><th>未收</th></tr></thead>
              <tbody>
                {fcs.map((row) => (
                  <tr key={row.fc_id}>
                    <td><strong>{row.fc_name}</strong><small className="cell-note">{row.fc_code}</small></td>
                    <td>{row.company_name}</td>
                    <td>{row.active_client_count}</td>
                    <td><Money value={row.service_fee_generated} /></td>
                    <td><Money value={row.paid_amount} /></td>
                    <td><Money value={row.outstanding_amount} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="empty-state"><strong>尚无FC资料</strong><span>请先在基础设置中建立Company和FC。</span></div>}
      </Panel>
    </>
  );
}
