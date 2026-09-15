import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SettlementsPage from "../src/pages/SettlementsPage";

const account = { id: 1, client_id: 1, client_name: "客户甲", platform_id: 1, platform_name: "平台甲", fee_plan_id: 1, fee_plan_name: "计划20", account_number: "Q1-ONLY", start_date: "2026-01-01", end_date: "2026-03-31", status: "ACTIVE" };
const records: Record<string, unknown[]> = {
  "/api/clients": [{ id: 1, name: "客户甲", status: "ACTIVE" }, { id: 2, name: "客户乙", status: "ACTIVE" }],
  "/api/platforms": [{ id: 1, name: "平台甲" }],
  "/api/fee-plans": [{ id: 1, name: "计划20", fee_rate_percent: 20 }],
  "/api/accounts": [account,
    { ...account, id: 2, account_number: "Q2-ONLY", start_date: "2026-04-01", end_date: "2026-06-30" },
    { ...account, id: 3, account_number: "PRIVATE-B", client_id: 2, client_name: "客户乙" },
  ],
  "/api/balance-snapshots": [{ id: 1, account_id: 1, as_of_date: "2026-01-01", total_balance: "1000.00", evidence_complete: true }, { id: 2, account_id: 1, as_of_date: "2026-03-31", total_balance: "1100.00", eligible_for_closing: true, evidence_complete: true }],
};

async function chooseClient() {
  const input = screen.getByRole("combobox", { name: "结算客户" });
  fireEvent.focus(input);
  fireEvent.click(await screen.findByRole("option", { name: "客户甲" }));
}

test("组合总览先按年季选择，只有明确选定客户才显示其账户，换季清除旧组合及输入", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? []))));
  render(<SettlementsPage notify={vi.fn()} />);
  const overview = within(screen.getByRole("region", { name: "本季账户组合总览" }));
  fireEvent.change(overview.getByLabelText("结算年份"), { target: { value: "2026" } });
  await waitFor(() => expect((screen.getByRole("combobox", { name: "结算客户" }) as HTMLInputElement).disabled).toBe(false));
  expect(overview.queryByText("Q1-ONLY")).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "结算客户" }), { target: { value: "客户甲" } });
  expect(overview.queryByText("Q1-ONLY")).toBeNull();
  await chooseClient();
  expect(overview.getByText("Q1-ONLY")).toBeTruthy();
  expect(overview.queryByText("PRIVATE-B")).toBeNull();
  expect(overview.queryByText("Q2-ONLY")).toBeNull();
  fireEvent.click(overview.getByRole("button", { name: "建立此组合" }));
  fireEvent.change(screen.getByLabelText("Q1-ONLY Original HWM"), { target: { value: "1234.56" } });
  fireEvent.change(screen.getByLabelText("Q1-ONLY Beginning Snapshot"), { target: { value: "1" } });
  fireEvent.change(overview.getByLabelText("结算季度"), { target: { value: "2" } });
  expect(overview.queryByText("Q1-ONLY")).toBeNull();
  expect(overview.getByText("Q2-ONLY")).toBeTruthy();
  expect(screen.queryByLabelText("Q1-ONLY Original HWM")).toBeNull();
  expect((screen.getByLabelText("Platform") as HTMLSelectElement).value).toBe("");
  fireEvent.click(overview.getByRole("button", { name: "建立此组合" }));
  expect((screen.getByLabelText("Q2-ONLY Starting Date") as HTMLInputElement).value).toBe("2026-04-01");
  expect((screen.getByLabelText("Q2-ONLY Original HWM") as HTMLInputElement).value).toBe("");
  fireEvent.change(overview.getByLabelText("结算季度"), { target: { value: "1" } });
  fireEvent.click(overview.getByRole("button", { name: "建立此组合" }));
  expect((screen.getByLabelText("Q1-ONLY Original HWM") as HTMLInputElement).value).toBe("1000.00");
  expect((screen.getByLabelText("Q1-ONLY Beginning Snapshot") as HTMLSelectElement).value).toBe("1");
});

test("换年清除旧计算结果和可提交账户，清空客户后总览恢复隐藏", async () => {
  const settlement = { id: 1, client_id: 1, client_name: "客户甲", platform_id: 1, platform_name: "平台甲", fee_plan_id: 1, fee_plan_name: "计划20", year: 2026, quarter: 1, status: "DRAFT", version_no: 1, calculation_mode: "ACCOUNT_HWM", fee_rate: 0.2, account_lines: [], service_fee: "20.00" };
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/settlements" ? [settlement] : records[path] ?? []))));
  render(<SettlementsPage notify={vi.fn()} />);
  const overview = within(screen.getByRole("region", { name: "本季账户组合总览" }));
  fireEvent.change(overview.getByLabelText("结算年份"), { target: { value: "2026" } });
  await chooseClient();
  fireEvent.click(overview.getByRole("button", { name: "查看结算" }));
  expect(screen.getByRole("region", { name: "计算结果 · v1" })).toBeTruthy();
  fireEvent.change(overview.getByLabelText("结算年份"), { target: { value: "2027" } });
  expect(screen.queryByRole("region", { name: "计算结果 · v1" })).toBeNull();
  expect(overview.getByText("本季没有可计算账户")).toBeTruthy();
  expect((screen.getByRole("button", { name: "计算并保存Draft" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(overview.getByLabelText("结算年份"), { target: { value: "2026" } });
  expect(overview.getByText("Q1-ONLY")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "清空结算客户" }));
  expect(overview.queryByText("Q1-ONLY")).toBeNull();
  expect(overview.getByText("先搜索并选定客户")).toBeTruthy();
});
