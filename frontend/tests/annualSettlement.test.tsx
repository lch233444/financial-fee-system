import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { setDatasetState } from "../src/datasetSession";
import { DatasetManagement, DatasetSelector } from "../src/Datasets";
import SettlementsPage from "../src/pages/SettlementsPage";
import { quarterDates } from "../src/hooks";
import { periodSnapshots } from "../src/cashRecords";
import type { Account, BalanceSnapshot, Settlement } from "../src/types";

const state = { active_id: "annual", token: "annual-token", settlement_cycle: "ANNUAL" as const,
  datasets: [{ id: "primary", name: "季度客户", settlement_cycle: "QUARTERLY" as const },
    { id: "annual", name: "年度客户", settlement_cycle: "ANNUAL" as const }] };
afterEach(() => setDatasetState({ ...state, active_id: "primary", settlement_cycle: "QUARTERLY" }));

test("新建空白包明确选择自然年度，不提交客户或旧包资料", async () => {
  setDatasetState(state);
  const fetcher = vi.fn(async (_path, options) => new Response(JSON.stringify(options?.method === "POST" ? { id: "new" } : state)));
  vi.stubGlobal("fetch", fetcher);
  render(<><DatasetSelector /><DatasetManagement notify={vi.fn()} /></>);
  expect(screen.getByRole("option", { name: "年度客户 · 自然年度结算" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("新数据包名称"), { target: { value: "年度新客户" } });
  fireEvent.change(screen.getByLabelText("结算周期"), { target: { value: "ANNUAL" } });
  fireEvent.click(screen.getByRole("button", { name: "新建空白数据包" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ name: "年度新客户", settlement_cycle: "ANNUAL" });
});

test("年度计算没有季度选项，显示完整年期并仅允许年末或实际退出日", async () => {
  setDatasetState(state);
  const records: Record<string, unknown[]> = {
    "/api/clients": [{ id: 1, name: "年度客户甲", status: "ACTIVE" }],
    "/api/platforms": [{ id: 1, name: "年度平台" }],
    "/api/fee-plans": [{ id: 1, name: "利润收费", fee_rate_percent: 20 }],
    "/api/accounts": [{ id: 1, client_id: 1, client_name: "年度客户甲", platform_id: 1,
      platform_name: "年度平台", fee_plan_id: 1, fee_plan_name: "利润收费", account_number: "ANNUAL-1",
      start_date: "2026-01-01", end_date: null, status: "ACTIVE" }],
    "/api/balance-snapshots": [
      { id: 1, account_id: 1, as_of_date: "2025-12-31", total_balance: "1000.00", evidence_complete: true },
      { id: 2, account_id: 1, as_of_date: "2026-12-31", total_balance: "1600.00", evidence_complete: true },
      { id: 3, account_id: 1, as_of_date: "2026-03-31", total_balance: "1200.00", evidence_complete: true }],
  };
  vi.stubGlobal("fetch", vi.fn(async path => new Response(JSON.stringify(records[path as string] ?? []))));
  render(<SettlementsPage notify={vi.fn()} />);
  expect(screen.queryByLabelText("结算季度")).toBeNull();
  const overview = within(screen.getByRole("region", { name: "1 · 选择客户与年度" }));
  fireEvent.change(overview.getByLabelText("结算年份"), { target: { value: "2026" } });
  fireEvent.focus(screen.getByRole("combobox", { name: "结算客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "年度客户甲" }));
  fireEvent.click(overview.getByRole("button", { name: "建立此组合" }));
  expect((screen.getByLabelText("ANNUAL-1 开始日期") as HTMLInputElement).value).toBe("2026-01-01");
  expect((screen.getByLabelText("ANNUAL-1 结算日期") as HTMLInputElement).value).toBe("2026-12-31");
  const closing = within(screen.getByLabelText("ANNUAL-1 期末结余"));
  expect(closing.getByRole("option", { name: /1600.00/ })).toBeTruthy();
  expect(closing.queryByRole("option", { name: /1200.00/ })).toBeNull();
  expect(document.body.textContent).not.toContain("第0季度");
  expect(quarterDates(2024, 0)).toEqual(["2024-01-01", "2024-12-31"]);
});

test("年度资金查询带出上一年期末，缺年时不误认更早期末", () => {
  setDatasetState(state);
  const accounts = [{ id: 1, start_date: "2025-01-01" }] as Account[];
  const snapshots = [{ id: 1, account_id: 1, as_of_date: "2025-12-31", total_balance: "1600.00" }] as BalanceSnapshot[];
  const settlements = [{ year: 2025, quarter: 0, status: "FINALIZED", account_lines: [
    { account_id: 1, closing_snapshot_id: 1, next_hwm: "1600.00" }] }] as Settlement[];
  expect(periodSnapshots(accounts, snapshots, settlements, "2026", "").map(item => item.id)).toEqual([1]);
  expect(periodSnapshots(accounts, snapshots, settlements, "2027", "")).toEqual([]);
});
