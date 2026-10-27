import { isAnnualDataset, cycleText } from "../settlementCycle";
import { TaskTabs, TaskView } from "../TaskTabs";
import { useEffect, useState } from "react";
import { AlertTriangle, Banknote, BriefcaseBusiness, CircleDollarSign, UsersRound } from "lucide-react";
import { api } from "../api";
import { ErrorBanner, Field, Loading, Money, PageHeader, WorkflowSection } from "../components";
import { useApiList } from "../hooks";
import { ArrowRight, Calculator, FileInput, WalletCards } from "lucide-react";
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
  const fcs = useApiList<{ id: number }>("/api/fcs");
  const platforms = useApiList<{ id: number }>("/api/platforms");
  const plans = useApiList<{ id: number }>("/api/fee-plans");
  const years = Array.from({ length: 201 }, (_, index) => 2000 + index);
  const [view, setView] = useState<"home-tasks" | "home-overview">("home-tasks");
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
    <div className="workflow-page dashboard-workflow">
      <PageHeader title="工作台" subtitle="选择今天要办理的任务，或查看本期经营情况。" />
      <TaskTabs label="工作台内容" value={view} onChange={setView} items={[{ id: "home-tasks", label: "办理业务" }, { id: "home-overview", label: "经营概览" }]} />
      <TaskView id="home-tasks" active={view === "home-tasks"}>
      <section className="workbench-start" aria-labelledby="workbench-title">
        <div className="workbench-heading"><div><span className="eyebrow">日常办理</span><h2 id="workbench-title">今天从哪里开始？</h2><p>新客户先建档，已有客户从本次需要的环节继续。</p></div><a className="text-link" href="#/setup">基础资料设置 <ArrowRight size={17} aria-hidden="true" /></a></div>
        <div className="task-cards">
          <a href="#/clients"><FileInput aria-hidden="true" /><strong>客户建档与查询</strong><span>导入原件或手动建立客户、账户。</span><b>进入客户与账户 <ArrowRight size={16} aria-hidden="true" /></b></a>
          <a href="#/transactions"><WalletCards aria-hidden="true" /><strong>{cycleText("准备本季资料")}</strong><span>补充结余、供款、加款及取款凭证。</span><b>进入资金与余额 <ArrowRight size={16} aria-hidden="true" /></b></a>
          <a href="#/settlements"><Calculator aria-hidden="true" /><strong>计算收费 / 导出 Excel</strong><span>逐账户计算，复核锁定后导出明细。</span><b>进入账单计算 <ArrowRight size={16} aria-hidden="true" /></b></a>
        </div>
        <div className="workbench-secondary"><a href="#/invoices">出具客户账单 <ArrowRight size={16} aria-hidden="true" /></a><a href="#/payments">登记收款 / 查看凭证 <ArrowRight size={16} aria-hidden="true" /></a></div>
        {fcs.error || platforms.error || plans.error ? <p className="workbench-readiness" role="status">基础资料状态读取失败，请到基础设置核对后开始办理。</p>
          : fcs.loading || platforms.loading || plans.loading ? <p className="workbench-readiness" role="status">正在检查基础资料…</p>
          : !fcs.data.length || !platforms.data.length || !plans.data.length ? <p className="workbench-readiness" role="status"><strong>首次使用，先补充：</strong>{[!fcs.data.length && "中介人", !platforms.data.length && "投资平台", !plans.data.length && "收费计划"].filter(Boolean).join("、")}。<a href="#/setup">去设置</a>。收款公司可在出具账单前确认。</p> : null}
      </section>
      </TaskView>
      <TaskView id="home-overview" active={view === "home-overview"}>
      <div className="overview-heading"><h2>经营概览</h2><span>选择期间查看统计</span></div>
      <WorkflowSection id="dashboard-period" title="统计期间" description={isAnnualDataset() ? "选择并确认统计年度。当前包按自然年度结算。" : "选择并确认年度，可进一步按季度查看。"}>
      <div className="dashboard-period">
        <div className="settlement-controls dashboard-period-controls">
          <Field group label="统计年度"><SearchableSelect label="统计年度" required value={String(year)} onChange={(value) => setYear(Number(value))} options={years.map((value) => ({ value: String(value), label: `${value}年` }))} optionLimit={years.length} centerSelectedOnOpen searchPlaceholder="输入年份搜索" /></Field>
          {!isAnnualDataset() && <Field label={cycleText("统计季度")}><select value={quarter} onChange={(event) => setQuarter(event.target.value)}><option value="">{cycleText("全年 · 全部季度")}</option><option value="1">{cycleText("第一季度")}</option><option value="2">{cycleText("第二季度")}</option><option value="3">{cycleText("第三季度")}</option><option value="4">{cycleText("第四季度")}</option></select></Field>}
        </div>
      </div>
      </WorkflowSection>
      <WorkflowSection id="dashboard-summary" title="本期概况" description={`当前期间：${year}年${quarter ? `第${quarter}季度` : "全年"}。核对服务费、已收、未收及逾期情况；点击在管客户数字可查看对应期间客户。`}>
      {error ? <ErrorBanner message={error} /> : null}
      {!dashboard && !error ? <Loading /> : null}
      {dashboard ? <><div className="stats-grid dashboard-stats">
        <article className="stat-card"><span className="stat-icon blue"><UsersRound /></span><div><small>在管客户</small><strong><a className="stat-value-link" aria-label={`查看${year}年${quarter ? `第${quarter}季度` : "全年"}在管客户：${dashboard.managed_clients}位`} href={`#/clients?year=${year}${quarter ? `&quarter=${quarter}` : ""}`}>{dashboard.managed_clients}</a></strong><span>所选期间实际受管 · 按客户去重</span></div></article>
        <article className="stat-card"><span className="stat-icon amber"><CircleDollarSign /></span><div><small>产生服务费</small><strong><Money value={dashboard.generated_service_fee} /></strong><span>已锁定结算 · 排除已作废账单</span></div></article>
        <article className="stat-card"><span className="stat-icon green"><Banknote /></span><div><small>已收金额</small><strong><Money value={dashboard.paid_amount} /></strong><span>已确认收款</span></div></article>
        <article className="stat-card"><span className="stat-icon violet"><BriefcaseBusiness /></span><div><small>未收金额</small><strong><Money value={dashboard.outstanding_amount} /></strong><span>尚待收取</span></div></article>
        <article className="stat-card"><span className="stat-icon red"><AlertTriangle /></span><div><small>逾期账单</small><strong>{dashboard.overdue_invoices}</strong><span>超过到期日未结清</span></div></article>
      </div>

      {dashboard.accounts_with_incomplete_management_dates > 0 ? <p role="status">有{dashboard.accounts_with_incomplete_management_dates}个账户缺少完整管理日期，未纳入期间统计；请在客户与账户中补齐后核对。</p> : null}
      </> : null}
      </WorkflowSection>
      <WorkflowSection id="dashboard-next" title="相关查询" description="查询会带入本页所选年季。">
        <div className="workflow-links">
          <a href={`#/clients?year=${year}${quarter ? `&quarter=${quarter}` : ""}`}><strong>查看本期客户与账户</strong><span>核对在管客户及管理日期；新增客户使用该页的新增入口。</span></a>
        </div>
      </WorkflowSection>
      </TaskView>
    </div>
  );
}
