import { useState } from "react";
import { Field } from "./components";
import type { Company, Invoice, InvoiceCorrection } from "./types";

export type CorrectionChoice = { target_company_id: number | null; recalculate_settlements: boolean; reason: string };

export default function InvoiceCorrectionForm({ original, correction, companies, busy, onSubmit, onCancel }: {
  original: Invoice; correction: InvoiceCorrection | null; companies: Company[]; busy: boolean;
  onSubmit: (choice: CorrectionChoice) => void; onCancel: () => void;
}) {
  const canChangeCompany = correction ? correction.can_amend : original.can_correct_company;
  const [companyId, setCompanyId] = useState(correction?.target_company_id ?? original.payee_company_id);
  const [recalculate, setRecalculate] = useState(correction?.recalculate_settlements ?? !canChangeCompany);
  const [reason, setReason] = useState("");
  const companyChanged = companyId !== original.payee_company_id;
  const unchanged = correction && companyId === (correction.target_company_id ?? original.payee_company_id)
    && recalculate === correction.recalculate_settlements;
  return <form className="correction-form" onSubmit={(event) => {
    event.preventDefault();
    if (busy || unchanged || (!companyChanged && !recalculate) || reason.trim().length < 2) return;
    onSubmit({ target_company_id: companyChanged ? companyId : null, recalculate_settlements: recalculate, reason: reason.trim() });
  }}>
    <Field label="收款公司" hint={canChangeCompany ? "保留当前公司，或选择新的收款公司" : "已有资金记录，收款公司不可变更"}>
      <select value={companyId} onChange={(event) => setCompanyId(Number(event.target.value))} disabled={busy || !canChangeCompany}>
        <option value={original.payee_company_id}>{original.company_name}（原收款公司）</option>
        {companies.filter((company) => company.id !== original.payee_company_id).map((company) => <option key={company.id} value={company.id}>{company.name}</option>)}
      </select>
    </Field>
    <label className="checkbox"><input type="checkbox" checked={recalculate} disabled={busy || !canChangeCompany}
      onChange={(event) => setRecalculate(event.target.checked)} />需要重新核算费用／结算来源</label>
    <p className="correction-continuity">{recalculate
      ? "请按结算版本链作废、重建并确认结算，再生成替代账单；金额以重新核算结果为准。"
      : "保留原结算、金额及服务归属，仅使用新公司的收款资料和付款期限重新出具账单。"}</p>
    <Field label={correction ? "调整原因" : "更正原因"}>
      <input value={reason} onChange={(event) => setReason(event.target.value)} minLength={2} maxLength={500} required disabled={busy} />
    </Field>
    <p className="cell-note">{correction ? "保存后保留最初更正原因，并记录本次调整前后的内容。" : "确认后原账单作废，原编号、PDF和资金历史保留；替代账单使用新编号。已有现金按更正流程冲回分配，完成时核对留存及退款。"}</p>
    <div className="invoice-actions">
      <button className="primary" type="submit" disabled={busy || Boolean(unchanged) || (!companyChanged && !recalculate) || reason.trim().length < 2}>
        {busy ? "正在核对并保存..." : correction ? "确认调整更正" : "确认发起更正"}
      </button>
      <button className="secondary" type="button" disabled={busy} onClick={onCancel}>取消</button>
    </div>
  </form>;
}
