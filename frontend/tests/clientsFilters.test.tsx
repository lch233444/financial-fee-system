import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ClientsPage from "../src/pages/ClientsPage";
import { accountManagedInPeriod, periodDateRange } from "../src/periodFilters";
import type { Account } from "../src/types";

vi.mock("../src/pages/ImportsPage", () => ({ default: ({ embedded, onConfirmed }: { embedded?: boolean; onConfirmed?: () => void }) => <button onClick={onConfirmed}>{embedded ? "嵌入导入确认" : "独立导入"}</button> }));

function setup(initialQuarter = "2") {
  const accounts = [
    { id: 1, client_id: 1, status: "ACTIVE", start_date: "2026-01-01", end_date: null, fee_plan_id: 1, fee_plan_name: "基础计划", account_number: "ACCOUNT-A" },
    { id: 2, client_id: 1, status: "CLOSED", start_date: "2025-01-01", end_date: "2026-04-01", fee_plan_id: 2, fee_plan_name: "特殊计划", account_number: "ACCOUNT-B" },
    { id: 3, client_id: 2, status: "CLOSED", start_date: "2025-01-01", end_date: "2026-05-31", fee_plan_id: 2, fee_plan_name: "特殊计划", account_number: "ACCOUNT-C" },
    { id: 4, client_id: 3, status: "ACTIVE", start_date: "2026-07-01", end_date: null, fee_plan_id: 1, fee_plan_name: "基础计划" },
    { id: 5, client_id: 4, status: "CLOSED", start_date: "2026-01-01", end_date: null },
    { id: 6, client_id: 5, status: "ACTIVE", start_date: null, end_date: null },
    { id: 7, client_id: 6, status: "DRAFT", start_date: "2026-01-01", end_date: null },
  ];
  const clients = ["客户甲", "客户乙", "未来客户", "结束缺日期", "开始缺日期", "待补全客户", "无账户客户"].map((name, index) => ({ id: index + 1, name, fc_id: index === 1 ? 2 : 1, fc_name: index === 1 ? "顾问乙" : "顾问甲", management_start_date: "2020-01-01", status: index === 5 ? "DRAFT" : index === 1 ? "CLOSED" : "ACTIVE" }));
  const records: Record<string, unknown> = { "/api/accounts": accounts, "/api/clients": clients, "/api/fcs": [{ id: 1, name: "顾问甲" }, { id: 2, name: "顾问乙" }], "/api/fee-plans": [{ id: 1, name: "基础计划" }, { id: 2, name: "特殊计划" }] };
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? [])));
  vi.stubGlobal("fetch", fetcher);
  const view = render(<ClientsPage notify={vi.fn()} initialYear="2026" initialQuarter={initialQuarter} />);
  return { ...view, fetcher };
}

async function choose(label: string, name: string) {
  fireEvent.focus(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name, exact: true }));
}

test("期间客户按实际子账户去重，已结束账户及无账单客户计入，缺日期和Draft不计入", async () => {
  setup();
  await screen.findByText("客户甲");
  const directory = screen.getByRole("region", { name: "客户与账户清单" });
  expect(within(directory).getByText("客户乙")).toBeTruthy();
  expect(within(directory).getByText("显示 2 / 2 位在管客户")).toBeTruthy();
  expect(within(directory).getByText("收费计划：基础计划、特殊计划")).toBeTruthy();
  expect(within(directory).getByText("管理期间：2025年01月01日 至 2026年04月01日")).toBeTruthy();
  for (const name of ["未来客户", "结束缺日期", "开始缺日期", "待补全客户", "无账户客户"]) expect(within(directory).queryByText(name)).toBeNull();
  expect(directory.textContent).not.toContain("2020年");
  expect(directory.textContent).not.toMatch(/客户\s*#|顾问甲\s*\(|Code/);
  expect(within(directory).queryByText("ACCOUNT-A")).toBeNull();
});

test("客户、FC、收费计划和年季组合筛选，明确选客后才展开账户", async () => {
  setup();
  await screen.findByText("客户甲");
  const directory = screen.getByRole("region", { name: "客户与账户清单" });
  await choose("Fee Plan筛选", "特殊计划");
  await choose("FC筛选", "顾问甲");
  expect(within(directory).getByText("显示 1 / 2 位在管客户")).toBeTruthy();
  expect(within(directory).queryByText("客户乙")).toBeNull();
  await choose("查询客户账户", "客户甲");
  expect(within(directory).getByText("ACCOUNT-B")).toBeTruthy();
  expect(within(directory).queryByText("ACCOUNT-A")).toBeNull();
  fireEvent.change(screen.getByLabelText("季度"), { target: { value: "3" } });
  expect(within(directory).getByText("所选条件下没有在管客户")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "清空Fee Plan筛选" }));
  expect(within(directory).getByText("ACCOUNT-A")).toBeTruthy();
  await choose("年份", "2024年");
  expect(within(directory).getByText("所选条件下没有在管客户")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("季度"), { target: { value: "" } });
  await choose("年份", "2025年");
  expect(within(directory).getByText("ACCOUNT-B")).toBeTruthy();
});

