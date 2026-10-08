import { formatDate } from "./types";
import { todayIso } from "./hooks";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Building2, Calculator, ChevronRight, Database, Gauge, Info, Landmark, Menu, ReceiptText, ShieldCheck, UsersRound, WalletCards, X } from "lucide-react";
import { api } from "./api";
import { Loading } from "./components";
import PageBoundary from "./PageBoundary";
import { useBrowserSession } from "./useBrowserSession";

const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const SetupPage = lazy(() => import("./pages/SetupPage"));
const ClientsPage = lazy(() => import("./pages/ClientsPage"));
const TransactionsPage = lazy(() => import("./pages/TransactionsPage"));
const SettlementsPage = lazy(() => import("./pages/SettlementsPage"));
const InvoicesPage = lazy(() => import("./pages/InvoicesPage"));
const SystemPage = lazy(() => import("./pages/SystemPage"));

type Page = "dashboard" | "setup" | "clients" | "transactions" | "settlements" | "invoices" | "payments" | "system";
const navigation = [
  { id: "dashboard", label: "经营概览", sub: "期间经营情况", icon: Gauge, group: "工作台" },
  { id: "clients", label: "客户与账户", sub: "客户资料与账户", icon: UsersRound, group: "账单制作" },
  { id: "transactions", label: "资金与余额", sub: "资金记录与历史结余", icon: WalletCards, group: "账单制作" },
  { id: "settlements", label: "账单计算", sub: "核算并复核收费", icon: Calculator, group: "账单制作" },
  { id: "invoices", label: "账单出具", sub: "出具并归档账单", icon: ReceiptText, group: "账单制作" },
  { id: "payments", label: "收款情况", sub: "收款登记与核对", icon: ReceiptText, group: "收款情况" },
  { id: "setup", label: "基础设置", sub: "公司、中介人、平台及计划", icon: Building2, group: "管理" },
  { id: "system", label: "数据与系统", sub: "备份、恢复与识别设置", icon: Database, group: "管理" },
] as const;

function locationPage(): Page {
  const id = window.location.hash.slice(2).split(/[/?]/)[0];
  if (id === "imports") return "transactions";
  return navigation.some((item) => item.id === id) ? id as Page : "dashboard";
}

