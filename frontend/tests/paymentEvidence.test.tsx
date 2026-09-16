import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import InvoicesPage from "../src/pages/InvoicesPage";

const base = {
  id: 1, invoice_number: "MIXED-001", client_id: 1, client_name: "客户甲", fc_id: 1, fc_name: "同名FC",
  year: 2026, quarter: 1, payee_company_id: 2, company_id: 1, company_name: "实际收款公司乙",
  fee_plan_id: 10, fee_plan_ids: [10, 30], fee_plan_name: "计划20 / 计划30", settlement_id: 1, settlement_ids: [1, 2], source_count: 2,
  account_lines: [{ id: 1, settlement_id: 1, account_number: "ACCOUNT-SECRET", service_fee: "100.00", platform_name: "平台", start_date: "2026-01-01", closing_date: "2026-03-31" }],
  lifecycle_status: "ISSUED", payment_status: "PAID", amount: "300.00", paid_amount: "280.00", adjustment_amount: "20.00", outstanding_amount: "0.00", is_overdue: false,
  adjustments: [{ id: 1, amount: "20.00", reason: "公司承担计算差额" }],
  payments: [{ id: 8, payment_date: "2026-04-12", method: "BANK_TRANSFER", amount: "280.00", original_amount: "320.00", original_invoice_id: 9, proof_attachment_id: 77 }],
};
function setup({ fileError = false, unpaid = false } = {}) {
  const paid = { ...base, ...(unpaid ? { payment_status: "UNPAID", paid_amount: "0.00", adjustment_amount: "0.00", outstanding_amount: "300.00", payments: [], adjustments: [] } : {}) };
  const invoices = [paid,
    { ...base, id: 2, invoice_number: "ONLY30", fee_plan_id: 30, fee_plan_ids: [30] },
    { ...base, id: 3, invoice_number: "OTHER-FC", fc_id: 2 },
    { ...base, id: 4, invoice_number: "OTHER-CLIENT", client_id: 2, client_name: "客户乙" },
    { ...base, id: 5, invoice_number: "OTHER-YEAR", year: 2025 },
    { ...base, id: 6, invoice_number: "OTHER-QUARTER", quarter: 2 },
    { ...base, id: 7, invoice_number: "OTHER-COMPANY", payee_company_id: 1, company_name: "公司甲" },
    { ...base, id: 10, invoice_number: "UNPAID-001", payment_status: "UNPAID", payments: [], paid_amount: "0.00", outstanding_amount: "300.00" },
    { ...base, id: 11, invoice_number: "VOID-001", lifecycle_status: "VOID" },
    { ...base, id: 12, invoice_number: null, lifecycle_status: "DRAFT" },
  ];
  const correction = { recalculate_settlements: true, revision_no: 1, can_amend: false, id: 1, status: "COMPLETED", reason: "更正收费金额", original_invoice: { id: 9, invoice_number: "ORIGINAL-009", lifecycle_status: "VOID", amount: "320.00" }, replacement_invoice: { id: 1, invoice_number: "MIXED-001", lifecycle_status: "ISSUED", amount: "300.00" }, opened_at: "2026-04-13", payments: [], adjustments: [],
    allocations: [{ id: 1, entry_type: "REVERSAL", payment_id: 8, invoice_id: 9, amount: "320.00" }, { id: 2, entry_type: "APPLY", payment_id: 8, invoice_id: 1, amount: "280.00" }],
    refunds: [{ id: 1, payment_id: 8, refund_date: "2026-04-13", amount: "40.00", reason: "退回多收款", proof_attachment_id: 78 }] };
  const writes: { path: string; body: unknown }[] = [];
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value: function(this: HTMLDialogElement) { this.setAttribute("open", ""); this.querySelector<HTMLElement>("[autofocus], button")?.focus(); } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value: function(this: HTMLDialogElement) { this.removeAttribute("open"); } });
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (path.includes("/attachments/") && path.endsWith("/file")) return fileError
      ? new Response(JSON.stringify({ detail: "凭证原文件不存在" }), { status: 404 })
      : new Response("synthetic", { headers: { "Content-Type": path.includes("78") ? "application/pdf" : "image/png" } });
    if (init?.method === "POST") {
      writes.push({ path, body: init.body instanceof FormData ? Object.fromEntries(init.body) : JSON.parse(init.body as string) });
      if (path === "/api/attachments") return new Response(JSON.stringify({ id: 77 }));
      if (path === "/api/invoices/1/payments") Object.assign(paid, base);
      return new Response(JSON.stringify(paid));
    }
    return new Response(JSON.stringify(path === "/api/invoices" ? invoices : path === "/api/invoice-corrections" ? (unpaid ? [] : [correction])
      : path === "/api/companies" ? [{ id: 1, name: "公司甲" }, { id: 2, name: "实际收款公司乙" }]
      : path === "/api/fee-plans" ? [{ id: 10, name: "计划20", code: "P20" }, { id: 30, name: "计划30", code: "P30" }] : []));
  }));
  const createUrl = vi.fn(() => "blob:synthetic");
  const revokeUrl = vi.fn();
  vi.stubGlobal("URL", class extends URL { static createObjectURL = createUrl; static revokeObjectURL = revokeUrl; });
  render(<InvoicesPage mode="payment" notify={vi.fn()} />);
  return { writes, revokeUrl };
}
async function choose(label: string, name: string) {
  fireEvent.focus(screen.getByRole("combobox", { name: label }));
  fireEvent.click(await screen.findByRole("option", { name }));
}

