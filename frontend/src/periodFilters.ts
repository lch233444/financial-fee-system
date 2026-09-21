import type { Account } from "./types";

export type RecordFilterValue = { clientId: string; year: string; quarter: string; fcId: string; feePlanId: string };

export const defaultRecordFilters: RecordFilterValue = { clientId: "", year: "2026", quarter: "", fcId: "", feePlanId: "" };

export function periodDateRange(year: string, quarter: string) {
  const selectedYear = /^\d{4}$/.test(year) && Number(year) >= 2000 && Number(year) <= 2200 ? Number(year) : 2026;
  const selectedQuarter = /^[1-4]$/.test(quarter) ? Number(quarter) : null;
  const firstMonth = selectedQuarter ? (selectedQuarter - 1) * 3 : 0;
  const lastMonth = selectedQuarter ? selectedQuarter * 3 : 12;
  return {
    start: `${selectedYear}-${String(firstMonth + 1).padStart(2, "0")}-01`,
    end: new Date(Date.UTC(selectedYear, lastMonth, 0)).toISOString().slice(0, 10),
  };
}

export function dateInPeriod(date: string | null | undefined, year: string, quarter: string) {
  if (!date) return false;
  const { start, end } = periodDateRange(year, quarter);
  return date >= start && date <= end;
}

export function accountManagedInPeriod(account: Account, year: string, quarter: string) {
  if (!['ACTIVE', 'CLOSED'].includes(account.status) || !account.start_date || (account.status === "CLOSED" && !account.end_date)) return false;
  const { start, end } = periodDateRange(year, quarter);
  return account.start_date <= end && (!account.end_date || account.end_date >= start);
}
