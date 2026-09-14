import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ClientsPage from "../src/pages/ClientsPage";
import SettlementsPage from "../src/pages/SettlementsPage";
import TransactionsPage from "../src/pages/TransactionsPage";

const accounts = [1, 2].map((id) => ({ id, client_id: id, client_name: `客户${id}`, account_number: `ACCOUNT-${id}`, platform_id: 1, platform_name: "平台", fee_plan_id: 1, fee_plan_name: "计划", start_date: "2026-01-01", status: "DRAFT" }));
const records: Record<string, unknown[]> = {
  "/api/accounts": accounts,
  "/api/clients": [1, 2].map((id) => ({ id, name: `客户${id}`, fc_name: "FC", status: "ACTIVE" })),
  "/api/platforms": [{ id: 1, name: "平台" }], "/api/fee-plans": [{ id: 1, name: "计划", code: "P", fee_rate_percent: 20 }],
  "/api/transactions": accounts.map((a) => ({ id: a.id, account_id: a.id, account_number: a.account_number, transaction_date: "2026-01-04", transaction_type: "CONTRIBUTION", amount: "10.00", correction_allowed: true })),
  "/api/balance-snapshots": accounts.map((a) => ({ id: a.id, account_id: a.id, account_number: a.account_number, as_of_date: "2026-01-01", total_balance: "1000.00", holdings: [] })),
  "/api/settlements": accounts.map((a) => ({ id: a.id, client_id: a.client_id, client_name: a.client_name, platform_id: 1, platform_name: "平台", fee_plan_id: 1, fee_plan_name: "计划", year: 2026, quarter: 1, version_no: 1, status: "FINALIZED", calculation_mode: "ACCOUNT_HWM", service_fee: "20.00", fee_rate: 0.2, closing: "1100.00", account_lines: [{ id: a.id, account_id: a.id, account_number: a.account_number }] })),
};
function setup() { vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? [])))); }
async function choose(label: string, option: string) {
  const input = screen.getByRole("combobox", { name: label });
  fireEvent.focus(input); fireEvent.change(input, { target: { value: option } });
  fireEvent.click(await screen.findByRole("option", { name: option }));
}
test("客户查询和待确认账户均先明确选择客户，文字搜索不展开账户，切换清除旧选择", async () => {
  setup(); render(<ClientsPage notify={vi.fn()} />);
  await screen.findByText("客户1");
  expect(screen.queryByText("ACCOUNT-1")).toBeNull();
  const input = screen.getByRole("combobox", { name: "查询客户账户" });
  fireEvent.focus(input); fireEvent.change(input, { target: { value: "客户1" } });
  expect(screen.queryByText("ACCOUNT-1")).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: "客户1 · FC · 客户#1" }));
  expect(screen.getByText("ACCOUNT-1")).toBeTruthy();
  expect(screen.queryByText("ACCOUNT-2")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "清空查询客户账户" }));
  expect(screen.queryByText("ACCOUNT-1")).toBeNull();
  const draft = screen.getByLabelText("选择Draft Sub Account") as HTMLSelectElement;
  expect(draft.disabled).toBe(true);
  await choose("待确认账户客户", "客户1 · 客户#1");
  fireEvent.change(draft, { target: { value: "1" } });
  expect(screen.getByRole("button", { name: "补全并激活Sub Account" })).toBeTruthy();
  await choose("待确认账户客户", "客户2 · 客户#2");
  expect(draft.value).toBe("");
  expect(screen.queryByRole("button", { name: "补全并激活Sub Account" })).toBeNull();
  expect(within(draft).queryByRole("option", { name: /ACCOUNT-1/ })).toBeNull();
});
test("资金流水、快照、账户及凭证关联在未选客户时均隐藏，切客清除更正与旧关联", async () => {
  setup(); render(<TransactionsPage notify={vi.fn()} />);
  const input = screen.getByRole("combobox", { name: "资金与余额客户" });
  fireEvent.focus(input); await screen.findByRole("option", { name: "客户1" });
  fireEvent.change(input, { target: { value: "客户1" } });
  expect(screen.queryByText(/ACCOUNT-1/)).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: "客户1" }));
  const ledger = within(screen.getByRole("region", { name: "最近资金流水" }));
  fireEvent.click(ledger.getByRole("button", { name: "更正" }));
  fireEvent.change(screen.getByLabelText("关联记录"), { target: { value: "1" } });
  await choose("资金与余额客户", "客户2");
  expect(screen.queryByRole("button", { name: "保存更正" })).toBeNull();
  expect((screen.getByLabelText("关联记录") as HTMLSelectElement).value).toBe("");
  expect(screen.queryByText(/ACCOUNT-1/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "清空资金与余额客户" }));
  expect(screen.queryByText(/ACCOUNT-2/)).toBeNull();
  expect((screen.getByRole("button", { name: "保存流水" }) as HTMLButtonElement).disabled).toBe(true);
});
test("历史结算和批量导出保留汇总，未选客户时不显示账户；清空恢复隐藏", async () => {
  setup(); render(<SettlementsPage notify={vi.fn()} />);
  await screen.findByRole("button", { name: /查看2026 Q1 客户1/ });
  expect(screen.queryByText("ACCOUNT-1")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /查看2026 Q1 客户1/ }));
  expect(screen.queryByText("ACCOUNT-1")).toBeNull();
  await choose("历史结算客户", "客户1");
  const history = within(screen.getByRole("region", { name: "历史Settlement" }));
  expect(history.getByText("ACCOUNT-1")).toBeTruthy();
  expect(history.queryByText("ACCOUNT-2")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "清空历史结算客户" }));
  expect(screen.queryByText("ACCOUNT-1")).toBeNull();
  await choose("导出客户", "客户2");
  const excel = within(screen.getByRole("region", { name: "公司内部财务Excel" }));
  expect(excel.getByText("ACCOUNT-2")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "清空导出客户" }));
  await waitFor(() => expect(screen.queryByText("ACCOUNT-2")).toBeNull());
  expect(excel.getAllByRole("checkbox")).toHaveLength(3);
});
