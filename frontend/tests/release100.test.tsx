import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SettlementsPage from "../src/pages/SettlementsPage";
import InvoicesPage from "../src/pages/InvoicesPage";
import GeneratedBills, { SettlementBillingStatus } from "../src/GeneratedBills";
import { hwmBasis } from "../src/hwmBasis";
import type { Invoice, Settlement, BalanceSnapshot } from "../src/types";

const snapshots = [
  { id: 1, account_id: 1, as_of_date: "2026-01-01", total_balance: "1000.00", evidence_complete: true },
  { id: 2, account_id: 1, as_of_date: "2026-03-31", total_balance: "1200.00", eligible_for_closing: true, evidence_complete: true },
] as BalanceSnapshot[];

test("首次HWM自动采用系统期初快照，修改原因和独立确认缺一不可，改值后确认失效", async () => {
  const writes: Record<string, unknown>[] = [];
  const records: Record<string, unknown[]> = {
    "/api/clients": [{ id: 1, name: "测试客户", status: "ACTIVE" }],
    "/api/accounts": [{ id: 1, client_id: 1, platform_id: 1, fee_plan_id: 1, account_number: "AUTO100", start_date: "2025-03-01", status: "ACTIVE" }],
    "/api/platforms": [{ id: 1, name: "平台" }], "/api/fee-plans": [{ id: 1, name: "计划", fee_rate_percent: 20 }],
    "/api/balance-snapshots": snapshots,
  };
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      writes.push(JSON.parse(init.body as string));
      return new Response(JSON.stringify({ id: 1, status: "DRAFT", account_lines: [], fee_rate: 0.2, version_no: 1 }));
    }
    return new Response(JSON.stringify(records[path] ?? []));
  }));
  render(<SettlementsPage notify={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("结算年份"), { target: { value: "2026" } });
  fireEvent.focus(screen.getByRole("combobox", { name: "结算客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "测试客户" }));
  fireEvent.click(screen.getByRole("button", { name: "建立此组合" }));
  const hwm = screen.getByLabelText("AUTO100 Original HWM") as HTMLInputElement;
  expect(hwm.value).toBe("1000.00");
  fireEvent.change(screen.getByLabelText("AUTO100 Closing Snapshot"), { target: { value: "2" } });
  fireEvent.change(hwm, { target: { value: "1100" } });
  fireEvent.click(screen.getByRole("button", { name: "计算并保存Draft" }));
  await screen.findByText(/修改首次自动HWM必须填写原因并单独确认/);
  expect(writes).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("AUTO100 首次HWM修改原因"), { target: { value: "复核首次计费基准" } });
  fireEvent.click(screen.getByRole("button", { name: "确认首次HWM修改" }));
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("期初结余 #1"));
  fireEvent.change(hwm, { target: { value: "1150" } });
  expect((screen.getByRole("button", { name: "确认首次HWM修改" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "计算并保存Draft" }));
  await waitFor(() => expect((screen.getByRole("button", { name: "计算并保存Draft" }) as HTMLButtonElement).disabled).toBe(false));
  expect(writes).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "确认首次HWM修改" }));
  fireEvent.click(screen.getByRole("button", { name: "计算并保存Draft" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ account_lines: [{ original_hwm: "1150", beginning_snapshot_id: 1, hwm_override_reason: "复核首次计费基准", hwm_override_confirmed: true }] });
});

test("继承上期Next HWM，不使用首次覆盖输入；快照选择有歧义时不能自动选", () => {
  const input = { startDate: "2026-04-01", beginningSnapshotId: "", originalHwm: "999", overrideReason: "旧输入", confirmedBasis: "" };
  const prior = { status: "FINALIZED", year: 2026, quarter: 1, account_lines: [{ id: 5, account_id: 1, closing_snapshot_id: 2, next_hwm: "1180.00" }] } as Settlement;
  const basis = hwmBasis(1, input, snapshots, [prior], 2026, 2);
  expect(basis.value).toBe("1180.00");
  expect(basis.modified).toBe(false);
  expect(basis.snapshot?.id).toBe(2);
  const ambiguous = hwmBasis(1, { ...input, startDate: "2026-01-01" }, [...snapshots, { ...snapshots[0], id: 3, as_of_date: "2025-12-31" }], [], 2026, 1);
  expect(ambiguous.snapshot).toBeUndefined();
});

test("计算锁定不冒充已生成账单，生成清单仅含已签发及正式作废单", () => {
  const settlement = { id: 1, status: "FINALIZED" } as Settlement;
  const draft = { id: 1, lifecycle_status: "DRAFT", settlement_ids: [1] } as Invoice;
  const issued = { ...draft, id: 2, lifecycle_status: "ISSUED", invoice_number: "202609001", client_id: 1, client_name: "客户", year: 2026, quarter: 3, amount: "20.00" } as Invoice;
  const view = render(<><SettlementBillingStatus settlement={settlement} invoices={[draft]} /><GeneratedBills invoices={[draft]} /></>);
  expect(screen.getByText("收费已计算")).toBeTruthy();
  expect(screen.getByText("暂无已生成账单")).toBeTruthy();
  view.rerender(<><SettlementBillingStatus settlement={settlement} invoices={[issued]} /><GeneratedBills invoices={[draft, issued]} /></>);
  expect(screen.getByText("202609001")).toBeTruthy();
  expect(screen.getByRole("link", { name: "查看账单及PDF" }).getAttribute("href")).toBe("#/invoices/2");
});

test("收款情况不显示建单入口，账单出具不显示付款确认表单", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]")));
  const view = render(<InvoicesPage mode="payment" notify={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "收款情况", exact: true })).toBeTruthy();
  expect(screen.queryByRole("region", { name: "建立Invoice Draft" })).toBeNull();
  view.unmount();
  render(<InvoicesPage mode="issue" notify={vi.fn()} />);
  expect(screen.getByRole("heading", { name: "账单出具", exact: true })).toBeTruthy();
  expect(screen.getByRole("region", { name: "建立Invoice Draft" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "确认已付款" })).toBeNull();
});

test("计算作废与出具失败分别显示，正式作废账单使用已保存的替代单关联", () => {
  const settlement = { id: 1, status: "VOID" } as Settlement;
  const view = render(<SettlementBillingStatus settlement={settlement} invoices={[]} />);
  expect(screen.getByText("计算已作废")).toBeTruthy();
  const bill = { id: 4, settlement_ids: [1], lifecycle_status: "DRAFT", last_issue_status: "FAILED" } as Invoice;
  view.rerender(<SettlementBillingStatus settlement={{ ...settlement, status: "FINALIZED" }} invoices={[bill]} />);
  expect(screen.getByText("出具失败")).toBeTruthy();
  view.rerender(<SettlementBillingStatus settlement={settlement} invoices={[{ ...bill, lifecycle_status: "VOID", invoice_number: "202609001", replacement_invoice_id: 8 }]} />);
  expect(screen.getByText("账单已作废")).toBeTruthy();
  expect(screen.getByRole("link", { name: "查看替代账单 #8" }).getAttribute("href")).toBe("#/invoices/8");
});
