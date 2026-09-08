import type { PropsWithChildren } from "react";

export function PageHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="page-header">
      <div>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
    </div>
  );
}

export function Panel({ title, subtitle, children, className = "" }: PropsWithChildren<{ title: string; subtitle?: string; className?: string }>) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
      </div>
      {children}
    </section>
  );
}

export function Field({ label, hint, children, group = false }: PropsWithChildren<{ label: string; hint?: string; group?: boolean }>) {
  const Container = group ? "div" : "label";
  return (
    <Container className="field">
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </Container>
  );
}

export function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

export function StatusBadge({ value }: { value: string }) {
  const key = value.toLowerCase().replaceAll("_", "-");
  const labels: Record<string, string> = {
    active: "已启用",
    draft: "草稿",
    finalized: "已确认",
    issued: "已出具",
    void: "已作废",
    confirmed: "已入账",
    "needs-review": "待复核",
    paid: "已付清",
    unpaid: "未付款",
    "partially-paid": "部分付款",
    overdue: "已逾期",
    closed: "已结束",
    ready: "已连接",
    "signed-out": "未登录",
    "auth-pending": "等待登录",
    "login-pending": "等待登录",
    unavailable: "不可用",
    error: "连接异常",
    checking: "检查中",
    matched: "识别一致",
  };
  return <span className={`status status-${key}`}>{labels[key] || value}</span>;
}

export function Money({ value, emphasis = false }: { value: string | number; emphasis?: boolean }) {
  const numeric = Number(value || 0);
  return <span className={emphasis ? "money emphasis" : "money"}>HKD {numeric.toLocaleString("en-HK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>;
}

export function Loading() {
  return <div className="loading">正在读取本地数据...</div>;
}

export function ErrorBanner({ message }: { message: string }) {
  return <div className="error-banner">{message}</div>;
}

export function SystemExitScreen({ complete, message }: { complete: boolean; message: string }) {
  return (
    <main className="system-exit-screen" role="status" aria-live="polite">
      <section className="system-exit-card">
        <div className={complete ? "system-exit-mark complete" : "system-exit-mark"} aria-hidden="true">
          {complete ? "✓" : <span />}
        </div>
        <h1>{complete ? "系统已安全退出" : "正在安全退出系统"}</h1>
        <p>{message}</p>
        <small>{complete ? "若页面没有自动关闭，现在可以直接关闭此页面。" : "正在停止本地服务与本程序启动的Sol识别进程，请稍候。"}</small>
      </section>
    </main>
  );
}
