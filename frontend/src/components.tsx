import { type PropsWithChildren, useEffect, useId, useRef } from "react";
import { AlertCircle, ArrowUpRight, Inbox, LoaderCircle } from "lucide-react";

export function PageHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="page-header">
      <div>
        <span className="page-eyebrow">FINANCIAL FEE / WORKSPACE</span>
        <h1>{title}</h1>
        <p>{subtitle}</p>
      </div>
    </div>
  );
}

export function Panel({ title, subtitle, children, className = "", id }: PropsWithChildren<{ title: string; subtitle?: string; className?: string; id?: string }>) {
  const titleId = useId();
  return (
    <section className={`panel ${className}`} id={id} aria-labelledby={titleId} tabIndex={id ? -1 : undefined}>
      <div className="panel-heading">
        <div>
          <h2 id={titleId}>{title}</h2>
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
      <span className="empty-state-icon"><Inbox size={23} aria-hidden="true" /></span>
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
  return <div className="loading" role="status"><LoaderCircle size={22} aria-hidden="true" /><span>正在读取本地数据…</span></div>;
}

export function ErrorBanner({ message }: { message: string }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => { element.current?.focus(); }, [message]);
  return <div ref={element} tabIndex={-1} className="error-banner" role="alert"><AlertCircle size={20} aria-hidden="true" /><div><strong>请检查后继续</strong><span>{message}</span></div></div>;
}

/** Page-local navigation does not change the app route or unmount any form. */
export function SectionNav({ items }: { items: Array<{ id: string; label: string }> }) {
  return <nav className="section-nav" aria-label="本页分区"><span>本页</span>{items.map((item) => <button type="button" key={item.id} onClick={() => {
    const target = document.getElementById(item.id);
    target?.scrollIntoView({ behavior: "auto", block: "start" });
    target?.focus({ preventScroll: true });
  }}>{item.label}<ArrowUpRight size={14} aria-hidden="true" /></button>)}</nav>;
}

export function Pagination({ page, total, pageSize, onChange }: { page: number; total: number; pageSize: number; onChange: (page: number) => void }) {
  const last = Math.max(1, Math.ceil(total / pageSize));
  if (last <= 1) return null;
  return <div className="pagination"><span role="status">第 {page} / {last} 页 · 共 {total} 条</span><div><button className="ghost" disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</button><button className="ghost" disabled={page >= last} onClick={() => onChange(page + 1)}>下一页</button></div></div>;
}
