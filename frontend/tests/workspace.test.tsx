import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ClientsPage from "../src/pages/ClientsPage";
import SettlementsPage from "../src/pages/SettlementsPage";
import InvoicesPage from "../src/pages/InvoicesPage";
import PageBoundary from "../src/PageBoundary";
import { ErrorBanner, Panel, SectionNav } from "../src/components";

test("客户分页及状态筛选重置页码，保留新增表单与已选账户客户", async () => {
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/clients"
    ? Array.from({ length: 24 }, (_, i) => ({ id: i + 1, name: `客户${String(i + 1).padStart(2, "0")}`, status: i === 23 ? "DRAFT" : "ACTIVE" })) : []))));
  render(<ClientsPage notify={vi.fn()} />);
  const directory = screen.getByRole("region", { name: "客户与账户清单" });
  await within(directory).findByText("客户01");
  expect(within(directory).queryByText("客户11")).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Client Name", exact: true }), { target: { value: "未保存的客户" } });
  fireEvent.focus(screen.getByRole("combobox", { name: "新增账户客户" }));
  fireEvent.click(screen.getByRole("option", { name: "客户02" }));
  fireEvent.click(within(directory).getByRole("button", { name: "下一页" }));
  expect(within(directory).getByText("客户11")).toBeTruthy();
  fireEvent.change(screen.getByRole("combobox", { name: "客户状态" }), { target: { value: "DRAFT" } });
  expect(within(directory).getByText("客户24")).toBeTruthy();
  expect(within(directory).queryByRole("button", { name: "下一页" })).toBeNull();
  expect((screen.getByRole("textbox", { name: "Client Name", exact: true }) as HTMLInputElement).value).toBe("未保存的客户");
  expect((screen.getByRole("combobox", { name: "新增账户客户" }) as HTMLInputElement).value).toBe("客户02");
});

test("结算选齐客户、平台和计划后才显示相应账户及可辨认的输入标签", async () => {
  const records: Record<string, unknown[]> = {
    "/api/clients": [{ id: 1, name: "测试客户", status: "ACTIVE" }],
    "/api/platforms": [{ id: 1, name: "测试平台" }],
    "/api/fee-plans": [{ id: 1, name: "测试计划" }],
    "/api/accounts": [{ id: 1, client_id: 1, platform_id: 1, fee_plan_id: 1, account_number: "QA-001", start_date: "2026-01-01", status: "ACTIVE" }],
  };
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] || []))));
  render(<SettlementsPage notify={vi.fn()} />);
  await waitFor(() => expect((screen.getByRole("combobox", { name: "结算客户" }) as HTMLInputElement).disabled).toBe(false));
  expect(screen.queryByRole("checkbox", { name: /QA-001/ })).toBeNull();
  fireEvent.focus(screen.getByRole("combobox", { name: "结算客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "测试客户" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Platform", exact: true }), { target: { value: "1" } });
  expect(screen.queryByRole("checkbox", { name: /QA-001/ })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Fee Plan", exact: true }), { target: { value: "1" } });
  expect(screen.getByRole("checkbox", { name: /QA-001/ })).toBeTruthy();
  expect(screen.getByLabelText("QA-001 Original HWM")).toBeTruthy();
});

test("建立Invoice草稿的重复点击只产生一个请求", async () => {
  let finish!: (value: Response) => void;
  const writes: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") { writes.push(JSON.parse(init.body as string)); return new Promise<Response>((resolve) => { finish = resolve; }); }
    return new Response(JSON.stringify(path === "/api/settlements" ? [{ id: 1, client_id: 1, client_name: "测试客户", company_id: 1, fc_id: 1, fee_plan_id: 1, fee_plan_name: "20%", year: 2026, quarter: 1, status: "FINALIZED", service_fee: "120.00", account_lines: [] }] : []));
  }));
  render(<InvoicesPage notify={vi.fn()} />);
  const combo = screen.getByRole("combobox", { name: "客户季度Invoice组合" });
  await waitFor(() => expect((combo as HTMLInputElement).disabled).toBe(false));
  fireEvent.focus(combo);
  fireEvent.click(screen.getByRole("option", { name: /测试客户/ }));
  const form = screen.getByRole("button", { name: "建立Draft" }).closest("form")!;
  fireEvent.submit(form); fireEvent.submit(form);
  expect(writes).toHaveLength(1);
  expect((screen.getByRole("button", { name: "正在建立…" }) as HTMLButtonElement).disabled).toBe(true);
  finish(new Response(JSON.stringify({ id: 1 })));
  await screen.findByRole("button", { name: "建立Draft" });
});

test("页面异常提供明确恢复入口，避免整个工作台空白", () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  function Broken(): never { throw new Error("Synthetic render failure"); }
  render(<PageBoundary><Broken /></PageBoundary>);
  expect(screen.getByRole("alert").textContent).toContain("页面暂时无法显示");
  expect(screen.getByRole("button", { name: "刷新页面" })).toBeTruthy();
});

test("页内导航只定位目标区域，不改变当前功能地址或销毁输入", () => {
  const scroll = vi.fn();
  const initial = window.location.hash;
  render(<><SectionNav items={[{ id: "proof", label: "上传凭证" }]} /><Panel id="proof" title="凭证"><input aria-label="备注" defaultValue="保留输入" /></Panel></>);
  const panel = screen.getByRole("region", { name: "凭证" });
  panel.scrollIntoView = scroll;
  fireEvent.click(screen.getByRole("button", { name: "上传凭证" }));
  expect(scroll).toHaveBeenCalledOnce();
  expect(document.activeElement).toBe(panel);
  expect(window.location.hash).toBe(initial);
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("保留输入");
});

test("服务端错误以完整原因展示并聚焦，后续不同错误也能获得焦点", () => {
  const view = render(<ErrorBanner message="凭证未补齐，不能确认结算" />);
  const error = screen.getByRole("alert");
  expect(error.textContent).toContain("凭证未补齐，不能确认结算");
  expect(document.activeElement).toBe(error);
  view.rerender(<ErrorBanner message="已保存，但列表刷新失败；不要重复提交" />);
  expect(document.activeElement).toBe(error);
  expect(error.textContent).toContain("不要重复提交");
});
