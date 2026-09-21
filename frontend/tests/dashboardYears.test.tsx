import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import DashboardPage from "../src/pages/DashboardPage";

function setup() {
  const fetcher = vi.fn(async () => new Response(JSON.stringify({ managed_clients: 0, client_overview: [], accounts_with_incomplete_management_dates: 0, generated_service_fee: "0.00", paid_amount: "0.00", outstanding_amount: "0.00", overdue_invoices: 0 })));
  vi.stubGlobal("fetch", fetcher);
  render(<DashboardPage />);
  return fetcher;
}

test("年份从2000到2200连续完整排列，2026上下分别为2025和2027", async () => {
  const fetcher = setup();
  const input = screen.getByRole("combobox", { name: "统计年度" });
  fireEvent.focus(input);
  const years = within(screen.getByRole("listbox", { name: "统计年度候选" })).getAllByRole("option").map((option) => option.textContent);
  expect(years).toEqual(Array.from({ length: 201 }, (_, i) => `${2000 + i}年`));
  expect(document.getElementById(input.getAttribute("aria-activedescendant")!)?.textContent).toBe("2026年");
  fireEvent.keyDown(input, { key: "ArrowDown" });
  fireEvent.keyDown(input, { key: "Enter" });
  expect((input as HTMLInputElement).value).toBe("2027年");
  await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith("/api/dashboard?year=2027", expect.anything()));
  fireEvent.click(input);
  fireEvent.keyDown(input, { key: "ArrowUp" });
  fireEvent.keyDown(input, { key: "ArrowUp" });
  fireEvent.keyDown(input, { key: "Enter" });
  expect((input as HTMLInputElement).value).toBe("2025年");
});

test("搜索及取消保留所选统计期间，远端年份可选择且季度继续生效", async () => {
  const fetcher = setup();
  const input = screen.getByRole("combobox", { name: "统计年度" });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "2200" } });
  fireEvent.keyDown(input, { key: "Escape" });
  expect((input as HTMLInputElement).value).toBe("2026年");
  fireEvent.click(input);
  fireEvent.click(screen.getByRole("option", { name: "2200年" }));
  fireEvent.change(screen.getByLabelText("统计季度"), { target: { value: "2" } });
  await waitFor(() => expect(fetcher).toHaveBeenLastCalledWith("/api/dashboard?year=2200&quarter=2", expect.anything()));
});

test("在管数字链接保留年度季度并移除原客户总览，全年不附季度", async () => {
  setup();
  expect((await screen.findByRole("link", { name: "查看2026年全年在管客户：0位" })).getAttribute("href")).toBe("#/clients?year=2026");
  expect(screen.queryByRole("heading", { name: "客户总览" })).toBeNull();
  fireEvent.change(screen.getByLabelText("统计季度"), { target: { value: "2" } });
  expect((await screen.findByRole("link", { name: "查看2026年第2季度在管客户：0位" })).getAttribute("href")).toBe("#/clients?year=2026&quarter=2");
});
