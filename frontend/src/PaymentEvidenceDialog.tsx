import { useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { fetchBlob } from "./api";
import { ErrorBanner, Loading, Money } from "./components";
import type { Invoice, InvoiceCorrection } from "./types";

function ProofPreview({ id }: { id: number }) {
  const [preview, setPreview] = useState<{ url: string; type: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    let url: string | null = null;
    setPreview(null);
    setError("");
    fetchBlob(`/api/attachments/${id}/file`, { signal: abort.signal }).then((blob) => {
      if (abort.signal.aborted) return;
      url = URL.createObjectURL(blob);
      setPreview({ url, type: blob.type });
    }).catch((err: Error) => { if (!abort.signal.aborted) setError(err.message); });
    return () => { abort.abort(); if (url) URL.revokeObjectURL(url); };
  }, [id]);
  if (error) return <ErrorBanner message={error} />;
  if (!preview) return <Loading />;
  return <div className="payment-proof-preview">
    {/^image\/(png|jpeg)$/.test(preview.type) ? <img src={preview.url} alt={`付款凭证 #${id}`} />
      : preview.type === "application/pdf" ? <object data={preview.url} type="application/pdf" aria-label={`付款凭证 PDF #${id}`}><p>浏览器未能显示PDF，请打开原件查看。</p></object>
      : <p>此凭证格式不支持页内预览，请打开原件查看。</p>}
    <a className="text-link" href={`/api/attachments/${id}/file`} target="_blank" rel="noreferrer">打开凭证原件 #{id}</a>
  </div>;
}

export default function PaymentEvidenceDialog({ invoice, corrections, onClose }: {
  invoice: Invoice; corrections: InvoiceCorrection[]; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [proofId, setProofId] = useState<number | null>(invoice.payments[0]?.proof_attachment_id ?? null);
  const related = corrections.filter((item) => item.original_invoice.id === invoice.id || item.replacement_invoice?.id === invoice.id);
  useEffect(() => {
    const element = dialog.current!;
    const previousFocus = document.activeElement as HTMLElement | null;
    element.showModal();
    return () => { element.close(); previousFocus?.focus(); };
  }, []);
  return <dialog className="payment-evidence-dialog" ref={dialog} aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <header><div><h2 id={titleId}>已付款 · 付款凭证</h2><p>{invoice.invoice_number} · {invoice.client_name} · {invoice.year} Q{invoice.quarter}</p></div>
      <button className="ghost icon-button" type="button" aria-label="关闭付款凭证" onClick={onClose}><X size={20} /></button></header>
    <div className="invoice-balance"><span>账单应付 <Money value={invoice.amount} /></span><span>计入本单现金 <Money value={invoice.paid_amount} /></span><span>公司承担差额 <Money value={invoice.adjustment_amount} /></span></div>
    <div className="table-wrap" tabIndex={0} role="region" aria-label="付款及原始凭证"><table><thead><tr><th>收款日期 / 方式</th><th>原收款来源</th><th>计入本单现金</th><th>凭证</th></tr></thead><tbody>
      {invoice.payments.map((payment) => <tr key={payment.id}><td>{payment.payment_date}<small className="cell-note">{payment.method}</small></td>
        <td>Payment #{payment.id}<small className="cell-note">原账单 #{payment.original_invoice_id ?? invoice.id} · 原现金 <Money value={payment.original_amount ?? payment.amount} /></small></td>
        <td><Money value={payment.amount} />{payment.remark ? <small className="cell-note">{payment.remark}</small> : null}</td>
        <td><button className="secondary" type="button" aria-pressed={proofId === payment.proof_attachment_id} onClick={() => setProofId(payment.proof_attachment_id)}>查看付款凭证 #{payment.id}</button></td></tr>)}
    </tbody></table></div>
    {!invoice.payments.length ? <p role="status">该账单没有可显示的付款凭证，请核对更正和差额记录。</p> : null}
    {invoice.adjustments?.map((item) => <p key={item.id}>公司承担差额 <Money value={item.amount} /> · {item.reason}</p>)}
    {related.map((item) => <details key={item.id}><summary>更正 #{item.id} · {item.status === "COMPLETED" ? "已完成" : "处理中"} · 原单 {item.original_invoice.invoice_number || `#${item.original_invoice.id}`} → {item.replacement_invoice?.invoice_number || "等待替代单"}</summary>
      <p>{item.reason}</p>
      {item.allocations.map((entry) => <p key={entry.id}>Payment #{entry.payment_id} · {entry.entry_type === "APPLY" ? "转入" : "冲回"}账单 #{entry.invoice_id} · <Money value={entry.amount} /></p>)}
      {item.refunds.map((refund) => <p key={refund.id}>Payment #{refund.payment_id} · {refund.refund_date}退款 <Money value={refund.amount} /> · {refund.reason} <button className="ghost" type="button" onClick={() => setProofId(refund.proof_attachment_id)}>查看退款凭证 #{refund.id}</button></p>)}
    </details>)}
    {proofId != null ? <ProofPreview key={proofId} id={proofId} /> : null}
  </dialog>;
}
