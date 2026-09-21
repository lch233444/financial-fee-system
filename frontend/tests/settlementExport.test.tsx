import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SettlementsPage from "../src/pages/SettlementsPage";
import { download } from "../src/api";

vi.mock("../src/api", async (original) => ({ ...await original<typeof import("../src/api")>(), download: vi.fn(async () => {}) }));

const base = { client_id: 1, client_name: "客户甲", platform_id: 1, platform_name: "平台甲", year: 2026, quarter: 1, version_no: 1, status: "FINALIZED", calculation_mode: "ACCOUNT_HWM", closing: "1100.00", account_lines: [] };
const records: Record<string, unknown[]> = {
  "/api/clients": [{ id: 1, name: "客户甲", status: "ACTIVE" }, { id: 2, name: "客户乙", status: "ACTIVE" }],
  "/api/platforms": [{ id: 1, name: "平台甲" }],
  "/api/fee-plans": [{ id: 10, name: "计划20", code: "P20", fee_rate_percent: 20 }, { id: 30, name: "计划30", code: "P30", fee_rate_percent: 30 }],
  "/api/settlements": [
    { ...base, id: 1, fee_plan_id: 10, fee_plan_name: "计划20", service_fee: "20.01" },
    { ...base, id: 2, fee_plan_id: 30, fee_plan_name: "计划30", service_fee: "90.02" },
    { ...base, id: 3, fee_plan_id: 30, fee_plan_name: "计划30", client_id: 2, client_name: "客户乙", service_fee: "30.00" },
    { ...base, id: 4, fee_plan_id: 30, fee_plan_name: "计划30", quarter: 2, service_fee: "40.00" },
    { ...base, id: 5, fee_plan_id: 30, fee_plan_name: "计划30", year: 2025, service_fee: "50.00" },
    { ...base, id: 6, fee_plan_id: 30, fee_plan_name: "计划30", status: "DRAFT", service_fee: "60.00" },
    { ...base, id: 7, fee_plan_id: 30, fee_plan_name: "计划30", status: "VOID", service_fee: "70.00" },
  ],
};

test("Excel按计划与年季客户联合筛选，切换清空旧勾选，提交仅含当前所选ID", async () => {
  vi.mocked(download).mockClear();
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? []))));
  render(<SettlementsPage notify={vi.fn()} />);
  const panel = within(screen.getByRole("region", { name: "公司内部财务Excel" }));
  await waitFor(() => expect(panel.getAllByRole("checkbox").length).toBeGreaterThan(1));
  fireEvent.change(panel.getByLabelText("Year"), { target: { value: "2026" } });
  fireEvent.change(panel.getByLabelText("Quarter"), { target: { value: "1" } });
  fireEvent.click(panel.getByRole("checkbox", { name: "选择当前筛选结果" }));
  fireEvent.click(panel.getByRole("button", { name: "导出所选内部Excel" }));
  await waitFor(() => expect(download).toHaveBeenLastCalledWith("/api/exports/excel?settlement_ids=1,2,3", expect.any(String), { method: "POST" }));
  fireEvent.focus(panel.getByRole("combobox", { name: "导出收费计划" }));
  fireEvent.click(await screen.findByRole("option", { name: "计划30" }));
  expect(panel.getAllByRole("checkbox")).toHaveLength(3);
  expect((panel.getByRole("button", { name: "导出所选内部Excel" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.focus(panel.getByRole("combobox", { name: "导出客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "客户甲" }));
  fireEvent.click(panel.getByRole("checkbox", { name: "选择当前筛选结果" }));
  fireEvent.click(panel.getByRole("button", { name: "导出所选内部Excel" }));
  await waitFor(() => expect(download).toHaveBeenLastCalledWith("/api/exports/excel?settlement_ids=2&fee_plan_id=30", expect.any(String), { method: "POST" }));
  fireEvent.change(panel.getByLabelText("Quarter"), { target: { value: "2" } });
  expect((panel.getByRole("button", { name: "导出所选内部Excel" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(panel.getByRole("checkbox", { name: "选择当前筛选结果" }));
  fireEvent.change(panel.getByLabelText("Year"), { target: { value: "2025" } });
  expect(panel.getByText("没有可导出的结算")).toBeTruthy();
  fireEvent.change(panel.getByLabelText("Quarter"), { target: { value: "1" } });
  expect((panel.getByRole("button", { name: "导出所选内部Excel" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(panel.getByRole("button", { name: "清空导出收费计划" }));
  expect((panel.getByRole("button", { name: "导出所选内部Excel" }) as HTMLButtonElement).disabled).toBe(true);
});
