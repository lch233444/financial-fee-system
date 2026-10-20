import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { HelpTip } from "../src/Progressive";
import SetupPage from "../src/pages/SetupPage";
import SettlementsPage from "../src/pages/SettlementsPage";
import ImportsPage from "../src/pages/ImportsPage";
import DashboardPage from "../src/pages/DashboardPage";

test("帮助按需显示，鼠标离开关闭，键盘与触屏可打开及关闭", async () => {
  vi.useFakeTimers();
  try {
    render(<HelpTip title="计算规则">每个账户独立计算</HelpTip>);
    const trigger = screen.getByRole("button", { name: "计算规则：帮助" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.pointerEnter(trigger);
    expect(screen.getByRole("tooltip").textContent).toContain("每个账户独立计算");
    fireEvent.pointerLeave(trigger);
    act(() => vi.advanceTimersByTime(150));
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.focus(trigger);
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.click(trigger);
    fireEvent.pointerLeave(trigger);
    act(() => vi.advanceTimersByTime(150));
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("tooltip")).toBeNull();
  } finally { vi.useRealTimers(); }
});

test("基础设置二级表单返回后保留输入，浏览和帮助不写业务数据", async () => {
  const fetcher = vi.fn(async () => new Response("[]"));
  vi.stubGlobal("fetch", fetcher);
  render(<SetupPage notify={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "保存收款公司" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "新增收款公司", exact: true }));
  fireEvent.change(screen.getByLabelText("收款公司全名"), { target: { value: "尚未保存的公司" } });
  expect(screen.queryByRole("region", { name: "现有收款公司" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "返回列表" }));
  fireEvent.click(screen.getByRole("button", { name: "新增收款公司", exact: true }));
  expect((screen.getByLabelText("收款公司全名") as HTMLInputElement).value).toBe("尚未保存的公司");
  await waitFor(() => expect(fetcher).toHaveBeenCalled());
  expect(fetcher.mock.calls.every((args) => !((args as unknown[])[1] as RequestInit)?.method)).toBe(true);
});

test("账单计算只显示当前步骤，返回选择保留金额，换客户清空旧账户", async () => {
  const records: Record<string, unknown[]> = {
    "/api/clients": [{ id: 1, name: "演示客户", status: "ACTIVE" }, { id: 2, name: "另一客户", status: "ACTIVE" }],
    "/api/accounts": [{ id: 1, client_id: 1, platform_id: 1, fee_plan_id: 1, account_number: "DEMO", start_date: "2026-01-01", status: "ACTIVE" }],
    "/api/platforms": [{ id: 1, name: "平台" }], "/api/fee-plans": [{ id: 1, name: "20%", fee_rate_percent: 20 }],
    "/api/balance-snapshots": [{ id: 1, account_id: 1, as_of_date: "2026-01-01", total_balance: "1000.00" }],
  };
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(records[path] || [])));
  vi.stubGlobal("fetch", fetcher);
  render(<SettlementsPage notify={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "计算并保存草稿" })).toBeNull();
  expect(screen.queryByText("等待计算结果")).toBeNull();
  fireEvent.change(screen.getByLabelText("结算年份"), { target: { value: "2026" } });
  fireEvent.focus(screen.getByRole("combobox", { name: "结算客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "演示客户" }));
  fireEvent.click(screen.getByRole("button", { name: "建立此组合" }));
  fireEvent.change(screen.getByLabelText("DEMO 期初高水位线"), { target: { value: "1200" } });
  expect(screen.queryByRole("combobox", { name: "结算客户" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "返回选择" }));
  fireEvent.click(screen.getByRole("button", { name: "建立此组合" }));
  expect((screen.getByLabelText("DEMO 期初高水位线") as HTMLInputElement).value).toBe("1200");
  fireEvent.click(screen.getByRole("button", { name: "返回选择" }));
  fireEvent.focus(screen.getByRole("combobox", { name: "结算客户" }));
  fireEvent.click(screen.getByRole("option", { name: "另一客户" }));
  expect(screen.queryByLabelText("DEMO 期初高水位线")).toBeNull();
  expect(fetcher.mock.calls.every((args) => !((args as unknown[])[1] as RequestInit)?.method)).toBe(true);
});

test("导入首屏只显示上传和记录，选定后显示原件及核对，返回不触发确认", async () => {
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/statement-imports"
    ? [{ id: 1, original_name: "仅供界面测试.png", status: "NEEDS_REVIEW", extracted: {}, confidence: {}, warnings: [] }] : path.includes("assistant") ? {} : [])));
  vi.stubGlobal("fetch", fetcher);
  render(<ImportsPage notify={vi.fn()} />);
  expect(screen.queryByRole("button", { name: "打开文件" })).toBeNull();
  fireEvent.click(await screen.findByRole("button", { name: /仅供界面测试.png/ }));
  expect(screen.queryByRole("button", { name: "上传并本地识别" })).toBeNull();
  expect(screen.getByRole("button", { name: "打开文件" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "返回导入记录" }));
  expect(screen.getByRole("button", { name: "上传并本地识别" })).toBeTruthy();
  expect(fetcher.mock.calls.every((args) => !((args as unknown[])[1] as RequestInit)?.method)).toBe(true);
});

test("工作台先展示办理入口，统计在经营概览中按需查看", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path.startsWith("/api/dashboard")
    ? { managed_clients: 0, generated_service_fee: "0", paid_amount: "0", outstanding_amount: "0", overdue_invoices: 0 } : []))));
  render(<DashboardPage />);
  expect(screen.queryByRole("combobox", { name: "统计年度" })).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "经营概览" }));
  expect(screen.getByRole("combobox", { name: "统计年度" })).toBeTruthy();
  expect(within(screen.getByRole("tabpanel", { name: "经营概览" })).getByRole("heading", { name: "本期概况" })).toBeTruthy();
});
