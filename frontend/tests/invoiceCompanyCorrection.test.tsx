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
    recalculate_settlements: false, revision_no: 1, can_amend: !paid, replacement_invoice: null, opened_at: "2026-09-08T01:00:00Z", payments: [], allocations: [], refunds: [], adjustments: [],
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
    if (init?.method === "POST" || init?.method === "PATCH") {
      posts.push({ path, body: JSON.parse(init.body as string) });
      if (path === "/api/invoice-corrections/1" && init?.method === "PATCH") Object.assign(correction, JSON.parse(init.body as string), { revision_no: 2 });
      if (path === "/api/invoices/1/corrections") {
        Object.assign(correction, JSON.parse(init.body as string));
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
  fireEvent.click(screen.getByRole("button", { name: "更正账单", exact: true }));
  const select = screen.getByRole("combobox", { name: /^收款公司/ });
  expect(within(select).getByRole("option", { name: "原公司（原收款公司）" })).toBeTruthy();
  fireEvent.change(select, { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("更正原因"), { target: { value: "更正本张账单收款公司" } });
  fireEvent.click(screen.getByRole("button", { name: "确认发起更正" }));
  await screen.findByRole("button", { name: "建立或查看替代账单" });
  expect(db.posts).toEqual([{ path: "/api/invoices/1/corrections", body: { target_company_id: 2, recalculate_settlements: false, reason: "更正本张账单收款公司" } }]);
  expect(screen.queryByText(/请先在Settlement页按版本链作废/)).toBeNull();
  fireEvent.focus(screen.getByRole("combobox", { name: "客户季度Invoice组合" }));
  expect(screen.getByRole("option", { name: /陳大文.*120.00/ })).toBeTruthy();
});

test("已收款Invoice统一更正表单锁定收款公司", async () => {
  const db = scenario(true);
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看原公司-FC-20260908-1" }));
  const button = screen.getByRole("button", { name: "更正账单", exact: true });
  fireEvent.click(button);
  expect((screen.getByRole("combobox", { name: /^收款公司/ }) as HTMLSelectElement).disabled).toBe(true);
  expect((screen.getByRole("checkbox", { name: "需要重新核算费用／结算来源" }) as HTMLInputElement).checked).toBe(true);
  expect(db.posts).toEqual([]);
});

test("原Settlement保持不变的公司替代单可以完成关联，完成前禁止收款", async () => {
  const db = scenario(false, true);
  render(<InvoicesPage mode="payment" notify={vi.fn()} />);
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


test("同一次更正可选择新公司并重新核算", async () => {
  const db = scenario();
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看原公司-FC-20260908-1" }));
  fireEvent.click(screen.getByRole("button", { name: "更正账单", exact: true }));
  expect(screen.queryByRole("button", { name: "更正收款公司", exact: true })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: /^收款公司/ }), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "需要重新核算费用／结算来源" }));
  fireEvent.change(screen.getByLabelText("更正原因"), { target: { value: "同时更换公司和重新核算" } });
  fireEvent.click(screen.getByRole("button", { name: "确认发起更正" }));
  await waitFor(() => expect(db.posts).toHaveLength(1));
  expect(db.posts[0].body).toEqual({ target_company_id: 2, recalculate_settlements: true, reason: "同时更换公司和重新核算" });
  await screen.findByText("需要按版本链重新核算费用／结算来源。", { exact: false });
  expect(screen.queryByRole("option", { name: /陳大文.*120.00/ })).toBeNull();
});

test("处理中普通更正可在同一表单改公司并携带原版本号保存", async () => {
  const db = scenario(false, true);
  db.records["/api/invoices"] = db.records["/api/invoices"].slice(0, 1);
  Object.assign(db.records["/api/invoice-corrections"][0] as object, { target_company_id: null, target_company_name: null, recalculate_settlements: true });
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看原公司-FC-20260908-1" }));
  fireEvent.click(screen.getByRole("button", { name: "调整本次更正" }));
  const recalc = screen.getByRole("checkbox", { name: "需要重新核算费用／结算来源" });
  expect((recalc as HTMLInputElement).checked).toBe(true);
  fireEvent.change(screen.getByRole("combobox", { name: /^收款公司/ }), { target: { value: "2" } });
  fireEvent.click(recalc);
  fireEvent.change(screen.getByLabelText("调整原因"), { target: { value: "核对后只需要换公司" } });
  fireEvent.click(screen.getByRole("button", { name: "确认调整更正" }));
  await waitFor(() => expect(db.posts).toHaveLength(1));
  expect(db.posts[0]).toEqual({ path: "/api/invoice-corrections/1", body: {
    target_company_id: 2, recalculate_settlements: false, reason: "核对后只需要换公司", expected_revision: 1,
  } });
});

test("未选择实际更正内容时禁止提交，取消不写入", async () => {
  const db = scenario();
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看原公司-FC-20260908-1" }));
  fireEvent.click(screen.getByRole("button", { name: "更正账单", exact: true }));
  fireEvent.change(screen.getByLabelText("更正原因"), { target: { value: "尚未选择更正内容" } });
  expect((screen.getByRole("button", { name: "确认发起更正" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "取消", exact: true }));
  expect(screen.queryByLabelText("更正原因")).toBeNull();
  expect(db.posts).toHaveLength(0);
});
