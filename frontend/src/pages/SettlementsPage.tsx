import { useEffect, useMemo, useState } from "react";
import { Calculator, CheckCircle2, Download, FileText } from "lucide-react";
import { download, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Money, PageHeader, Panel, StatusBadge } from "../components";
import { quarterDates, useApiList } from "../hooks";
import type { Account, Client, FeePlan, Platform, Settlement } from "../types";

type Snapshot = {
  id: number;
  account_id: number;
  as_of_date: string;
  total_balance: string;
  eligible_for_closing: boolean;
  evidence_complete: boolean;
};
type LineState = Record<number, {
  enabled: boolean;
  beginningSnapshotId: string;
  closingSnapshotId: string;
  originalHwm: string;
}>;

export default function SettlementsPage({ notify }: { notify: (message: string) => void }) {
  const clients = useApiList<Client>("/api/clients");
  const accounts = useApiList<Account>("/api/accounts");
  const platforms = useApiList<Platform>("/api/platforms");
  const plans = useApiList<FeePlan>("/api/fee-plans");
  const snapshots = useApiList<Snapshot>("/api/balance-snapshots");
  const settlements = useApiList<Settlement>("/api/settlements");
  const currentYear = new Date().getFullYear();
  const [clientId, setClientId] = useState("");
  const [platformId, setPlatformId] = useState("");
  const [planId, setPlanId] = useState("");
  const [year, setYear] = useState(currentYear);
  const [quarter, setQuarter] = useState(1);
  const [startDate, setStartDate] = useState(`${currentYear}-01-01`);
  const [closingDate, setClosingDate] = useState(`${currentYear}-03-31`);
  const [lines, setLines] = useState<LineState>({});
  const [result, setResult] = useState<Settlement | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const [start, close] = quarterDates(year, quarter);
    setStartDate(start);
    setClosingDate(close);
  }, [year, quarter]);

  const groupAccounts = useMemo(() => accounts.data.filter((account) =>
    (!clientId || account.client_id === Number(clientId)) &&
    (!platformId || account.platform_id === Number(platformId)) &&
    (!planId || account.fee_plan_id === Number(planId))), [accounts.data, clientId, platformId, planId]);

  useEffect(() => {
    setLines((previous) => {
      const next: LineState = {};
      for (const account of groupAccounts) {
        next[account.id] = previous[account.id] || {
          enabled: true,
          beginningSnapshotId: "",
          closingSnapshotId: "",
          originalHwm: "",
        };
      }
      return next;
    });
  }, [groupAccounts]);

  function updateLine(accountId: number, patch: Partial<LineState[number]>) {
    setLines((current) => ({ ...current, [accountId]: { ...current[accountId], ...patch } }));
  }

  async function calculate() {
    setBusy(true);
    setError("");
    try {
      const accountLines = groupAccounts
        .filter((account) => lines[account.id]?.enabled)
        .map((account) => ({
          account_id: account.id,
          beginning_snapshot_id: lines[account.id].beginningSnapshotId
            ? Number(lines[account.id].beginningSnapshotId)
            : null,
          closing_snapshot_id: Number(lines[account.id].closingSnapshotId),
          original_hwm: lines[account.id].originalHwm || null,
        }));
      if (!accountLines.length || accountLines.some((line) => !line.closing_snapshot_id)) {
        throw new Error("每个加入结算的Sub Account都必须选择Closing Snapshot");
      }
      const response = await postJson<Settlement>("/api/settlements/calculate", {
        client_id: Number(clientId),
        platform_id: Number(platformId),
        fee_plan_id: Number(planId),
        year,
        quarter,
        start_date: startDate,
        closing_date: closingDate,
        account_lines: accountLines,
      });
      setResult(response);
      await settlements.reload();
      notify("账户级HWM已计算并保存为Draft");
    } catch (err) {
      setError(err instanceof Error ? err.message : "计算失败");
    } finally {
      setBusy(false);
    }
  }

  async function finalize(id: number) {
    try {
      const response = await postJson<Settlement>(`/api/settlements/${id}/finalize`, {});
      setResult(response);
      await settlements.reload();
      notify("Settlement已Finalized并锁定");
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败");
    }
  }

  return (
    <>
      <PageHeader title="季度结算" subtitle="每个Sub Account独立计算HWM和Service Fee；组合结果仅作相加汇总" />
      {error || settlements.error ? <ErrorBanner message={error || settlements.error} /> : null}
      <Panel title="建立结算组合" subtitle="首次账户需选择Beginning Snapshot并输入自己的Original HWM；以后系统自动继承上期Closing和Next HWM">
        <div className="settlement-controls">
          <Field label="Client"><select value={clientId} onChange={(e) => setClientId(e.target.value)}><option value="">请选择</option>{clients.data.filter((x) => x.status === "ACTIVE").map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label="Platform"><select value={platformId} onChange={(e) => setPlatformId(e.target.value)}><option value="">请选择</option>{platforms.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label="Fee Plan"><select value={planId} onChange={(e) => setPlanId(e.target.value)}><option value="">请选择</option>{plans.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label="Year"><input type="number" min="2000" max="2200" value={year} onChange={(e) => setYear(Number(e.target.value))} /></Field>
          <Field label="Quarter"><select value={quarter} onChange={(e) => setQuarter(Number(e.target.value))}><option value={1}>Q1</option><option value={2}>Q2</option><option value={3}>Q3</option><option value={4}>Q4</option></select></Field>
          <Field label="Starting Date"><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></Field>
          <Field label="Closing Date"><input type="date" value={closingDate} onChange={(e) => setClosingDate(e.target.value)} /></Field>
        </div>
        <div className="account-entry-table">
          <div className="account-entry header"><span>加入</span><span>Sub Account</span><span>Beginning Snapshot</span><span>Closing Snapshot</span><span>Original HWM</span></div>
          {groupAccounts.length ? groupAccounts.map((account) => {
            const beginningOptions = snapshots.data.filter((snapshot) => snapshot.account_id === account.id && snapshot.as_of_date === startDate);
            const closingOptions = snapshots.data.filter((snapshot) => snapshot.account_id === account.id && snapshot.as_of_date === closingDate && snapshot.eligible_for_closing);
            return <div className="account-entry" key={account.id}>
              <span><input type="checkbox" checked={lines[account.id]?.enabled ?? true} onChange={(e) => updateLine(account.id, { enabled: e.target.checked })} /></span>
              <span><strong>{account.account_number}</strong><small>{account.client_name}</small></span>
              <span><select disabled={!lines[account.id]?.enabled} value={lines[account.id]?.beginningSnapshotId || ""} onChange={(e) => updateLine(account.id, { beginningSnapshotId: e.target.value })}><option value="">自动继承；首次请选择</option>{beginningOptions.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>HKD {snapshot.total_balance} · {snapshot.evidence_complete ? "有凭证" : "待补凭证"}</option>)}</select></span>
              <span><select disabled={!lines[account.id]?.enabled} value={lines[account.id]?.closingSnapshotId || ""} onChange={(e) => updateLine(account.id, { closingSnapshotId: e.target.value })}><option value="">请选择Closing</option>{closingOptions.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>HKD {snapshot.total_balance} · {snapshot.evidence_complete ? "有凭证" : "待补凭证"}</option>)}</select></span>
              <span><input type="number" min="0" step="0.01" placeholder="首次账户必填" disabled={!lines[account.id]?.enabled} value={lines[account.id]?.originalHwm || ""} onChange={(e) => updateLine(account.id, { originalHwm: e.target.value })} /></span>
            </div>;
          }) : <EmptyState title="没有匹配账户" detail="选择完整的Client、Platform和Fee Plan后，系统只显示同组账户。" />}
        </div>
        <div className="form-actions"><button className="primary" disabled={busy || !clientId || !platformId || !planId || !groupAccounts.length} onClick={() => void calculate()}><Calculator size={17} />{busy ? "计算中..." : "计算并保存Draft"}</button></div>
      </Panel>

      {result ? <Panel title="计算结果" subtitle={`${result.calculation_mode === "ACCOUNT_HWM" ? "账户级HWM" : "历史组合HWM"} · Formula ${result.formula_version} · ${result.status === "DRAFT" ? "尚未锁定" : "已锁定"}`}>
        <div className="calculation-grid"><span><small>Beginning</small><Money value={result.beginning} /></span><span><small>Net Contribution</small><Money value={result.net_contribution} /></span><span><small>Closing</small><Money value={result.closing} /></span><span><small>Gain / Loss</small><Money value={result.gain_loss} /></span><span><small>Period Rate</small><strong>{result.period_rate == null ? "N/A" : `${(result.period_rate * 100).toFixed(2)}%`}</strong></span><span><small>Days（仅展示）</small><strong>{result.days}</strong></span><span><small>Adjusted HWM合计</small><Money value={result.adjusted_hwm} /></span><span><small>各账户Above HWM合计</small><Money value={result.chargeable_above_hwm} /></span><span className="highlight"><small>各账户Service Fee合计</small><Money value={result.service_fee} emphasis /></span><span><small>Next HWM合计</small><Money value={result.next_hwm} /></span></div>
        {result.account_lines.length ? <div className="table-wrap settlement-line-results"><table><thead><tr><th>Sub Account</th><th>Beginning</th><th>Net Contribution</th><th>Closing</th><th>Original HWM</th><th>Above HWM</th><th>Service Fee</th><th>凭证</th></tr></thead><tbody>{result.account_lines.map((line) => <tr key={line.id}><td>{line.account_number}</td><td><Money value={line.beginning} /></td><td><Money value={line.net_contribution || "0.00"} /></td><td><Money value={line.closing} /></td><td><Money value={line.original_hwm || "0.00"} /></td><td><Money value={line.chargeable_above_hwm || "0.00"} /></td><td><Money value={line.service_fee || "0.00"} /></td><td>{(line.beginning_evidence_count || 0) > 0 && (line.closing_evidence_count || 0) > 0 ? "完整" : "待补"}</td></tr>)}</tbody></table></div> : null}
        <div className="form-actions">{result.status === "DRAFT" ? <button className="primary" onClick={() => void finalize(result.id)}><CheckCircle2 size={17} />Finalized并锁定</button> : <><button className="secondary" onClick={() => void download(`/api/exports/pdf?settlement_id=${result.id}&language=zh`, `settlement_${result.id}_zh.pdf`, { method: "POST" })}><FileText size={17} />中文结算PDF</button><button className="secondary" onClick={() => void download(`/api/exports/pdf?settlement_id=${result.id}&language=en`, `settlement_${result.id}_en.pdf`, { method: "POST" })}><FileText size={17} />English PDF</button><button className="ghost" onClick={() => void download(`/api/exports/excel?settlement_ids=${result.id}`, `settlement_${result.id}.xlsx`, { method: "POST" })}><Download size={17} />内部Excel</button></>}</div>
      </Panel> : null}

      <Panel title="历史Settlement">{settlements.data.length ? <div className="table-wrap"><table><thead><tr><th>Period</th><th>Client</th><th>Platform / Plan</th><th>口径</th><th>Closing</th><th>Service Fee</th><th>Status</th></tr></thead><tbody>{settlements.data.map((item) => <tr key={item.id} onClick={() => setResult(item)} className="clickable"><td>{item.year} Q{item.quarter}</td><td>{item.client_name}</td><td>{item.platform_name}<small className="cell-note">{item.fee_plan_name}</small></td><td>{item.calculation_mode === "ACCOUNT_HWM" ? "账户级" : "历史组合"}</td><td><Money value={item.closing} /></td><td><Money value={item.service_fee} /></td><td><StatusBadge value={item.status} /></td></tr>)}</tbody></table></div> : <EmptyState title="暂无结算" detail="上方建立第一份季度Settlement。" />}</Panel>
    </>
  );
}
