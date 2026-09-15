import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ClientsPage from "../src/pages/ClientsPage";
import InvoicesPage from "../src/pages/InvoicesPage";

test("客户不再选公司，账户计划包含原属其他公司的档案", async () => {
  const records: Record<string, unknown[]> = {
    "/api/clients": [{ id: 1, name: "客户", company_id: 1, status: "ACTIVE" }],
    "/api/fcs": [{ id: 1, name: "独立FC", code: "FC", company_id: null }],
    "/api/fee-plans": [{ id: 1, name: "旧计划", company_id: 1 }, { id: 2, name: "另一计划", company_id: 2 }],
  };
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? []))));
  render(<ClientsPage notify={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "新增与确认", exact: true }));
  const create = screen.getByRole("region", { name: "新增Client" });
  expect(within(create).queryByRole("combobox", { name: "Company" })).toBeNull();
  await within(create).findByRole("option", { name: /独立FC/ });
  fireEvent.focus(screen.getByRole("combobox", { name: "新增账户客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "客户", exact: true }));
  const form = screen.getByRole("region", { name: "新增Sub Account" });
  expect(within(form).getByRole("option", { name: /另一计划/ })).toBeTruthy();
});

test("切换账单客户后重新选择收款公司，不沿用上一位客户的选择", async () => {
  const settlements = [1, 2].map((id) => ({ id, client_id: id, client_name: `客户${id}`,
    company_id: null, fc_id: 1, year: 2026, quarter: 1, fee_plan_id: 1, fee_plan_name: "20%",
    status: "FINALIZED", service_fee: "20.00", account_lines: [] }));
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(
    path === "/api/settlements" ? settlements : path === "/api/companies" ? [{ id: 1, name: "收款甲" }, { id: 2, name: "收款乙" }] : []))));
  render(<InvoicesPage notify={vi.fn()} />);
  const candidate = screen.getByRole("combobox", { name: "客户季度Invoice组合" });
  await waitFor(() => expect((candidate as HTMLInputElement).disabled).toBe(false));
  fireEvent.focus(candidate);
  fireEvent.click(screen.getByRole("option", { name: /客户1/ }));
  const company = screen.getByRole("combobox", { name: /^本次账单收款公司/ }) as HTMLSelectElement;
  fireEvent.change(company, { target: { value: "2" } });
  expect(company.value).toBe("2");
  fireEvent.focus(candidate);
  fireEvent.change(candidate, { target: { value: "客户2" } });
  fireEvent.click(screen.getByRole("option", { name: /客户2/ }));
  expect(company.value).toBe("");
  expect((screen.getByRole("button", { name: "建立Draft" }) as HTMLButtonElement).disabled).toBe(true);
});
