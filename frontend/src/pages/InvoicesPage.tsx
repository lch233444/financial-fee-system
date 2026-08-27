import { FormEvent, useMemo, useState } from "react";
import { Ban, FileDown, FilePlus2, ReceiptText } from "lucide-react";
import { download, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Money, PageHeader, Panel, StatusBadge } from "../components";
import { todayIso, useApiList } from "../hooks";
import type { Invoice, Settlement } from "../types";

export default function InvoicesPage({ notify }: { notify: (message: string) => void }) {
  const invoices = useApiList<Invoice>("/api/invoices");
  const settlements = useApiList<Settlement>("/api/settlements");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const selected = invoices.data.find((item) => item.id === selectedId) || null;
  const invoiceCandidates = useMemo(() => settlements.data.filter((item) => item.status === "FINALIZED" && Number(item.service_fee) > 0 && !invoices.data.some((invoice) => invoice.settlement_id === item.id && invoice.lifecycle_status !== "VOID")), [settlements.data, invoices.data]);

  async function createDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const result = await postJson<Invoice>("/api/invoices", { settlement_id: Number(data.get("settlement_id")), language: data.get("language") });
      await invoices.reload();
      setSelectedId(result.id);
      notify("Invoice Draft已建立");
    } catch (err) { setError(err instanceof Error ? err.message : "建立失败"); }
  }

  async function issue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const data = new FormData(event.currentTarget);
    try {
      await postJson(`/api/invoices/${selected.id}/issue`, { issue_date: data.get("issue_date"), due_date: data.get("due_date") || null, language: data.get("language") });
      await invoices.reload();
      notify("Invoice已出具并锁定编号");
    } catch (err) { setError(err instanceof Error ? err.message : "出具失败"); }
  }

  async function addPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    const data = new FormData(event.currentTarget);
    try {
      await postJson(`/api/invoices/${selected.id}/payments`, { payment_date: data.get("payment_date"), amount: data.get("amount"), method: data.get("method"), remark: data.get("remark") || null });
      event.currentTarget.reset();
      await invoices.reload();
      notify("Payment已登记");
    } catch (err) { setError(err instanceof Error ? err.message : "付款登记失败"); }
  }

  async function voidInvoice() {
    if (!selected) return;
    const reason = window.prompt("请输入作废原因。编号不会复用：");
    if (!reason) return;
    try {
      await postJson(`/api/invoices/${selected.id}/void`, { reason });
      await invoices.reload();
      notify("Invoice已作废，原编号永久保留");
    } catch (err) { setError(err instanceof Error ? err.message : "作废失败"); }
  }

  return (
    <>
      <PageHeader title="Invoice与收款" subtitle="编号在Issued时分配；已出具账单只能作废重开，不能覆盖修改" />
      {error || invoices.error ? <ErrorBanner message={error || invoices.error} /> : null}
      <div className="split-layout invoices-top">
        <Panel title="建立Invoice Draft" subtitle="Service Fee为0的结算不会出现在这里">
          <form className="form-grid" onSubmit={(e) => void createDraft(e)}>
            <Field label="Finalized Settlement"><select name="settlement_id" required defaultValue=""><option value="" disabled>请选择</option>{invoiceCandidates.map((item) => <option key={item.id} value={item.id}>{item.client_name} · {item.year} Q{item.quarter} · HKD {item.service_fee}</option>)}</select></Field>
            <Field label="默认PDF语言"><select name="language" defaultValue="zh"><option value="zh">中文</option><option value="en">English</option></select></Field>
            <button className="primary" type="submit"><FilePlus2 size={17} />建立Draft</button>
          </form>
        </Panel>
        <Panel title="选中Invoice">
          {selected ? <div className="invoice-summary"><header><ReceiptText /><div><strong>{selected.invoice_number || `Draft #${selected.id}`}</strong><span>{selected.client_name} · {selected.company_name}</span></div><StatusBadge value={selected.lifecycle_status} /></header><div className="invoice-amount"><small>Service Fee</small><Money value={selected.amount} emphasis /></div><div className="invoice-balance"><span>已收 <Money value={selected.paid_amount} /></span><span>未收 <Money value={selected.outstanding_amount} /></span><StatusBadge value={selected.payment_status} /></div></div> : <EmptyState title="尚未选择Invoice" detail="从下方列表选择一条记录。" />}
        </Panel>
      </div>

      {selected?.lifecycle_status === "DRAFT" ? <Panel title="正式出具"><form className="inline-form" onSubmit={(e) => void issue(e)}><Field label="Issue Date"><input name="issue_date" type="date" defaultValue={todayIso()} required /></Field><Field label="Due Date" hint="留空则采用Company默认天数"><input name="due_date" type="date" /></Field><Field label="Language"><select name="language" defaultValue={selected.language}><option value="zh">中文</option><option value="en">English</option></select></Field><button className="primary" type="submit">Issued并分配编号</button></form></Panel> : null}
      {selected?.lifecycle_status === "ISSUED" ? <Panel title="PDF与收款"><div className="invoice-actions"><button className="secondary" onClick={() => void download(`/api/invoices/${selected.id}/pdf?language=zh`, `${selected.invoice_number}_zh.pdf`, { method: "POST" })}><FileDown size={17} />中文PDF</button><button className="secondary" onClick={() => void download(`/api/invoices/${selected.id}/pdf?language=en`, `${selected.invoice_number}_en.pdf`, { method: "POST" })}><FileDown size={17} />English PDF</button><button className="danger" onClick={() => void voidInvoice()} disabled={Number(selected.paid_amount) > 0}><Ban size={17} />作废Invoice</button></div><form className="inline-form payment-form" onSubmit={(e) => void addPayment(e)}><Field label="Payment Date"><input name="payment_date" type="date" defaultValue={todayIso()} required /></Field><Field label="Amount (HKD)"><input name="amount" type="number" min="0.01" step="0.01" required /></Field><Field label="Method"><select name="method" defaultValue="BANK_TRANSFER"><option value="BANK_TRANSFER">Bank Transfer</option><option value="CHEQUE">Cheque</option><option value="OTHER">Other</option></select></Field><Field label="Remark"><input name="remark" /></Field><button className="primary" type="submit" disabled={selected.payment_status === "PAID"}>登记Payment</button></form></Panel> : null}
      {selected?.lifecycle_status === "VOID" ? <Panel title="作废Invoice档案" subtitle={`作废原因：${selected.void_reason || "未记录"}`}><div className="invoice-actions"><button className="secondary" onClick={() => void download(`/api/invoices/${selected.id}/pdf?language=zh`, `${selected.invoice_number}_zh.pdf`, { method: "POST" })}><FileDown size={17} />原中文PDF</button><button className="secondary" onClick={() => void download(`/api/invoices/${selected.id}/pdf?language=en`, `${selected.invoice_number}_en.pdf`, { method: "POST" })}><FileDown size={17} />Original English PDF</button></div></Panel> : null}

      <Panel title="Invoice清单">{invoices.data.length ? <div className="table-wrap"><table><thead><tr><th>Invoice No.</th><th>Client</th><th>Issue / Due</th><th>Amount</th><th>Payment</th><th>Lifecycle</th></tr></thead><tbody>{invoices.data.map((item) => <tr key={item.id} className={selectedId === item.id ? "selected clickable" : "clickable"} onClick={() => setSelectedId(item.id)}><td><strong>{item.invoice_number || `Draft #${item.id}`}</strong></td><td>{item.client_name}<small className="cell-note">{item.fc_name}</small></td><td>{item.issue_date || "-"}<small className="cell-note">Due {item.due_date || "-"}</small></td><td><Money value={item.amount} /></td><td><StatusBadge value={item.payment_status} /></td><td><StatusBadge value={item.lifecycle_status} /></td></tr>)}</tbody></table></div> : <EmptyState title="暂无Invoice" detail="先Finalized一个产生Service Fee的Settlement。" />}</Panel>
    </>
  );
}