export default function App() {
  useBrowserSession();
  const [page, setPage] = useState<Page>(locationPage);
  const [route, setRoute] = useState(window.location.hash);
  const routeParams = new URLSearchParams(route.split("?")[1] || "");
  const hasNavigated = useRef(Boolean(window.location.hash));
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [toast, setToast] = useState("");
  const menuButton = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const wasSidebarOpen = useRef(false);
  const focusAfterClose = useRef<"menu" | "content">("menu");
  const current = navigation.find((item) => item.id === page)!;
  const workflow = ["clients", "transactions", "settlements", "invoices", "payments"] as const;
  const workflowIndex = workflow.findIndex((id) => id === page);
  const nextPage = workflowIndex >= 0 && workflowIndex < workflow.length - 1
    ? navigation.find((item) => item.id === workflow[workflowIndex + 1]) : undefined;

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(""), 8000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    const changed = () => {
      hasNavigated.current = true;
      setPage(locationPage());
      setRoute(window.location.hash);
      focusAfterClose.current = "content";
      setSidebarOpen(false);
      window.scrollTo({ top: 0, behavior: "auto" });
      content.current?.focus({ preventScroll: true });
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);

  useEffect(() => { document.title = `${current.label} · 金融计划收费系统`; }, [current.label]);

  useEffect(() => {
    let cancelled = false;
    api<Array<{ id: number }>>("/api/companies").then((companies) => {
      if (!cancelled && !hasNavigated.current && !companies.length) {
        window.history.replaceState(null, "", "#/setup");
        setPage("setup");
        setToast("首次使用：请先建立收款公司、中介人、投资平台和收费计划");
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!sidebarOpen) {
      if (!wasSidebarOpen.current) return;
      wasSidebarOpen.current = false;
      const frame = window.requestAnimationFrame(() => {
        (focusAfterClose.current === "content" ? content.current : menuButton.current)?.focus({ preventScroll: true });
      });
      return () => window.cancelAnimationFrame(frame);
    }
    wasSidebarOpen.current = true;
    focusAfterClose.current = "menu";
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = window.requestAnimationFrame(() => sidebar.current?.querySelector<HTMLButtonElement>(".mobile-close")?.focus());
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSidebarOpen(false);
      if (event.key !== "Tab") return;
      const controls = Array.from(sidebar.current?.querySelectorAll<HTMLElement>("button, a[href]") ?? []);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!sidebar.current?.contains(document.activeElement)) { event.preventDefault(); (event.shiftKey ? last : first)?.focus(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    const resize = () => { if (window.innerWidth > 1000) setSidebarOpen(false); };
    window.addEventListener("keydown", keydown);
    window.addEventListener("resize", resize);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.cancelAnimationFrame(frame);
      window.removeEventListener("keydown", keydown);
      window.removeEventListener("resize", resize);
    };
  }, [sidebarOpen]);

  function navigate(target: Page) {
    hasNavigated.current = true;
    focusAfterClose.current = "content";
    if (target !== page) window.location.hash = `/${target}`;
    setPage(target);
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: "auto" });
    content.current?.focus({ preventScroll: true });
  }

  const pages = {
    dashboard: <DashboardPage />,
    setup: <SetupPage notify={setToast} />,
    clients: <ClientsPage notify={setToast} initialYear={routeParams.get("year") || undefined} initialQuarter={routeParams.get("quarter") || undefined} />,
    transactions: <TransactionsPage notify={setToast} />,
    settlements: <SettlementsPage notify={setToast} />,
    invoices: <InvoicesPage mode="issue" notify={setToast} />,
    payments: <InvoicesPage mode="payment" notify={setToast} />,
    system: <SystemPage notify={setToast} />,
  };

  return <div className="app-shell">
    <a className="skip-link" href="#workspace" onClick={(event) => { event.preventDefault(); content.current?.focus(); }}>跳到主要内容</a>
    <aside ref={sidebar} id="app-navigation" className={sidebarOpen ? "sidebar open" : "sidebar"} aria-label="主导航">
      <div className="brand"><span className="brand-mark"><Landmark size={25} aria-hidden="true" /></span><div><strong>金融收费</strong><small>金融计划收费系统</small></div><button className="mobile-close icon-button" aria-label="关闭导航" onClick={() => setSidebarOpen(false)}><X /></button></div>
      <nav aria-label="功能页面">{["工作台", "账单制作", "收款情况", "管理"].map((group) => <div className="nav-group" key={group}><span className="nav-group-label">{group}</span>{navigation.filter((item) => item.group === group).map((item) => {
        const Icon = item.icon;
        return <button key={item.id} className={page === item.id ? "active" : ""} aria-current={page === item.id ? "page" : undefined} onClick={() => navigate(item.id)}><Icon size={20} aria-hidden="true" /><span><b>{item.label}</b><small>{item.sub}</small></span>{page === item.id ? <ChevronRight className="nav-arrow" size={16} aria-hidden="true" /> : null}</button>;
      })}</div>)}</nav>
      <div className="sidebar-footer"><ShieldCheck size={19} aria-hidden="true" /><span>本机财务工作台<small>本机使用 · 港币</small></span></div>
    </aside>
    {sidebarOpen ? <button className="sidebar-backdrop" tabIndex={-1} onClick={() => setSidebarOpen(false)} aria-label="关闭菜单" /> : null}
    <main inert={sidebarOpen}>
      <header className="topbar"><button ref={menuButton} className="menu-button icon-button" aria-label="打开导航" aria-controls="app-navigation" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)}><Menu /></button><div className="breadcrumb"><span>{current.group}</span><ChevronRight size={14} aria-hidden="true" /><strong>{current.label}</strong></div><time>{formatDate(todayIso())} {new Intl.DateTimeFormat("zh-CN", { weekday: "short" }).format(new Date())}</time></header>
      <div id="workspace" ref={content} tabIndex={-1} className={`content page-${page}`}><PageBoundary key={page}><Suspense fallback={<Loading />}>{pages[page]}{nextPage ? <nav className="module-next" aria-label="下一模块"><p>本页需要保留的资料请先保存；准备好后可继续下一模块。</p><button type="button" className="primary" onClick={() => navigate(nextPage.id)}>下一步：{nextPage.label}<ChevronRight size={18} aria-hidden="true" /></button></nav> : null}</Suspense></PageBoundary></div>
      <footer className="workspace-footer"><span>金融收费 · 本机运行</span><span>港币 · 逐账户独立核算</span></footer>
    </main>
    {toast ? <div className="toast" role="status" aria-live="polite"><Info size={20} aria-hidden="true" /><span>{toast}</span><button className="icon-button" aria-label="关闭提示" onClick={() => setToast("")}><X size={18} /></button></div> : null}
  </div>;
}
