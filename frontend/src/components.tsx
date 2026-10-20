import { type PropsWithChildren, useEffect, useId, useRef } from "react";
import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";
import { HelpTip } from "./Progressive";

export function PageHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="page-header">
      <div>
        <h1 aria-label={title}>{title}<HelpTip title={title}>{subtitle}</HelpTip></h1>
      </div>
    </div>
  );
}

export function Panel({ title, subtitle, children, className = "", id, step }: PropsWithChildren<{ title: string; subtitle?: string; className?: string; id?: string; step?: string }>) {
  const titleId = useId();
  return (
    <section className={`panel ${className}`} id={id} aria-labelledby={titleId} tabIndex={id ? -1 : undefined}>
      <div className="panel-heading">
        <div>
          <h2 id={titleId} aria-label={title}>{title}{subtitle ? <HelpTip title={title}>{step ? <p>{step}</p> : null}{subtitle}</HelpTip> : null}</h2>
        </div>
      </div>
      {children}
    </section>
  );
}

/** Visible task instructions, not a claim that a financial action has completed. */
export function WorkflowStep({ number, title, detail }: { number: number; title: string; detail?: string }) {
  return <div className="workflow-step"><span className="workflow-step-label">{number}</span><div><h3 aria-label={title}>{title}{detail ? <HelpTip title={title}>{detail}</HelpTip> : null}</h3></div></div>;
}

export function WorkflowSection({ id, title, description, children, compact = false }: PropsWithChildren<{ id: string; title: string; description: string; compact?: boolean }>) {
  const titleId = useId();
  return <section className="workflow-section" id={id} aria-labelledby={compact ? undefined : titleId} aria-label={compact ? title : undefined} tabIndex={-1}>
    {compact ? null : <header className="workflow-section-heading"><h2 id={titleId} aria-label={title}>{title}<HelpTip title={title}>{description}</HelpTip></h2></header>}
    {children}
  </section>;
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
      <span className="empty-state-icon"><Inbox size={20} aria-hidden="true" /></span>
      <strong>{title}</strong>
      <HelpTip title={title}>{detail}</HelpTip>
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
    "not-required": "无需付款",
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
    issuing: "出具中",
    failed: "失败",
    open: "处理中",
    completed: "已完成",
  };
  return <span className={`status status-${key}`}>{labels[key] || value}</span>;
}

export function Money({ value, emphasis = false }: { value: string | number; emphasis?: boolean }) {
  const numeric = Number(value || 0);
  return <span className={emphasis ? "money emphasis" : "money"}>港币 {numeric.toLocaleString("en-HK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>;
}

export function Loading() {
  return <div className="loading" role="status"><LoaderCircle size={22} aria-hidden="true" /><span>正在读取本地数据…</span></div>;
}

export function ErrorBanner({ message }: { message: string }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => { element.current?.focus(); }, [message]);
  return <div ref={element} tabIndex={-1} className="error-banner" role="alert"><AlertCircle size={20} aria-hidden="true" /><div><strong>请检查后继续</strong><span>{message}</span></div></div>;
}

export function Pagination({ page, total, pageSize, onChange }: { page: number; total: number; pageSize: number; onChange: (page: number) => void }) {
  const last = Math.max(1, Math.ceil(total / pageSize));
  if (last <= 1) return null;
  return <div className="pagination"><span role="status">第 {page} / {last} 页 · 共 {total} 条</span><div><button className="ghost" disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</button><button className="ghost" disabled={page >= last} onClick={() => onChange(page + 1)}>下一页</button></div></div>;
}
