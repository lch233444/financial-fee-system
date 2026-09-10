import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SettlementsPage from "../src/pages/SettlementsPage";
import TransactionsPage from "../src/pages/TransactionsPage";
import InvoicesPage from "../src/pages/InvoicesPage";
import { settlementSourcesFollowOriginal } from "../src/invoiceSources";
import type { Settlement } from "../src/types";

const accounts = [
  { id: 1, client_id: 1, client_name: "客户甲", platform_id: 1, platform_name: "平台甲", fee_plan_id: 1, fee_plan_name: "计划20", account_number: "A-20", start_date: "2026-01-01", status: "ACTIVE" },
  { id: 2, client_id: 1, client_name: "客户甲", platform_id: 1, platform_name: "平台甲", fee_plan_id: 2, fee_plan_name: "计划10", account_number: "A-10", start_date: "2026-01-01", status: "ACTIVE" },
  { id: 3, client_id: 2, client_name: "客户乙", platform_id: 2, platform_name: "平台乙", fee_plan_id: 3, fee_plan_name: "计划15", account_number: "B-15", start_date: "2026-01-01", status: "ACTIVE" },
];
const records: Record<string, unknown[]> = {
  "/api/accounts": accounts,
  "/api/clients": [{ id: 1, name: "客户甲", status: "ACTIVE" }, { id: 2, name: "客户乙", status: "ACTIVE" }],
  "/api/platforms": [{ id: 1, name: "平台甲" }, { id: 2, name: "平台乙" }],
  "/api/fee-plans": [{ id: 1, name: "计划20", fee_rate_percent: 20 }, { id: 2, name: "计划10", fee_rate_percent: 10 }, { id: 3, name: "计划15", fee_rate_percent: 15 }],
};
async function choose(label: string, option: string) {
  fireEvent.focus(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

test("结算按客户联动平台和计划，切换后不会沿用其他客户的组合", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? []))));
  render(<SettlementsPage notify={vi.fn()} />);
  await choose("结算客户", "客户甲");
  const platform = screen.getByRole("combobox", { name: "Platform", exact: true });
  expect(within(platform).queryByRole("option", { name: "平台乙" })).toBeNull();
  fireEvent.change(platform, { target: { value: "1" } });
  const plan = screen.getByRole("combobox", { name: "Fee Plan", exact: true });
  expect(within(plan).queryByRole("option", { name: /计划15/ })).toBeNull();
  fireEvent.change(plan, { target: { value: "2" } });
  expect(screen.getByLabelText("A-10 Original HWM")).toBeTruthy();
  expect(screen.queryByLabelText("A-20 Original HWM")).toBeNull();
  await choose("结算客户", "客户乙");
  expect((platform as HTMLSelectElement).value).toBe("");
  expect((plan as HTMLSelectElement).value).toBe("");
  expect(screen.queryByLabelText("A-10 Original HWM")).toBeNull();
  const overview = screen.getByRole("region", { name: "本季账户组合总览" });
  expect(within(overview).getByText("B-15")).toBeTruthy();
  expect(within(overview).queryByText("A-10")).toBeNull();
});

test("流水筛选改变时清除已选账户但保留已输入金额，不能误提交旧客户", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? []))));
  render(<TransactionsPage notify={vi.fn()} />);
  await choose("资金与余额客户", "客户甲");
  const form = within(screen.getByRole("region", { name: "新增资金流水" }));
  const account = form.getByRole("combobox", { name: "Sub Account", exact: true });
  expect(within(account).queryByRole("option", { name: /B-15/ })).toBeNull();
  fireEvent.change(account, { target: { value: "2" } });
  fireEvent.change(form.getByLabelText("Amount (HKD)"), { target: { value: "321.09" } });
  await choose("资金与余额客户", "客户乙");
  expect((form.getByRole("combobox", { name: "Sub Account", exact: true }) as HTMLSelectElement).value).toBe("");
  expect((form.getByLabelText("Amount (HKD)") as HTMLInputElement).value).toBe("321.09");
});

test("同客户两计划只出现一个缴费单候选，总额准确且提示未纳入账户", async () => {
  const sources = accounts.map((account, index) => ({ id: index + 1, client_id: account.client_id, client_name: account.client_name, platform_id: account.platform_id, platform_name: account.platform_name, fee_plan_id: account.fee_plan_id, fee_plan_name: account.fee_plan_name,
    company_id: 1, fc_id: 1, year: 2026, quarter: 1, status: "FINALIZED", fee_rate: index === 0 ? 0.2 : 0.1,
    service_fee: index === 0 ? "20.01" : "30.01", account_lines: [{ id: index + 1, account_id: account.id, account_number: account.account_number, service_fee: index === 0 ? "20.01" : "30.01" }],
  }));
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/settlements" ? sources : path === "/api/accounts" ? [...accounts, { ...accounts[0], id: 4, account_number: "A-PENDING" }] : records[path] ?? []))));
  render(<InvoicesPage notify={vi.fn()} />);
  const combo = screen.getByRole("combobox", { name: "客户季度Invoice组合" });
  await waitFor(() => expect((combo as HTMLInputElement).disabled).toBe(false));
  fireEvent.focus(combo);
  const options = screen.getAllByRole("option").filter((option) => option.textContent?.includes("客户甲"));
  expect(options).toHaveLength(1);
  expect(options[0].textContent).toContain("50.02");
  fireEvent.click(options[0]);
  expect(screen.getByText(/A-PENDING/)).toBeTruthy();
  const preview = screen.getByRole("region", { name: "建立Invoice Draft" });
  expect(within(preview).getByText("A-20")).toBeTruthy();
  expect(within(preview).getByText("A-10")).toBeTruthy();
  expect(within(preview).queryByText("B-15")).toBeNull();
});

test("同平台的新收费计划可加入更正，但不能冒充旧计划替代链", () => {
  const data = new Map([
    { id: 1, fee_plan_id: 1, status: "VOID", replaces_settlement_id: null },
    { id: 2, fee_plan_id: 1, status: "FINALIZED", replaces_settlement_id: 1 },
    { id: 3, fee_plan_id: 2, status: "FINALIZED", replaces_settlement_id: null },
  ].map((item) => [item.id, { ...item, client_id: 1, platform_id: 1, year: 2026, quarter: 1 } as Settlement]));
  expect(settlementSourcesFollowOriginal([1], [2, 3], data)).toBe(true);
  expect(settlementSourcesFollowOriginal([1], [3], data)).toBe(false);
  data.set(3, { ...data.get(3)!, fee_plan_id: 1 });
  expect(settlementSourcesFollowOriginal([1], [2, 3], data)).toBe(false);
});
