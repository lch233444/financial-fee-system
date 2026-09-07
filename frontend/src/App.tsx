import { type ReactElement, useEffect, useRef, useState } from "react";
import { Building2, Calculator, Database, FileScan, Gauge, Menu, Power, ReceiptText, UsersRound, WalletCards, X } from "lucide-react";
import DashboardPage from "./pages/DashboardPage";
import SetupPage from "./pages/SetupPage";
import ClientsPage from "./pages/ClientsPage";
import ImportsPage from "./pages/ImportsPage";
import TransactionsPage from "./pages/TransactionsPage";
import SettlementsPage from "./pages/SettlementsPage";
import InvoicesPage from "./pages/InvoicesPage";
import SystemPage from "./pages/SystemPage";
import { api, shutdownFinancialSystem } from "./api";
import { SystemExitScreen } from "./components";

type Page = "dashboard" | "setup" | "clients" | "imports" | "transactions" | "settlements" | "invoices" | "system";
type ShutdownState = "idle" | "requesting" | "exiting" | "exited";

const pause = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

async function waitForLocalServerToStop(timeoutMilliseconds = 6000): Promise<boolean> {
  const deadline = Date.now() + timeoutMilliseconds;
  while (Date.now() < deadline) {
    await pause(250);
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      if (!response.ok) return true;
    } catch {
      return true;
    }
  }
  return false;
}

const navigation: Array<{ id: Page; label: string; sub: string; icon: typeof Gauge }> = [
  { id: "dashboard", label: "经营概览", sub: "Dashboard", icon: Gauge },
  { id: "setup", label: "基础设置", sub: "Master Data", icon: Building2 },
  { id: "clients", label: "客户与账户", sub: "Client / A/C", icon: UsersRound },
  { id: "imports", label: "账单导入", sub: "eMPF OCR", icon: FileScan },
  { id: "transactions", label: "资金与余额", sub: "Cash Flow", icon: WalletCards },
  { id: "settlements", label: "季度结算", sub: "Settlement", icon: Calculator },
  { id: "invoices", label: "Invoice与收款", sub: "Payment", icon: ReceiptText },
  { id: "system", label: "数据与系统", sub: "Backup", icon: Database },
];

export default function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const hasNavigated = useRef(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [toast, setToast] = useState("");
  const [shutdownState, setShutdownState] = useState<ShutdownState>("idle");
  const [shutdownMessage, setShutdownMessage] = useState("安全退出请求已发送，请稍候。");

  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(""), 3400);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    let cancelled = false;
    api<Array<{ id: number }>>("/api/companies")
      .then((companies) => {
        if (!cancelled && !hasNavigated.current && !companies.length) {
          setPage("setup");
          setToast("首次使用：请先建立Company、FC、Platform和Fee Plan");
        }
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, []);

  function navigate(target: Page) {
    hasNavigated.current = true;
    setPage(target);
    setSidebarOpen(false);
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  async function shutdown() {
    if (shutdownState !== "idle") return;
    const confirmed = window.confirm(
      "确定安全退出金融计划收费系统吗？\n\n系统会停止本地服务和本程序启动的Sol识别进程；尚未提交的表单内容将不会保存。",
    );
    if (!confirmed) return;
    setSidebarOpen(false);
    setShutdownState("requesting");
    try {
      const result = await shutdownFinancialSystem();
      setShutdownMessage(result.message);
      setShutdownState("exiting");
      const stopped = await waitForLocalServerToStop();
      setShutdownMessage(stopped ? "本地服务与Sol识别进程已停止。" : "安全退出指令已执行，可以关闭此页面。");
      setShutdownState("exited");
      window.setTimeout(() => window.close(), 500);
    } catch (error) {
      setShutdownState("idle");
      setToast(error instanceof Error ? `安全退出失败：${error.message}` : "安全退出失败，请重试");
    }
  }

  const pages: Record<Page, ReactElement> = {
    dashboard: <DashboardPage />,
    setup: <SetupPage notify={setToast} />,
    clients: <ClientsPage notify={setToast} />,
    imports: <ImportsPage notify={setToast} />,
    transactions: <TransactionsPage notify={setToast} />,
    settlements: <SettlementsPage notify={setToast} />,
    invoices: <InvoicesPage notify={setToast} />,
    system: <SystemPage notify={setToast} />,
  };

  if (shutdownState === "exiting" || shutdownState === "exited") {
    return <SystemExitScreen complete={shutdownState === "exited"} message={shutdownMessage} />;
  }

  return (
    <div className="app-shell">
      <aside className={sidebarOpen ? "sidebar open" : "sidebar"}>
        <div className="brand"><span className="brand-mark">F</span><div><strong>Financial Fee</strong><small>收费计算系统</small></div><button className="mobile-close" onClick={() => setSidebarOpen(false)}><X /></button></div>
        <nav>{navigation.map((item) => { const Icon = item.icon; return <button key={item.id} className={page === item.id ? "active" : ""} onClick={() => navigate(item.id)}><Icon size={20} /><span><b>{item.label}</b><small>{item.sub}</small></span></button>; })}</nav>
        <div className="sidebar-footer">
          <div className="sidebar-local-status"><span className="local-dot" /><span>仅本机运行<strong>127.0.0.1</strong></span></div>
          <button className="shutdown-button" type="button" disabled={shutdownState !== "idle"} onClick={() => void shutdown()}>
            <Power size={16} />{shutdownState === "requesting" ? "正在安全退出…" : "安全退出系统"}
          </button>
        </div>
      </aside>
      {sidebarOpen ? <button className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} aria-label="关闭菜单" /> : null}
      <main>
        <header className="topbar"><button className="menu-button" onClick={() => setSidebarOpen(true)}><Menu /></button><div>本机财务工作台</div><time>{new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" }).format(new Date())}</time></header>
        <div className="content">{pages[page]}</div>
      </main>
      {toast ? <div className="toast"><span>✓</span>{toast}</div> : null}
    </div>
  );
}