test("整年继承及路由期间变更生效，导入仅新增区挂载并刷新客户账户，删除重复区块且导入后刷新", async () => {
  const view = setup("");
  await screen.findByText("未来客户");
  expect(screen.queryByRole("button", { name: "嵌入导入确认" })).toBeNull();
  view.rerender(<ClientsPage notify={vi.fn()} initialYear="2026" initialQuarter="2" />);
  await waitFor(() => expect(screen.queryByText("未来客户")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "新增客户（自动导入）" }));
  expect(screen.queryByRole("region", { name: "补全待确认Client" })).toBeNull();
  expect(screen.queryByRole("region", { name: "补全待确认Sub Account" })).toBeNull();
  expect(screen.queryByRole("region", { name: "档案维护" })).toBeNull();
  const clientsBefore = view.fetcher.mock.calls.filter(([path]) => path === "/api/clients").length;
  const accountsBefore = view.fetcher.mock.calls.filter(([path]) => path === "/api/accounts").length;
  fireEvent.click(screen.getByRole("button", { name: "嵌入导入确认" }));
  await waitFor(() => expect(view.fetcher.mock.calls.filter(([path]) => path === "/api/clients")).toHaveLength(clientsBefore + 1));
  expect(view.fetcher.mock.calls.filter(([path]) => path === "/api/accounts")).toHaveLength(accountsBefore + 1);
});

test("管理区间含边界日且全年范围覆盖闰年，不把当前状态替代历史期间", () => {
  expect(periodDateRange("2024", "1")).toEqual({ start: "2024-01-01", end: "2024-03-31" });
  expect(periodDateRange("2026", "")).toEqual({ start: "2026-01-01", end: "2026-12-31" });
  const account = { status: "CLOSED", start_date: "2026-01-01", end_date: "2026-04-01" } as Account;
  expect(accountManagedInPeriod(account, "2026", "2")).toBe(true);
  expect(accountManagedInPeriod({ ...account, end_date: "2026-03-31" }, "2026", "2")).toBe(false);
  expect(accountManagedInPeriod({ ...account, end_date: null }, "2026", "2")).toBe(false);
  expect(accountManagedInPeriod({ ...account, status: "DRAFT" }, "2026", "2")).toBe(false);
});


test("全部档案统一显示非期间档案及空客户，筛选、账户隐藏和删除保留", async () => {
  const { fetcher } = setup();
  await screen.findByText("客户甲");
  fireEvent.change(screen.getByLabelText("查询范围"), { target: { value: "all" } });
  const directory = screen.getByRole("region", { name: "客户与账户清单" });
  for (const name of ["未来客户", "待补全客户", "无账户客户", "客户乙"]) expect(within(directory).getByText(name)).toBeTruthy();
  expect(screen.queryByLabelText("季度")).toBeNull();
  expect(within(directory).queryByText("ACCOUNT-A")).toBeNull();
  await choose("查询客户账户", "无账户客户");
  expect(screen.getByText("尚无Sub Account")).toBeTruthy();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "删除Client 无账户客户" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/clients/7", expect.objectContaining({ method: "DELETE" })));
  fireEvent.change(screen.getByLabelText("查询范围"), { target: { value: "period" } });
  expect(screen.getByLabelText("季度")).toBeTruthy();
  expect(screen.queryByText("无账户客户")).toBeNull();
});
