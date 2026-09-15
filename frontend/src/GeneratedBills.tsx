import { useMemo, useState } from "react";
import SearchableSelect from "./SearchableSelect";
import { EmptyState, Field, Money, Panel, Pagination } from "./components";
import { usePagination } from "./hooks";
import type { Invoice, Settlement } from "./types";

export function InvoiceHistoryLinks({ invoice }: { invoice: Invoice }) {
  return <>{invoice.replacement_invoice_id ? <a className="text-link cell-note" href={`#/invoices/${invoice.replacement_invoice_id}`}>查看替代账单 #{invoice.replacement_invoice_id}</a> : null}
    {invoice.original_invoice_id ? <a className="text-link cell-note" href={`#/invoices/${invoice.original_invoice_id}`}>查看原账单 #{invoice.original_invoice_id}</a> : null}</>;
}

export function SettlementBillingStatus({ settlement, invoices }: { settlement: Settlement; invoices: Invoice[] }) {
  const related = invoices.filter((invoice) => (invoice.settlement_ids?.length ? invoice.settlement_ids : [invoice.settlement_id]).includes(settlement.id));
  const voidBills = related.filter((invoice) => invoice.lifecycle_status === "VOID" && invoice.invoice_number);
  const history = voidBills.map((invoice) => <InvoiceHistoryLinks key={invoice.id} invoice={invoice} />);
  if (related.some((invoice) => invoice.lifecycle_status === "ISSUED")) return <><span className="status status-issued">账单已生成</span>{history}</>;
  if (related.some((invoice) => invoice.lifecycle_status === "ISSUING")) return <><span className="status status-issuing">出具中</span>{history}</>;
  if (related.some((invoice) => invoice.lifecycle_status === "DRAFT" && invoice.last_issue_status === "FAILED")) return <><span className="status status-void">出具失败</span>{history}</>;
  if (voidBills.length) return <><span className="status status-void">账单已作废</span>{history}</>;
  if (settlement.status === "VOID") return <span className="status status-void">计算已作废</span>;
  return <span><span className="status status-draft">收费已计算</span><small className="cell-note">{settlement.status === "DRAFT" ? "待锁定" : "已锁定，待出具账单"}</small></span>;
}

export default function GeneratedBills({ invoices }: { invoices: Invoice[] }) {
  const [year, setYear] = useState("");
  const [quarter, setQuarter] = useState("");
  const [clientId, setClientId] = useState("");
  const [status, setStatus] = useState("");
  const issued = useMemo(() => invoices.filter((item) => item.invoice_number && (item.lifecycle_status === "ISSUED" || item.lifecycle_status === "VOID")), [invoices]);
  const years = [...new Set(issued.map((item) => item.year))].sort((a, b) => b - a);
  const clients = [...new Map(issued.map((item) => [item.client_id, { value: String(item.client_id), label: `${item.client_name} · #${item.client_id}` }])).values()];
  const filtered = issued.filter((item) => (!year || item.year === Number(year)) && (!quarter || item.quarter === Number(quarter))
    && (!clientId || item.client_id === Number(clientId)) && (!status || item.lifecycle_status === status));
  const pages = usePagination(filtered, `${year}:${quarter}:${clientId}:${status}`);
  return <Panel id="generated-bills" title="已生成账单" subtitle="正式签发的客户缴费单及其作废历史；每张账单汇总同一客户季度的已锁定收费。">
    <div className="settlement-controls">
      <Field label="已生成账单年份"><select value={year} onChange={(e) => setYear(e.target.value)}><option value="">全部年份</option>{years.map((value) => <option key={value} value={value}>{value}</option>)}</select></Field>
      <Field label="已生成账单季度"><select value={quarter} onChange={(e) => setQuarter(e.target.value)}><option value="">全部季度</option>{[1,2,3,4].map((value) => <option key={value} value={value}>Q{value}</option>)}</select></Field>
      <Field group label="客户"><SearchableSelect label="已生成账单客户" value={clientId} onChange={setClientId} options={clients} placeholder="全部客户" /></Field>
      <Field label="生成账单状态"><select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">全部状态</option><option value="ISSUED">账单已生成</option><option value="VOID">账单已作废</option></select></Field>
    </div>
    {filtered.length ? <div className="table-wrap" tabIndex={0} role="region" aria-label="已生成账单"><table><thead><tr><th>账单编号</th><th>客户 / 期间</th><th>出具日期</th><th>应付服务费</th><th>状态</th><th>操作</th></tr></thead><tbody>{pages.rows.map((item) => <tr key={item.id}>
      <td>{item.invoice_number}</td><td>{item.client_name}<small className="cell-note">{item.year} Q{item.quarter}</small></td><td>{item.issue_date}</td><td><Money value={item.amount} /></td>
      <td><span className={`status status-${item.lifecycle_status.toLowerCase()}`}>{item.lifecycle_status === "ISSUED" ? "账单已生成" : "账单已作废"}</span></td>
      <td><a className="text-link" href={`#/invoices/${item.id}`}>查看账单及PDF</a><InvoiceHistoryLinks invoice={item} /></td>
    </tr>)}</tbody></table></div> : <EmptyState title="暂无已生成账单" detail="完成计算并锁定后，在账单出具页面签发缴费单。" />}
    <Pagination {...pages} />
  </Panel>;
}
