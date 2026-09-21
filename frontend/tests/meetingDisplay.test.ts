import { expect, test } from "vitest";
import { accountIdentityDetail, formatDate } from "../src/types";
import { periodSnapshots, transactionLabels } from "../src/cashRecords";
import type { Account, BalanceSnapshot, Settlement } from "../src/types";

test("复合身份保留账户及收费计划，同名平台与Scheme只显示一次，不同名保留", () => {
  const account = { platform_name: "合成平台", account_number: "SYN-001", scheme_name: "合成平台", fee_plan_name: "收费20%" };
  expect(accountIdentityDetail(account)).toBe("合成平台 · SYN-001 · 收费计划 收费20%");
  expect(accountIdentityDetail({ ...account, scheme_name: "独立账户计划" })).toBe("合成平台 · SYN-001 · 独立账户计划 · 收费计划 收费20%");
});

test("网页日期保留自然日且月日补零，原API值不变", () => {
  const source = "2026-01-02";
  expect(formatDate(source)).toBe("2026年01月02日");
  expect(source).toBe("2026-01-02");
  expect(formatDate("2026-09-21T00:00:00Z")).toBe("2026年09月21日");
  expect(formatDate(null)).toBe("—");
});

test("历史流入全部显示加款，月供独立显示", () => {
  expect(transactionLabels.CONTRIBUTION).toBe("加款");
  expect(transactionLabels.MONTHLY_CONTRIBUTION).toBe("供款（月供）");
});

test("季度历史结余包含本季和适用期初，不取任意最近一天或其他账户", () => {
  const accounts = [{ id: 1, start_date: "2026-01-01" }] as Account[];
  const snapshots = [
    { id: 1, account_id: 1, as_of_date: "2026-03-15" },
    { id: 2, account_id: 1, as_of_date: "2026-03-31" },
    { id: 3, account_id: 1, as_of_date: "2026-06-30" },
    { id: 4, account_id: 2, as_of_date: "2026-06-30" },
    { id: 5, account_id: 1, as_of_date: "2026-09-30" },
  ] as BalanceSnapshot[];
  const rows = periodSnapshots(accounts, snapshots, [], "2026", "2");
  expect(rows.map((item) => item.id)).toEqual([2, 3]);
  expect(rows.find((item) => item.id === 2)?.opening).toBe(true);
  expect(periodSnapshots(accounts, snapshots.filter((item) => item.id !== 2), [], "2026", "2").map((item) => item.id)).toEqual([3]);
});

test("已计算记录使用的期初来源保留在当前筛选中", () => {
  const snapshots = [{ id: 8, account_id: 1, as_of_date: "2026-03-31" }, { id: 9, account_id: 1, as_of_date: "2026-06-30" }] as BalanceSnapshot[];
  const settlements = [{ year: 2026, quarter: 2, status: "FINALIZED", account_lines: [{ account_id: 1, beginning_snapshot_id: 8 }] }] as Settlement[];
  expect(periodSnapshots([{ id: 1, start_date: "2026-01-01" }] as Account[], snapshots, settlements, "2026", "2").map((item) => item.id)).toEqual([8, 9]);
});

test("不能把隔季的已锁定结余误标成本季期初，也不带出未来账户的结余", () => {
  const snapshots = [{ id: 1, account_id: 1, as_of_date: "2026-03-31" }, { id: 2, account_id: 1, as_of_date: "2026-06-30" }, { id: 3, account_id: 1, as_of_date: "2026-09-30" }, { id: 4, account_id: 2, as_of_date: "2026-10-01" }] as BalanceSnapshot[];
  const settlements = [{ year: 2026, quarter: 1, status: "FINALIZED", account_lines: [{ account_id: 1, closing_snapshot_id: 1 }] }] as Settlement[];
  const accounts = [{ id: 1, start_date: "2026-01-01" }, { id: 2, start_date: "2026-10-01" }] as Account[];
  expect(periodSnapshots(accounts, snapshots, settlements, "2026", "3").map((item) => item.id)).toEqual([3]);
});
