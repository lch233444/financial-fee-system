import { useEffect, useMemo, useState } from "react";
import { Calculator, CheckCircle2, Download, FileText } from "lucide-react";
import { download, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Money, PageHeader, Panel, StatusBadge } from "../components";
import { quarterDates, useApiList } from "../hooks";
import type { Account, Client, FeePlan, Platform, Settlement } from "../types";

type Snapshot = { id: number; account_id: number; as_of_date: string; total_balance: string; eligible_for_closing: boolean };
type LineState = Record<number, { enabled: boolean; beginning: string; closing: string; snapshotId: string }>;

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
  const [originalHwm, setOriginalHwm] = useState("");
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
        next[account.id] = previous[account.id] || { enabled: true, beginning: "", closing: "", snapshotId: "" };
      }
      return next;
    });
  }, [groupAccounts]);

  function updateLine(accountId: number, patch: Partial<LineState[number]>) {
    setLines((current) => ({ ...current, [accountId]: { ...current[accountId], ...patch } }));
  }

  function chooseSnapshot(accountId: number, snapshotId: string) {
    const snapshot = snapshots.data.find((item) => item.id === Number(snapshotId));
    updateLine(accountId, { snapshotId, closing: snapshot?.total_balance || lines[accountId]?.closing || "" });
    if (snapshot) setClosingDate(snapshot.as_of_date);
  }

  async function calculate() {
    setBusy(true);
    setError("");
    try {
      const accountLines = groupAccounts.filter((account) => lines[account.id]?.enabled).map((account) => ({
        account_id: account.id, beginning: lines[account.id].beginning, closing: lines[account.id].closing,
        closing_snapshot_id: lines[account.id].snapshotId ? Number(lines[account.id].snapshotId) : null,
      }));
      const response = await postJson<Settlement>("/api/settlements/calculate", {
        client_id: Number(clientId), platform_id: Number(platformId), fee_plan_id: Number(planId),
        year, quarter, start_date: startDate, closing_date: closingDate,
        original_hwm: originalHwm || null, account_lines: accountLines,
      });
      setResult(response);
      await settlements.reload();
      notify("结算已计算并保存为Draft");
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
      <PageHeader title="季度结算" subtitle="账户级保存Beginning/Closing，按Client + Platform + Fee Plan合并计算" />
      {error || settlements.error ? <ErrorBanner message={error || settlements.error} /> : null}
      <Panel title="建立结算组合" subtitle="同一天Starting Date发生的资金不会重复计入Contribution">
        <div className="settlement-controls">
          <Field label="Client"><select value={clientId} onChange={(e) => setClientId(e.target.value)}><option value="">请选择</option>{clients.data.filter((x) => x.status === "ACTIVE").map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label="Platform"><select value={platformId} onChange={(e) => setPlatformId(e.target.value)}><option value="">请选择</option>{platforms.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label="Fee Plan"><select value={planId} onChange={(e) => setPlanId(e.target.value)}><option value="">请选择</option>{plans.data.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label="Year"><input type="number" min="2000" max="2200" value={year} onChange={(e) => setYear(Number(e.target.value))} /></Field>
          <Field label="Quarter"><select value={quarter} onChange={(e) => setQuarter(Number(e.target.value))}><option value={1}>Q1</option><option value={2}>Q2</option><option value={3}>Q3</option><option value={4}>Q4</option></select></Field>
          <Field label="Starting Date"><input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></Field>
          <Field label="Closing Date"><input type="date" value={closingDate} onChange={(e) => setClosingDate(e.target.value)} /></Field>
          <Field label="Original HWM" hint="首次结算必填；以后由系统继承"><input type="number" step="0.01" min="0" value={originalHwm} onChange={(e) => setOriginalHwm(e.target.value)} /></Field>
        </div>
        <div className="account-entry-table">
          <div className="account-entry header"><span>加入</span><span>Sub Account</span><span>Beginning (HKD)</span><span>Closing (HKD)</span><span>Closing Snapshot</span></div>
          {groupAccounts.length ? groupAccounts.map((account) => <div className="account-entry" key={account.id}><span><input type="checkbox" checked={lines[account.id]?.enabled ?? true} onChange={(e) => updateLine(account.id, { enabled: e.target.checked })} /></span><span><strong>{account.account_number}</strong><small>{account.client_name}</small></span><span><input type="number" min="0" step="0.01" disabled={!lines[account.id]?.enabled} value={lines[account.id]?.beginning || ""} onChange={(e) => updateLine(account.id, { beginning: e.target.value })} /></span><span><input type="number" min="0" step="0.01" disabled={!lines[account.id]?.enabled} value={lines[account.id]?.closing || ""} onChange={(e) => updateLine(account.id, { closing: e.target.value })} /></span><span><select disabled={!lines[account.id]?.enabled} value={lines[account.id]?.snapshotId || ""} onChange={(e) => chooseSnapshot(account.id, e.target.value)}><option value="">手工输入</option>{snapshots.data.filter((x) => x.account_id === account.id && x.eligible_for_closing).map((x) => <option key={x.id} value={x.id}>{x.as_of_date} · HKD {x.total_balance}</option>)}</select></span></div>) : <EmptyState title="没有匹配账户" detail="选择完整的Client、Platform和Fee Plan后，系统只显示同组账户。" />}
        </div>
        <div className="form-actions"><button className="primary" disabled={busy || !clientId || !platformId || !planId || !groupAccounts.length} onClick={() => void calculate()}><Calculator size={17} />{busy ? "计算中..." : "计算并保存Draft"}</button></div>
      </Panel>

      {result ? <Panel title="计算结果" subtitle={`Formula ${result.status === "DRAFT" ? "尚未锁定" : "已锁定"}`}><div className="calculation-grid"><span><small>Beginning</small><Money value={result.beginning} /></span><span><small>Net Contribution</small><Money value={result.net_contribution} /></span><span><small>Closing</small><Money value={result.closing} /></span><span><small>Gain / Loss</small><Money value={result.gain_loss} /></span><span><small>Period Rate</small><strong>{result.period_rate == null ? "N/A" : `${(result.period_rate * 100).toFixed(2)}%`}</strong></span><span><small>Days（仅展示）</small><strong>{result.days}</strong></span><span><small>Adjusted HWM</small><Money value={result.adjusted_hwm} /></span><span><small>Above Watermark</small><Money value={result.chargeable_above_hwm} /></span><span className="highlight"><small>Service Fee</small><Money value={result.service_fee} emphasis /></span><span><small>Next HWM</small><Money value={result.next_hwm} /></span></div><div className="form-actions">{result.status === "DRAFT" ? <button className="primary" onClick={() => void finalize(result.id)}><CheckCircle2 size={17} />Finalized并锁定</button> : <><button className="secondary" onClick={() => void download(`/api/exports/pdf?settlement_id=${result.id}&language=zh`, `settlement_${result.id}_zh.pdf`, { method: "POST" })}><FileText size={17} />中文结算PDF</button><button className="secondary" onClick={() => void download(`/api/exports/pdf?settlement_id=${result.id}&language=en`, `settlement_${result.id}_en.pdf`, { method: "POST" })}><FileText size={17} />English PDF</button><button className="ghost" onClick={() => void download(`/api/exports/excel?settlement_ids=${result.id}`, `settlement_${result.id}.xlsx`, { method: "POST" })}><Download size={17} />内部Excel</button></>}</div></Panel> : null}

      <Panel title="历史Settlement">{settlements.data.length ? <div className="table-wrap"><table><thead><tr><th>Period</th><th>Client</th><th>Platform / Plan</th><th>Closing</th><th>Service Fee</th><th>Status</th></tr></thead><tbody>{settlements.data.map((item) => <tr key={item.id} onClick={() => setResult(item)} className="clickable"><td>{item.year} Q{item.quarter}</td><td>{item.client_name}</td><td>{item.platform_name}<small className="cell-note">{item.fee_plan_name}</small></td><td><Money value={item.closing} /></td><td><Money value={item.service_fee} /></td><td><StatusBadge value={item.status} /></td></tr>)}</tbody></table></div> : <EmptyState title="暂无结算" detail="上方建立第一份季度Settlement。" />}</Panel>
    </>
  );
}
