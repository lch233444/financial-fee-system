import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import InvoicesPage from "../src/pages/InvoicesPage";

function scenario(paid = false, open = false) {
  const invoice = {
    id: 1, invoice_number: "原公司-FC-20260908-1", client_id: 1, client_name: "陳大文",
    company_id: 1, company_name: "原公司", payee_company_id: 1, fc_id: 1, fc_name: "原FC",
    year: 2026, quarter: 1, fee_plan_id: 1, fee_plan_name: "20%", language: "zh",
    settlement_id: 1, settlement_ids: [1], source_count: 1, account_lines: [],
    amount: "120.00", paid_amount: paid ? "120.00" : "0.00", outstanding_amount: paid ? "0.00" : "120.00",
    company_difference: "0.00", lifecycle_status: open ? "VOID" : "ISSUED",
    payment_status: paid ? "PAID" : "UNPAID", can_correct_company: !paid && !open,
    payments: [], allocations: [], adjustments: [], issue_date: "2026-09-08", due_date: "2026-09-22",
  };
  const replacement = { ...invoice, id: 2, invoice_number: "新公司-FC-20260908-1", company_name: "新公司",
    payee_company_id: 2, lifecycle_status: "ISSUED", can_correct_company: true };
  const correction = {
    id: 1, status: "OPEN", target_company_id: 2, target_company_name: "新公司", original_company_name: "原公司",
    reason: "更正本张账单收款公司", original_invoice: { ...invoice, lifecycle_status: "VOID" },
    replacement_invoice: null, opened_at: "2026-09-08T01:00:00Z", payments: [], allocations: [], refunds: [], adjustments: [],
  };
  const records: Record<string, unknown[]> = {
    "/api/invoices": open ? [invoice, replacement] : [invoice],
    "/api/invoice-corrections": open ? [correction] : [],
    "/api/companies": [{ id: 1, name: "原公司" }, { id: 2, name: "新公司" }],
    "/api/settlements": [{ id: 1, client_id: 1, client_name: "陳大文", company_id: 1, company_name: "原公司",
      fc_id: 1, fc_name: "原FC", year: 2026, quarter: 1, fee_plan_id: 1, fee_plan_name: "20%",
      platform_id: 1, platform_name: "平台", status: "FINALIZED", service_fee: "120.00", account_lines: [] }],
  };
  const posts: Array<{ path: string; body: Record<string, unknown> }> = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(init.body as string) });
      if (path === "/api/invoices/1/corrections") {
        Object.assign(invoice, { lifecycle_status: "VOID", can_correct_company: false });
        records["/api/invoice-corrections"] = [correction];
      }
      if (path.endsWith("/complete")) Object.assign(correction, { status: "COMPLETED", replacement_invoice: replacement });
      return new Response(JSON.stringify(correction), { status: 201 });
    }
    return new Response(JSON.stringify(records[path] ?? []));
  }));
  return { posts, records };
}

test("未收款Invoice明确选择另一家公司后提交，刷新后保留原来源可建立替代单", async () => {
  const db = scenario();
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看原公司-FC-20260908-1" }));
  fireEvent.click(screen.getByRole("button", { name: "更正收款公司", exact: true }));
  const select = screen.getByRole("combobox", { name: "新的收款公司" });
  expect(within(select).queryByRole("option", { name: "原公司" })).toBeNull();
  fireEvent.change(select, { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("收款公司更正原因"), { target: { value: "更正本张账单收款公司" } });
  fireEvent.click(screen.getByRole("button", { name: "确认更正收款公司" }));
  await screen.findByRole("button", { name: "建立或查看替代账单" });
  expect(db.posts).toEqual([{ path: "/api/invoices/1/corrections", body: { target_company_id: 2, reason: "更正本张账单收款公司" } }]);
  expect(screen.queryByText(/请先在Settlement页按版本链作废/)).toBeNull();
  fireEvent.focus(screen.getByRole("combobox", { name: "客户季度Invoice组合" }));
  expect(screen.getByRole("option", { name: /陳大文.*120.00/ })).toBeTruthy();
});

test("已收款Invoice不允许打开更正收款公司表单", async () => {
  const db = scenario(true);
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看原公司-FC-20260908-1" }));
  const button = screen.getByRole("button", { name: "更正收款公司", exact: true });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(button);
  expect(screen.queryByLabelText("新的收款公司")).toBeNull();
  expect(db.posts).toEqual([]);
});

test("原Settlement保持不变的公司替代单可以完成关联，完成前禁止收款", async () => {
  const db = scenario(false, true);
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看新公司-FC-20260908-1" }));
  expect(screen.queryByRole("button", { name: "确认已付款" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看原公司-FC-20260908-1" }));
  fireEvent.change(screen.getByRole("combobox", { name: "替代Invoice" }), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "完成更正并锁定" }));
  await waitFor(() => expect(db.posts).toHaveLength(1));
  expect(db.posts[0]).toEqual({ path: "/api/invoice-corrections/1/complete", body: {
    replacement_invoice_id: 2, retained_allocations: [], refunds: [], company_difference: "0.00", difference_reason: null,
  } });
});
