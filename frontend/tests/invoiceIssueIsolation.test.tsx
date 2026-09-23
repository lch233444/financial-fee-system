import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import InvoicesPage from "../src/pages/InvoicesPage";
import { todayIso } from "../src/hooks";

test("切换Draft重置签发日期、到期日期和语言，仅向当前账单提交当前表单", async () => {
  const draft = {
    id: 1, invoice_number: null, client_id: 1, client_name: "合成客户A",
    company_id: 1, company_name: "合成公司A", payee_company_id: 1, fc_id: 1, fc_name: "合成FC",
    year: 2026, quarter: 1, fee_plan_id: 1, fee_plan_name: "合成计划", language: "en",
    settlement_id: 1, settlement_ids: [1], source_count: 1, account_lines: [],
    amount: "120.00", paid_amount: "0.00", outstanding_amount: "120.00", adjustment_amount: "0.00",
    lifecycle_status: "DRAFT", payment_status: "UNPAID", payments: [], allocations: [], adjustments: [],
    issue_date: null, due_date: null,
  };
  const drafts = [draft, { ...draft, id: 2, client_id: 2, client_name: "合成客户B", company_name: "合成公司B", payee_company_id: 2 }];
  const posts: Array<{ path: string; body: unknown }> = [];
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      posts.push({ path, body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify(drafts[1]));
    }
    return new Response(JSON.stringify(path === "/api/invoices" ? drafts : []));
  }));
  render(<InvoicesPage notify={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "查看Draft #1" }));
  const formA = within(screen.getByRole("region", { name: "正式出具" }));
  fireEvent.change(formA.getByLabelText("Issue Date"), { target: { value: "2026-01-15" } });
  fireEvent.change(formA.getByLabelText(/^Due Date/), { target: { value: "2026-01-30" } });
  fireEvent.change(formA.getByLabelText("Language"), { target: { value: "zh" } });

  fireEvent.click(screen.getByRole("button", { name: "查看Draft #2" }));
  const formB = within(screen.getByRole("region", { name: "正式出具" }));
  expect.soft((formB.getByLabelText("Issue Date") as HTMLInputElement).value).toBe(todayIso());
  expect.soft((formB.getByLabelText(/^Due Date/) as HTMLInputElement).value).toBe("");
  expect.soft((formB.getByLabelText("Language") as HTMLSelectElement).value).toBe("en");
  fireEvent.click(formB.getByRole("button", { name: "Issued并分配编号" }));
  await waitFor(() => expect(posts).toHaveLength(1));
  expect(posts).toEqual([{ path: "/api/invoices/2/issue", body: { issue_date: todayIso(), due_date: null, language: "en" } }]);
  await waitFor(() => expect((formB.getByRole("button", { name: "Issued并分配编号" }) as HTMLButtonElement).disabled).toBe(false));
});
