import { hwmBasis } from "./hwmBasis";
import { periodDateRange } from "./periodFilters";
import type { Account, BalanceSnapshot, Settlement } from "./types";

export const transactionLabels: Record<string, string> = {
  MONTHLY_CONTRIBUTION: "供款（月供）",
  CONTRIBUTION: "加款",
  WITHDRAWAL: "取款",
};

/** Include the same opening sources offered by fee calculation, never an arbitrary latest balance. */
export function periodSnapshots(accounts: Account[], snapshots: BalanceSnapshot[], settlements: Settlement[], year: string, quarter: string) {
  const { start, end } = periodDateRange(year, quarter);
  const accountIds = new Set(accounts.map((item) => item.id));
  const openingIds = new Set<number>();
  for (const account of accounts) {
    const existingLines = settlements.filter((item) => item.status !== "VOID" && item.year === Number(year)
      && (!quarter || item.quarter === Number(quarter))).flatMap((item) => item.account_lines.filter((line) => line.account_id === account.id));
    existingLines.forEach((line) => { if (line.beginning_snapshot_id) openingIds.add(line.beginning_snapshot_id); });
    if (!account.start_date || account.start_date > end || (account.end_date && account.end_date < start)) continue;
    const startDate = account.start_date && account.start_date > start ? account.start_date : start;
    const targetPeriod = Number(year) * 4 + (Number(quarter) || 1);
    const previous = settlements.filter((item) => item.status === "FINALIZED" && item.year * 4 + item.quarter < targetPeriod
      && item.account_lines.some((line) => line.account_id === account.id))
      .sort((a, b) => b.year * 4 + b.quarter - (a.year * 4 + a.quarter))[0];
    // An unfinished intervening quarter blocks inheritance, just as in Calculate.
    if (previous && previous.year * 4 + previous.quarter !== targetPeriod - 1) continue;
    const basis = hwmBasis(account.id, { startDate, beginningSnapshotId: "", originalHwm: "", overrideReason: "", confirmedBasis: "" }, snapshots, settlements, Number(year), Number(quarter) || 1);
    if (basis.snapshot) openingIds.add(basis.snapshot.id);
    else basis.options.forEach((item) => openingIds.add(item.id));
  }
  return snapshots.filter((item) => accountIds.has(item.account_id) && ((item.as_of_date >= start && item.as_of_date <= end) || openingIds.has(item.id)))
    .map((item) => ({ ...item, opening: item.as_of_date < start && openingIds.has(item.id) }));
}
