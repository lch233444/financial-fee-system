import type { Account, Settlement } from "./types";
import { quarterDates } from "./hooks";

export function accountOverlapsQuarter(account: Account, year: number, quarter: number) {
  const [start, end] = quarterDates(year, quarter);
  return account.status === "ACTIVE" && Boolean(account.start_date)
    && account.start_date! <= end && (!account.end_date || account.end_date >= start);
}

export function settlementGroups(accounts: Account[], settlements: Settlement[], year: number, quarter: number) {
  const groups = new Map<string, { key: string; accounts: Account[]; settlement?: Settlement }>();
  for (const account of accounts) {
    if (!accountOverlapsQuarter(account, year, quarter) || account.platform_id == null || account.fee_plan_id == null) continue;
    const key = `${account.client_id}:${account.platform_id}:${account.fee_plan_id}`;
    const group = groups.get(key) ?? { key, accounts: [] };
    group.accounts.push(account);
    groups.set(key, group);
  }
  for (const settlement of settlements) {
    if (settlement.year !== year || settlement.quarter !== quarter || settlement.status === "VOID") continue;
    const group = groups.get(`${settlement.client_id}:${settlement.platform_id}:${settlement.fee_plan_id}`);
    if (group) group.settlement = settlement;
  }
  return [...groups.values()].sort((left, right) => {
    const a = left.accounts[0], b = right.accounts[0];
    return (a.client_name ?? "").localeCompare(b.client_name ?? "", "zh-Hans-CN")
      || a.client_id - b.client_id || a.platform_id! - b.platform_id! || a.fee_plan_id! - b.fee_plan_id!;
  });
}
