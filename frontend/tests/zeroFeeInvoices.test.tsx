import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import InvoicesPage from "../src/pages/InvoicesPage";

const source = { id: 1, client_id: 1, client_name: "零收费测试客户", platform_id: 1, company_id: null, fc_id: 1, fc_name: "Test FC", fee_plan_id: 1, fee_plan_name: "20%", fee_rate: 0.2, year: 2026, quarter: 1, status: "FINALIZED", service_fee: "0.00", account_lines: [] };
const invoice = { id: 1, client_id: 1, client_name: source.client_name, fc_id: 1, year: 2026, quarter: 1, fee_plan_id: 1, fee_plan_name: "20%", settlement_ids: [1], source_count: 1, account_lines: [], invoice_number: "Zero Company-TF-202604001", company_name: "Zero Company", payee_company_id: 1, lifecycle_status: "ISSUED", payment_status: "NOT_REQUIRED", amount: "0.00", paid_amount: "0.00", outstanding_amount: "0.00", adjustment_amount: "0.00", payments: [], adjustments: [], is_overdue: false };

test("零收费来源可以选入并建立草稿，收款公司仍必须明确选择", async () => {
  const writes: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") { writes.push(JSON.parse(init.body as string)); return new Response(JSON.stringify({ ...invoice, lifecycle_status: "DRAFT" })); }
    return new Response(JSON.stringify(path === "/api/settlements" ? [source] : path === "/api/companies" ? [{ id: 1, name: "Zero Company" }] : []));
  }));
  render(<InvoicesPage notify={vi.fn()} />);
  const combo = screen.getByRole("combobox", { name: "客户季度账单组合" }) as HTMLInputElement;
  await waitFor(() => expect(combo.disabled).toBe(false));
  fireEvent.focus(combo);
  fireEvent.click(screen.getByRole("option", { name: /零收费测试客户.*港币 0.00/ }));
  expect((screen.getByRole("button", { name: "建立草稿" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.change(screen.getByRole("combobox", { name: /^本次账单收款公司/ }), { target: { value: "1" } });
  fireEvent.click(screen.getByRole("button", { name: "建立草稿" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ client_id: 1, year: 2026, quarter: 1, payee_company_id: 1 });
});

test("零金额出具后可筛选无需付款，查看时没有收款表单或付款凭证入口", async () => {
  const fetcher = vi.fn(async (path: string) => new Response(JSON.stringify(path === "/api/invoices" ? [invoice, { ...invoice, id: 2, invoice_number: "POSITIVE", payment_status: "UNPAID", amount: "10.00", outstanding_amount: "10.00" }] : path === "/api/settlements" ? [source] : [])));
  vi.stubGlobal("fetch", fetcher);
  render(<InvoicesPage mode="payment" notify={vi.fn()} />);
  await screen.findByRole("button", { name: `查看${invoice.invoice_number}` });
  fireEvent.click(screen.getByRole("button", { name: "无需付款", exact: true }));
  const table = within(screen.getByRole("region", { name: "付款账单清单" }));
  expect(table.queryByRole("button", { name: "查看POSITIVE" })).toBeNull();
  fireEvent.click(table.getByRole("button", { name: `查看${invoice.invoice_number}` }));
  expect(await screen.findByText(/本单无需付款，不用登记收款/)).toBeTruthy();
  expect(screen.getByRole("heading", { name: "第3步 · 查看无需付款账单" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "确认已付款" })).toBeNull();
  expect(screen.queryByRole("button", { name: "查看本单付款凭证" })).toBeNull();
  expect(screen.queryByText("已逾期", { selector: ".overdue-note" })).toBeNull();
  expect(fetcher.mock.calls.every((call) => call.length === 1 || !(call[1] as RequestInit)?.method)).toBe(true);
});