test("账单按六项条件联合筛选，混合计划按任一来源匹配且保留整单金额", async () => {
  setup();
  await screen.findByRole("button", { name: "查看MIXED-001" });
  fireEvent.change(screen.getByLabelText("账单年度"), { target: { value: "2026" } });
  fireEvent.change(screen.getByLabelText("账单季度"), { target: { value: "1" } });
  await choose("账单客户", "客户甲 · 客户#1");
  await choose("账单收费计划", "计划20 · P20 · #10");
  await choose("账单收款公司", "实际收款公司乙 · #2");
  await choose("账单FC", "同名FC · #1");
  fireEvent.click(screen.getByRole("button", { name: "已付款", exact: true }));
  const table = within(screen.getByRole("region", { name: "付款账单清单" }));
  expect(table.getAllByRole("row")).toHaveLength(2);
  expect(table.getByText("HKD 300.00")).toBeTruthy();
  expect(table.getByText("计划20 / 计划30")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "未付款", exact: true }));
  expect(table.getByRole("button", { name: "查看UNPAID-001" })).toBeTruthy();
  expect(table.queryByRole("button", { name: "查看VOID-001" })).toBeNull();
});

test("已付款按钮只打开凭证弹窗，保留原收款、转配、退款及差额；关闭恢复焦点并释放预览", async () => {
  const { writes, revokeUrl } = setup();
  const trigger = await screen.findByRole("button", { name: "已付款，查看凭证 MIXED-001" });
  await waitFor(() => expect((trigger as HTMLButtonElement).disabled).toBe(false));
  trigger.focus(); fireEvent.click(trigger);
  const modal = within(screen.getByRole("dialog", { name: "已付款 · 付款凭证" }));
  expect(await modal.findByRole("img", { name: "付款凭证 #77" })).toBeTruthy();
  expect(modal.getByText("公司承担计算差额", { exact: false })).toBeTruthy();
  expect(modal.getByText(/原账单 #9/)).toBeTruthy();
  fireEvent.click(modal.getByText(/更正 #1/));
  fireEvent.click(modal.getByRole("button", { name: "查看退款凭证 #1" }));
  await waitFor(() => expect(modal.getByLabelText("付款凭证 PDF #78")).toBeTruthy());
  expect(writes).toHaveLength(0);
  fireEvent(screen.getByRole("dialog"), new Event("cancel", { bubbles: false, cancelable: true }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
  expect(revokeUrl).toHaveBeenCalledTimes(2);
});

test("凭证原件缺失显示错误，不伪装为预览成功", async () => {
  setup({ fileError: true });
  const button = await screen.findByRole("button", { name: "已付款，查看凭证 MIXED-001" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false)); fireEvent.click(button);
  expect(await screen.findByText("凭证原文件不存在")).toBeTruthy();
  expect(screen.queryByRole("img", { name: "付款凭证 #77" })).toBeNull();
});

test("未付款按钮进入确认流程，缺凭证拒绝提交，有凭证才先上传再关联付款", async () => {
  const { writes } = setup({ unpaid: true });
  fireEvent.click(await screen.findByRole("button", { name: "未付款，登记付款 MIXED-001" }));
  const save = await screen.findByRole("button", { name: "确认已付款" });
  const form = save.closest("form")!;
  fireEvent.change(within(form).getByLabelText("实际现金 (HKD)"), { target: { value: "300.00" } });
  fireEvent.submit(form);
  await screen.findByText("必须选择付款凭证文件");
  expect(writes).toHaveLength(0);
  const currentForm = screen.getByRole("button", { name: "确认已付款" }).closest("form")!;
  expect((within(currentForm).getByLabelText("实际现金 (HKD)") as HTMLInputElement).value).toBe("300.00");
  fireEvent.change(currentForm.querySelector('[name="proof"]')!, { target: { files: [new File(["proof"], "proof.png", { type: "image/png" })] } });
  const originalGet = FormData.prototype.get;
  // jsdom does not populate FileList into FormData from fireEvent's synthetic files.
  vi.spyOn(FormData.prototype, "get").mockImplementation(function(this: FormData, name: string) {
    return name === "proof" ? new File(["proof"], "proof.png", { type: "image/png" }) : originalGet.call(this, name);
  });
  fireEvent.submit(currentForm);
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[0]).toMatchObject({ path: "/api/attachments", body: { entity_type: "PAYMENT" } });
  expect(writes[1]).toMatchObject({ path: "/api/invoices/1/payments", body: { amount: "300.00", proof_attachment_id: 77 } });
});

test("选择账单并不显示账户，必须搜索选定对应客户；清空后立即隐藏", async () => {
  setup();
  fireEvent.click(await screen.findByRole("button", { name: "查看MIXED-001" }));
  expect(screen.queryByText("ACCOUNT-SECRET")).toBeNull();
  const input = screen.getByRole("combobox", { name: "查看账单账户的客户" });
  fireEvent.focus(input); fireEvent.change(input, { target: { value: "客户甲" } });
  expect(screen.queryByText("ACCOUNT-SECRET")).toBeNull();
  fireEvent.click(screen.getByRole("option", { name: "客户甲 · 客户#1" }));
  expect(screen.getByText("ACCOUNT-SECRET")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "清空查看账单账户的客户" }));
  expect(screen.queryByText("ACCOUNT-SECRET")).toBeNull();
});
