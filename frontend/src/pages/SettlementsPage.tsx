import SearchableSelect, { matchesSearch } from "../SearchableSelect";
import { useEffect, useMemo, useRef, useState } from "react";
import { Ban, Calculator, CheckCircle2, Download, FileText, Trash2 } from "lucide-react";
import { api, download, postJson } from "../api";
import { EmptyState, ErrorBanner, Field, Money, PageHeader, Panel, StatusBadge, SectionNav, Loading, Pagination } from "../components";
import { settlementGroups } from "../settlementWorkspace";
import { quarterDates, useApiList, usePagination } from "../hooks";
import type { Account, BalanceSnapshot, Client, FeePlan, Platform, Settlement } from "../types";
type LineState = Record<number, {
  enabled: boolean;
  startDate: string;
  closingDate: string;
  beginningSnapshotId: string;
  closingSnapshotId: string;
  originalHwm: string;
}>;

function previousCalendarDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() - 1);
  return date.toISOString().slice(0, 10);
}

function SettlementAccountList({ item, accountById }: { item: Settlement; accountById: Map<number, Account> }) {
  if (!item.account_lines.length) return <span>-</span>;
  return <div className="settlement-account-list">{item.account_lines.map((line) => {
    const scheme = accountById.get(line.account_id)?.scheme_name?.trim();
    return <span key={line.id}><strong>{line.account_number}</strong><small>Scheme · {scheme || "未填写"}</small></span>;
  })}</div>;
}

export default function SettlementsPage({ notify }: { notify: (message: string) => void }) {
  const clients = useApiList<Client>("/api/clients");
  const accounts = useApiList<Account>("/api/accounts");
  const platforms = useApiList<Platform>("/api/platforms");
  const plans = useApiList<FeePlan>("/api/fee-plans");
  const snapshots = useApiList<BalanceSnapshot>("/api/balance-snapshots");
  const settlements = useApiList<Settlement>("/api/settlements");
  const currentYear = new Date().getFullYear();
  const [clientId, setClientId] = useState("");
  const [platformId, setPlatformId] = useState("");
  const [planId, setPlanId] = useState("");
  const [year, setYear] = useState(currentYear);
  const [quarter, setQuarter] = useState(1);
  const [lines, setLines] = useState<LineState>({});
  const [historySearch, setHistorySearch] = useState("");
  const [historyStatus, setHistoryStatus] = useState("");
  const [result, setResult] = useState<Settlement | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [finalizeBusy, setFinalizeBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [voidBusy, setVoidBusy] = useState(false);
  const calculateBusyRef = useRef(false);
  const finalizeBusyRef = useRef(false);
  const deleteBusyRef = useRef(false);
  const voidBusyRef = useRef(false);
  const resultRevisionRef = useRef(0);
  const [exportYear, setExportYear] = useState(currentYear);
  const [exportQuarter, setExportQuarter] = useState("");
  const [exportClientId, setExportClientId] = useState("");
  const [selectedExportIds, setSelectedExportIds] = useState<number[]>([]);
  const [exportBusy, setExportBusy] = useState(false);
  const settlementMutationBusy = busy || finalizeBusy || deleteBusy || voidBusy;

  const accountById = useMemo(() => new Map(accounts.data.map((account) => [account.id, account])), [accounts.data]);
  const groups = useMemo(() => settlementGroups(accounts.data, settlements.data, year, quarter), [accounts.data, settlements.data, year, quarter]);
  const clientGroups = useMemo(() => groups.filter((group) => !clientId || group.accounts[0].client_id === Number(clientId)), [groups, clientId]);
  const groupPages = usePagination(clientGroups, `${clientId}:${year}:${quarter}`, 10);
  const availablePlatforms = platforms.data.filter((platform) => clientId && clientGroups.some((group) => group.accounts[0].platform_id === platform.id));
  const availablePlans = plans.data.filter((plan) => clientId && platformId && clientGroups.some((group) => group.accounts[0].platform_id === Number(platformId) && group.accounts[0].fee_plan_id === plan.id));
  const groupAccounts = useMemo(() => groups.find((group) => group.key === `${clientId}:${platformId}:${planId}`)?.accounts ?? [], [groups, clientId, platformId, planId]);
  const selectedPlan = plans.data.find((plan) => plan.id === Number(planId));
  const history = useMemo(() => settlements.data.filter((item) => (!historyStatus || item.status === historyStatus)
    && matchesSearch([item.client_name, item.platform_name, item.fee_plan_name, `${item.year} Q${item.quarter}`, ...item.account_lines.map((line) => line.account_number)].join(" "), historySearch)), [settlements.data, historySearch, historyStatus]);
  const historyPages = usePagination(history, `${historySearch}:${historyStatus}`);

  const exportYears = useMemo(() => Array.from(new Set([
    currentYear,
    ...settlements.data.map((item) => item.year),
  ])).sort((a, b) => b - a), [currentYear, settlements.data]);

  const exportableSettlements = useMemo(() => settlements.data.filter((item) =>
    item.status === "FINALIZED"
    && item.year === exportYear
    && (!exportQuarter || item.quarter === Number(exportQuarter))
    && (!exportClientId || item.client_id === Number(exportClientId))),
  [exportClientId, exportQuarter, exportYear, settlements.data]);

  const selectedExportSettlements = useMemo(() => exportableSettlements.filter((item) =>
    selectedExportIds.includes(item.id)), [exportableSettlements, selectedExportIds]);

  const selectedServiceFee = useMemo(() => selectedExportSettlements.reduce(
    (total, item) => total + Math.round(Number(item.service_fee) * 100), 0,
  ) / 100, [selectedExportSettlements]);

  const allExportableSelected = exportableSettlements.length > 0
    && exportableSettlements.every((item) => selectedExportIds.includes(item.id));

  useEffect(() => {
    setSelectedExportIds([]);
  }, [exportClientId, exportQuarter, exportYear]);

  useEffect(() => {
    setLines((previous) => {
      const next: LineState = {};
      const [quarterStart, quarterClosing] = quarterDates(year, quarter);
      for (const account of groupAccounts) {
        const defaultStart = account.start_date && account.start_date > quarterStart ? account.start_date : quarterStart;
        const defaultClosing = account.end_date && account.end_date < quarterClosing ? account.end_date : quarterClosing;
        const existing = previous[account.id];
        const existingIsValid = existing
          && existing.startDate >= quarterStart
          && existing.closingDate <= quarterClosing
          && existing.startDate <= existing.closingDate
          && (!account.start_date || existing.startDate >= account.start_date)
          && (!account.end_date || existing.closingDate <= account.end_date);
        next[account.id] = existingIsValid ? existing : {
          enabled: defaultStart <= defaultClosing,
          startDate: defaultStart,
          closingDate: defaultClosing,
          beginningSnapshotId: "",
          closingSnapshotId: "",
          originalHwm: "",
        };
      }
      return next;
    });
  }, [groupAccounts, year, quarter]);

  function invalidateResult() {
    resultRevisionRef.current += 1;
    setResult(null);
  }

  function updateLine(accountId: number, patch: Partial<LineState[number]>) {
    invalidateResult();
    setLines((current) => ({ ...current, [accountId]: { ...current[accountId], ...patch } }));
  }

  async function calculate() {
    if (calculateBusyRef.current || finalizeBusyRef.current || deleteBusyRef.current || voidBusyRef.current) return;
    calculateBusyRef.current = true;
    setBusy(true);
    setError("");
    const requestRevision = resultRevisionRef.current + 1;
    resultRevisionRef.current = requestRevision;
    setResult(null);
    try {
      const accountLines = groupAccounts
        .filter((account) => lines[account.id]?.enabled)
        .map((account) => ({
          account_id: account.id,
          start_date: lines[account.id].startDate,
          closing_date: lines[account.id].closingDate,
          beginning_snapshot_id: lines[account.id].beginningSnapshotId
            ? Number(lines[account.id].beginningSnapshotId)
            : null,
          closing_snapshot_id: Number(lines[account.id].closingSnapshotId),
          original_hwm: lines[account.id].originalHwm || null,
        }));
      if (!accountLines.length || accountLines.some((line) => !line.start_date || !line.closing_date || !line.closing_snapshot_id)) {
        throw new Error("每个加入结算的Sub Account都必须选择Closing Snapshot");
      }
      const response = await postJson<Settlement>("/api/settlements/calculate", {
        client_id: Number(clientId),
        platform_id: Number(platformId),
        fee_plan_id: Number(planId),
        year,
        quarter,
        account_lines: accountLines,
      });
      if (resultRevisionRef.current === requestRevision) {
        setResult(response);
      }
      await settlements.reload();
      notify("账户级HWM已计算并保存为Draft");
    } catch (err) {
      setError(err instanceof Error ? err.message : "计算失败");
    } finally {
      calculateBusyRef.current = false;
      setBusy(false);
    }
  }

  async function finalize(id: number) {
    if (finalizeBusyRef.current || calculateBusyRef.current || deleteBusyRef.current || voidBusyRef.current) return;
    finalizeBusyRef.current = true;
    setFinalizeBusy(true);
    setError("");
    const requestRevision = resultRevisionRef.current;
    try {
      const response = await postJson<Settlement>(`/api/settlements/${id}/finalize`, {});
      if (resultRevisionRef.current === requestRevision) {
        setResult(response);
      }
      await settlements.reload();
      notify("Settlement已Finalized并锁定");
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认失败");
    } finally {
      finalizeBusyRef.current = false;
      setFinalizeBusy(false);
    }
  }

  async function deleteDraft(item: Settlement) {
    if (
      item.status !== "DRAFT"
      || deleteBusyRef.current
      || calculateBusyRef.current
      || finalizeBusyRef.current
      || voidBusyRef.current
      || !window.confirm(
        `确认删除 ${item.year} Q${item.quarter} 的 Draft Settlement？\n\n只会删除尚未锁定的Draft及其账户明细，并保留审计记录；Finalized或Void记录不能删除。`,
      )
    ) return;
    deleteBusyRef.current = true;
    setDeleteBusy(true);
    setError("");
    try {
      await api<{ status: string; id: number }>(`/api/settlements/${item.id}`, { method: "DELETE" });
      resultRevisionRef.current += 1;
      setResult(null);
      await settlements.reload();
      notify("Draft Settlement已删除，审计记录已保留");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Draft Settlement删除失败");
    } finally {
      deleteBusyRef.current = false;
      setDeleteBusy(false);
    }
  }

  async function voidFinalized(item: Settlement) {
    if (
      item.status !== "FINALIZED"
      || voidBusyRef.current
      || calculateBusyRef.current
      || finalizeBusyRef.current
      || deleteBusyRef.current
    ) return;
    const enteredReason = window.prompt(
      `请输入作废 ${item.year} Q${item.quarter} Settlement v${item.version_no} 的原因：`,
    );
    const reason = enteredReason?.trim() ?? "";
    if (!reason) return;
    if (reason.length < 2 || reason.length > 500) {
      setError("作废原因必须为2至500个字符");
      return;
    }
    if (!window.confirm(
      `最终确认作废 Settlement #${item.id} v${item.version_no}？\n\n` +
      "该操作不可撤销；有后续HWM或活动Invoice时系统会拒绝。作废后只能建立有完整版本链的替代Settlement。",
    )) return;
    voidBusyRef.current = true;
    setVoidBusy(true);
    setError("");
    try {
      const response = await postJson<Settlement>(`/api/settlements/${item.id}/void`, { reason });
      resultRevisionRef.current += 1;
      setResult(response);
      setSelectedExportIds((current) => current.filter((id) => id !== item.id));
      await settlements.reload();
      notify("Settlement已作废并保留完整历史；可按同一自然键建立下一版本");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Settlement作废失败";
      try {
        const refreshed = await api<Settlement>(`/api/settlements/${item.id}`);
        setResult(refreshed);
        if (refreshed.status === "VOID") {
          setSelectedExportIds((current) => current.filter((id) => id !== refreshed.id));
          await settlements.reload();
          notify("Settlement已作废并保留完整历史；可按同一自然键建立下一版本");
          return;
        }
      } catch {
        // Keep the original mutation error; the list reload below still refreshes other rows.
      }
      await settlements.reload();
      setError(message);
    } finally {
      voidBusyRef.current = false;
      setVoidBusy(false);
    }
  }

  function showHistoricalResult(item: Settlement) {
    if (calculateBusyRef.current || finalizeBusyRef.current || deleteBusyRef.current || voidBusyRef.current) return;
    resultRevisionRef.current += 1;
    setError("");
    setResult(item);
    window.setTimeout(() => { const panel = document.getElementById("settlement-result"); panel?.scrollIntoView?.({ block: "start" }); panel?.focus({ preventScroll: true }); }, 0);
  }

  function toggleExportSettlement(id: number) {
    setSelectedExportIds((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id]);
  }

  function toggleAllExportable() {
    setSelectedExportIds(allExportableSelected ? [] : exportableSettlements.map((item) => item.id));
  }

  async function exportInternalExcel() {
    const exportIds = selectedExportSettlements.map((item) => item.id);
    if (!exportIds.length) {
      setError("请先选择至少一份Finalized Settlement");
      return;
    }
    setExportBusy(true);
    setError("");
    try {
      const period = exportQuarter ? `${exportYear}_Q${exportQuarter}` : `${exportYear}_全年`;
      const ids = [...exportIds].sort((a, b) => a - b).join(",");
      await download(
        `/api/exports/excel?settlement_ids=${ids}`,
        `公司内部财务_${period}.xlsx`,
        { method: "POST" },
      );
      notify(`已按公司模板导出${exportIds.length}份Finalized Settlement`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "内部财务Excel导出失败");
    } finally {
      setExportBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="季度结算" subtitle="每个Sub Account独立计算HWM和Service Fee；余额快照入账后仍须财务人工计算并Finalize" />
      <SectionNav items={[{ id: "settlement-create", label: "建立结算" }, { id: "settlement-groups", label: "本季组合" }, { id: "settlement-history", label: "历史结算" }, { id: "settlement-export", label: "内部Excel" }]} />
      {error || settlements.error || clients.error || accounts.error || platforms.error || plans.error || snapshots.error ? <ErrorBanner message={error || settlements.error || clients.error || accounts.error || platforms.error || plans.error || snapshots.error} /> : null}
      <Panel id="settlement-create" title="建立结算组合" subtitle="首次账户需选择Beginning Snapshot并输入自己的Original HWM；自然季度首日可选择上一季末Snapshot；账单导入不会自动计算或Finalize">
        <div className="settlement-controls">
          <Field group label="Client"><SearchableSelect label="结算客户" disabled={settlementMutationBusy} value={clientId} onChange={(value) => { invalidateResult(); setClientId(value); setPlatformId(""); setPlanId(""); }} options={clients.data.filter((x) => x.status === "ACTIVE").map((x) => ({ value: String(x.id), label: x.name }))} /></Field>
          <Field label="Platform"><select disabled={settlementMutationBusy || !clientId} value={platformId} onChange={(e) => { invalidateResult(); setPlatformId(e.target.value); setPlanId(""); }}><option value="">请选择</option>{availablePlatforms.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
          <Field label="Fee Plan"><select disabled={settlementMutationBusy || !platformId} value={planId} onChange={(e) => { invalidateResult(); setPlanId(e.target.value); }}><option value="">请选择</option>{availablePlans.map((x) => <option key={x.id} value={x.id}>{x.name}{x.fee_rate_percent != null ? ` · ${x.fee_rate_percent}%` : ""}</option>)}</select></Field>
          <Field label="Year"><input disabled={settlementMutationBusy} type="number" min="2000" max="2200" value={year} onChange={(e) => { invalidateResult(); setYear(Number(e.target.value) || currentYear); setPlatformId(""); setPlanId(""); }} /></Field>
          <Field label="Quarter"><select disabled={settlementMutationBusy} value={quarter} onChange={(e) => { invalidateResult(); setQuarter(Number(e.target.value)); setPlatformId(""); setPlanId(""); }}><option value={1}>Q1</option><option value={2}>Q2</option><option value={3}>Q3</option><option value={4}>Q4</option></select></Field>
        </div>
        <div className="settlement-scope" role="status"><strong>{clientId ? clients.data.find((client) => client.id === Number(clientId))?.name : "先选择客户"} · {year} Q{quarter}</strong><span>{platformId ? platforms.data.find((platform) => platform.id === Number(platformId))?.name : "选择平台"} · {selectedPlan ? `${selectedPlan.name} · 利润收费 ${selectedPlan.fee_rate_percent}%` : "选择收费计划"}</span><span>当前组合 {groupAccounts.length} 个子账户 · 各账户独立HWM，亏损不抵消其他账户费用；最终按客户季度合并缴费单。</span></div>
        <div className="account-entry-table" tabIndex={0} role="region" aria-label="账户结算输入，可横向滚动">
          <div className="account-entry header"><span>加入</span><span>Sub Account</span><span>Starting Date</span><span>Closing Date</span><span>Beginning Snapshot</span><span>Closing Snapshot</span><span>Original HWM</span></div>
          {groupAccounts.length ? groupAccounts.map((account) => {
            const line = lines[account.id];
            const [quarterStart, quarterClosing] = quarterDates(year, quarter);
            const minimumStart = account.start_date && account.start_date > quarterStart ? account.start_date : quarterStart;
            const maximumClosing = account.end_date && account.end_date < quarterClosing ? account.end_date : quarterClosing;
            const beginningDates = new Set(line?.startDate ? [line.startDate] : []);
            if (line?.startDate === quarterStart) beginningDates.add(previousCalendarDate(quarterStart));
            const beginningOptions = snapshots.data.filter((snapshot) => snapshot.account_id === account.id && beginningDates.has(snapshot.as_of_date));
            const closingOptions = snapshots.data.filter((snapshot) => snapshot.account_id === account.id && snapshot.as_of_date === line?.closingDate && snapshot.eligible_for_closing);
            return <div className="account-entry" key={account.id}>
              <span><input type="checkbox" aria-label={`将Sub Account ${account.account_number}加入Settlement`} disabled={settlementMutationBusy} checked={lines[account.id]?.enabled ?? true} onChange={(e) => updateLine(account.id, { enabled: e.target.checked })} /></span>
              <span><strong>{account.account_number}</strong><small>{account.client_name} · {account.platform_name || "待确认Platform"} · {account.fee_plan_name}{account.scheme_name ? ` · ${account.scheme_name}` : ""}</small></span>
              <span><input aria-label={`${account.account_number} Starting Date`} type="date" disabled={settlementMutationBusy || !line?.enabled} min={minimumStart} max={line?.closingDate || maximumClosing} value={line?.startDate || ""} onChange={(e) => updateLine(account.id, { startDate: e.target.value, beginningSnapshotId: "" })} /></span>
              <span><input aria-label={`${account.account_number} Closing Date`} type="date" disabled={settlementMutationBusy || !line?.enabled} min={line?.startDate || minimumStart} max={maximumClosing} value={line?.closingDate || ""} onChange={(e) => updateLine(account.id, { closingDate: e.target.value, closingSnapshotId: "" })} /></span>
              <span><select aria-label={`${account.account_number} Beginning Snapshot`} disabled={settlementMutationBusy || !lines[account.id]?.enabled} value={lines[account.id]?.beginningSnapshotId || ""} onChange={(e) => updateLine(account.id, { beginningSnapshotId: e.target.value })}><option value="">自动继承；首次请选择</option>{beginningOptions.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>{snapshot.as_of_date} · HKD {snapshot.total_balance} · {snapshot.source_type === "STATEMENT_IMPORT" ? "账单导入" : "手工快照"} · {snapshot.evidence_complete ? "有凭证" : "待补凭证"}</option>)}</select></span>
              <span><select aria-label={`${account.account_number} Closing Snapshot`} disabled={settlementMutationBusy || !lines[account.id]?.enabled} value={lines[account.id]?.closingSnapshotId || ""} onChange={(e) => updateLine(account.id, { closingSnapshotId: e.target.value })}><option value="">请选择Closing</option>{closingOptions.map((snapshot) => <option key={snapshot.id} value={snapshot.id}>HKD {snapshot.total_balance} · {snapshot.source_type === "STATEMENT_IMPORT" ? "账单导入" : "手工快照"} · {snapshot.evidence_complete ? "有凭证" : "待补凭证"}</option>)}</select></span>
              <span><input aria-label={`${account.account_number} Original HWM`} type="number" min="0" step="0.01" placeholder="首次账户必填" disabled={settlementMutationBusy || !lines[account.id]?.enabled} value={lines[account.id]?.originalHwm || ""} onChange={(e) => updateLine(account.id, { originalHwm: e.target.value })} /></span>
            </div>;
          }) : <EmptyState title="没有匹配账户" detail="选择完整的Client、Platform和Fee Plan后，系统只显示同组且已填写开始管理日期的Active账户。" />}
        </div>
        <div className="form-actions"><button className="primary" disabled={settlementMutationBusy || !clientId || !platformId || !planId || !groupAccounts.length} onClick={() => void calculate()}><Calculator size={17} />{busy ? "计算中..." : "计算并保存Draft"}</button></div>
      </Panel>

      <Panel id="settlement-groups" title="本季账户组合总览" subtitle={`${year} Q${quarter} · 按客户、平台和收费计划分组。先核对每组账户及凭证，再逐组计算；已锁定组合缺少账户时，须通过原结算的作废/更正流程补齐。`}>
        {accounts.loading || settlements.loading ? <Loading /> : clientGroups.length ? <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="本季客户平台计划组合"><table><thead><tr><th>客户</th><th>平台 / 收费计划</th><th>子账户</th><th>结算进度</th><th>已锁定服务费</th><th>操作</th></tr></thead><tbody>{groupPages.rows.map((group) => {
            const first = group.accounts[0];
            const settled = group.settlement;
            const covered = settled?.status === "FINALIZED" ? group.accounts.filter((account) => settled.account_lines.some((line) => line.account_id === account.id)).length : 0;
            const rate = settled ? settled.fee_rate * 100 : plans.data.find((plan) => plan.id === first.fee_plan_id)?.fee_rate_percent;
            return <tr key={group.key} className={group.key === `${clientId}:${platformId}:${planId}` ? "selected" : ""}><td><strong>{first.client_name || clients.data.find((client) => client.id === first.client_id)?.name}</strong></td><td>{first.platform_name || platforms.data.find((platform) => platform.id === first.platform_id)?.name}<small className="cell-note">{first.fee_plan_name || plans.data.find((plan) => plan.id === first.fee_plan_id)?.name}{rate != null ? ` · ${rate}%${settled ? "（结算费率）" : ""}` : ""}</small></td><td>{group.accounts.map((account) => <small className="cell-note" key={account.id}>{account.account_number}{account.scheme_name ? ` · ${account.scheme_name}` : ""}</small>)}</td><td>{settled ? <StatusBadge value={settled.status} /> : "待计算"}<small className="cell-note">已锁定 {covered} / {group.accounts.length} 个账户{settled?.status === "FINALIZED" && covered < group.accounts.length ? " · 有账户遗漏" : ""}</small></td><td>{settled?.status === "FINALIZED" ? <Money value={settled.service_fee} /> : "待锁定"}</td><td><button className="ghost" disabled={settlementMutationBusy} onClick={() => {
              if (settled) { showHistoricalResult(settled); return; }
              invalidateResult(); setClientId(String(first.client_id)); setPlatformId(String(first.platform_id)); setPlanId(String(first.fee_plan_id));
              const panel = document.getElementById("settlement-create"); panel?.scrollIntoView?.({ block: "start" }); panel?.focus({ preventScroll: true });
            }}>{settled ? "查看结算" : "建立此组合"}</button></td></tr>;
          })}</tbody></table></div><Pagination {...groupPages} />
        </> : <EmptyState title="本季没有可计算账户" detail="核对账户是否Active、已分配平台和收费计划、开始管理日期是否落在本季或之前。" />}
      </Panel>

      {result ? <Panel id="settlement-result" title={`计算结果 · v${result.version_no}`} subtitle={`${result.calculation_mode === "ACCOUNT_HWM" ? "账户级HWM" : "历史组合HWM"} · Formula ${result.formula_version} · ${result.status === "DRAFT" ? "尚未锁定" : result.status === "FINALIZED" ? "已锁定" : "已作废并冻结为历史"}`}>
        <div className="settlement-scope"><strong>{result.client_name} · {result.year} Q{result.quarter}</strong><span>{result.platform_name} · {result.fee_plan_name} · 结算费率 {(result.fee_rate * 100).toFixed(2)}%</span><span>Settlement #{result.id} · {result.account_lines.length} 个账户。以下仅为这份结算，客户缴费单会合并本季所有已锁定平台及收费计划。</span></div>
        <div className="calculation-grid"><span><small>Beginning</small><Money value={result.beginning} /></span><span><small>Net Contribution</small><Money value={result.net_contribution} /></span><span><small>Closing</small><Money value={result.closing} /></span><span><small>Gain / Loss</small><Money value={result.gain_loss} /></span><span><small>Period Rate</small><strong>{result.period_rate == null ? "N/A" : `${(result.period_rate * 100).toFixed(2)}%`}</strong></span><span><small>Days（仅展示）</small><strong>{result.days}</strong></span><span><small>Adjusted HWM合计</small><Money value={result.adjusted_hwm} /></span><span><small>各账户Above HWM合计</small><Money value={result.chargeable_above_hwm} /></span><span className="highlight"><small>各账户Service Fee合计</small><Money value={result.service_fee} emphasis /></span><span><small>Next HWM合计</small><Money value={result.next_hwm} /></span></div>
        {result.account_lines.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap settlement-line-results"><table><thead><tr><th>Sub Account</th><th>账户期间</th><th>Beginning</th><th>Net Contribution</th><th>Closing</th><th>Original HWM</th><th>Above HWM</th><th>Service Fee</th><th>凭证</th></tr></thead><tbody>{result.account_lines.map((line) => { const account = accountById.get(line.account_id); return <tr key={line.id}><td><strong>{line.account_number}</strong><small className="cell-note">{account ? [account.client_name, account.platform_name || "待确认Platform", account.scheme_name].filter(Boolean).join(" · ") : `${result.client_name} · ${result.platform_name}`}</small></td><td>{line.start_date} 至 {line.closing_date}<small className="cell-note">{line.days}天</small></td><td><Money value={line.beginning} /></td><td><Money value={line.net_contribution || "0.00"} /></td><td><Money value={line.closing} /></td><td><Money value={line.original_hwm || "0.00"} /></td><td><Money value={line.chargeable_above_hwm || "0.00"} /></td><td><Money value={line.service_fee || "0.00"} /></td><td>{(line.beginning_evidence_count || 0) > 0 && (line.closing_evidence_count || 0) > 0 ? "完整" : "待补"}</td></tr>; })}</tbody></table></div> : null}
        {result.status === "VOID" ? <div className="invoice-candidate-warning">作废原因：{result.void_reason || "未记录"}{result.replaces_settlement_id ? ` · 本记录替代Settlement #${result.replaces_settlement_id}` : ""}</div> : null}
        <div className="form-actions">{result.status === "DRAFT" ? <><button className="primary" disabled={settlementMutationBusy} onClick={() => void finalize(result.id)}><CheckCircle2 size={17} />{finalizeBusy ? "Finalized处理中..." : "Finalized并锁定"}</button><button className="danger" disabled={settlementMutationBusy} onClick={() => void deleteDraft(result)}><Trash2 size={17} />{deleteBusy ? "删除中..." : "删除Draft"}</button></> : result.status === "FINALIZED" ? <><button className="secondary" disabled={settlementMutationBusy} onClick={() => void download(`/api/exports/pdf?settlement_id=${result.id}&language=zh`, `settlement_${result.id}_zh.pdf`, { method: "POST" })}><FileText size={17} />中文结算PDF</button><button className="secondary" disabled={settlementMutationBusy} onClick={() => void download(`/api/exports/pdf?settlement_id=${result.id}&language=en`, `settlement_${result.id}_en.pdf`, { method: "POST" })}><FileText size={17} />English PDF</button><button className="ghost" disabled={settlementMutationBusy} onClick={() => void download(`/api/exports/excel?settlement_ids=${result.id}`, `settlement_${result.id}.xlsx`, { method: "POST" })}><Download size={17} />内部Excel</button><button className="danger" disabled={settlementMutationBusy} onClick={() => void voidFinalized(result)}><Ban size={17} />{voidBusy ? "作废处理中..." : "作废Settlement"}</button></> : null}</div>
      </Panel> : null}

      <Panel id="settlement-export" title="公司内部财务Excel" subtitle="筛选并选择Finalized Settlement；系统按公司原Excel模板批量导出，逐Sub Account保留独立HWM与Service Fee">
        <div className="settlement-controls internal-export-filters">
          <Field label="Year"><select value={exportYear} onChange={(e) => setExportYear(Number(e.target.value))}>{exportYears.map((item) => <option key={item} value={item}>{item}</option>)}</select></Field>
          <Field label="Quarter"><select value={exportQuarter} onChange={(e) => setExportQuarter(e.target.value)}><option value="">全部季度</option><option value="1">Q1</option><option value="2">Q2</option><option value="3">Q3</option><option value="4">Q4</option></select></Field>
          <Field group label="Client"><SearchableSelect label="导出客户" value={exportClientId} onChange={setExportClientId} placeholder="全部客户" options={clients.data.map((item) => ({ value: String(item.id), label: item.name }))} /></Field>
        </div>
        {exportableSettlements.length ? <>
          <div className="internal-export-toolbar">
            <label><input type="checkbox" checked={allExportableSelected} onChange={toggleAllExportable} />选择当前筛选结果</label>
            <span>已选择 <strong>{selectedExportSettlements.length}</strong> 份Settlement</span>
            <span>Service Fee合计 <Money value={selectedServiceFee.toFixed(2)} emphasis /></span>
            <button className="primary" type="button" disabled={exportBusy || !selectedExportSettlements.length} onClick={() => void exportInternalExcel()}><Download size={17} />{exportBusy ? "正在生成..." : "导出所选内部Excel"}</button>
          </div>
          <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap"><table><thead><tr><th>选择</th><th>Period</th><th>Client</th><th>Platform / Plan</th><th>Sub Account / Scheme</th><th>口径</th><th>Closing</th><th>Service Fee</th></tr></thead><tbody>{exportableSettlements.map((item) => <tr key={item.id}><td><input type="checkbox" aria-label={`选择${item.year} Q${item.quarter} ${item.client_name} ${item.platform_name} ${item.fee_plan_name} Settlement v${item.version_no}`} checked={selectedExportIds.includes(item.id)} onChange={() => toggleExportSettlement(item.id)} /></td><td>{item.year} Q{item.quarter}</td><td>{item.client_name}</td><td>{item.platform_name}<small className="cell-note">{item.fee_plan_name}</small></td><td><SettlementAccountList item={item} accountById={accountById} /></td><td>{item.calculation_mode === "ACCOUNT_HWM" ? "账户级" : "历史组合"}</td><td><Money value={item.closing} /></td><td><Money value={item.service_fee} /></td></tr>)}</tbody></table></div>
        </> : <EmptyState title="没有可导出的结算" detail="当前筛选条件下没有Finalized Settlement。Draft和Void不会进入内部财务Excel。" />}
      </Panel>

      <Panel id="settlement-history" title="历史Settlement"><div className="list-search"><Field label="搜索结算客户、平台、计划或账户"><input type="search" value={historySearch} onChange={(event) => setHistorySearch(event.target.value)} placeholder="客户、平台、计划、账户或2026 Q2" /></Field><Field label="结算状态"><select value={historyStatus} onChange={(event) => setHistoryStatus(event.target.value)}><option value="">全部状态</option><option value="DRAFT">Draft</option><option value="FINALIZED">Finalized</option><option value="VOID">Void</option></select></Field></div>{settlements.loading ? <Loading /> : history.length ? <div tabIndex={0} role="region" aria-label="可滚动数据表格" className="table-wrap"><table><thead><tr><th>Period / Version</th><th>Client</th><th>Platform / Plan</th><th>Sub Account / Scheme</th><th>口径</th><th>Closing</th><th>Service Fee</th><th>Status</th></tr></thead><tbody>{historyPages.rows.map((item) => <tr key={item.id} onClick={() => showHistoricalResult(item)} onKeyDown={(event) => { if (!settlementMutationBusy && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); showHistoricalResult(item); } }} role="button" tabIndex={settlementMutationBusy ? -1 : 0} aria-label={`查看${item.year} Q${item.quarter} ${item.client_name} ${item.platform_name} ${item.fee_plan_name} Settlement v${item.version_no}`} className={settlementMutationBusy ? "" : "clickable"}><td>{item.year} Q{item.quarter}<small className="cell-note">v{item.version_no}{item.replaces_settlement_id ? ` · replaces #${item.replaces_settlement_id}` : ""}</small></td><td>{item.client_name}</td><td>{item.platform_name}<small className="cell-note">{item.fee_plan_name}</small></td><td><SettlementAccountList item={item} accountById={accountById} /></td><td>{item.calculation_mode === "ACCOUNT_HWM" ? "账户级" : "历史组合"}</td><td><Money value={item.closing} /></td><td><Money value={item.service_fee} /></td><td><StatusBadge value={item.status} />{item.status === "VOID" && item.void_reason ? <small className="cell-note">{item.void_reason}</small> : null}</td></tr>)}</tbody></table></div> : <EmptyState title="暂无结算" detail="调整搜索和状态筛选，或上方建立第一份季度Settlement。" />}<Pagination {...historyPages} /></Panel>
    </>
  );
}
