import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import TransactionsPage from "../src/pages/TransactionsPage";

const account = { id: 1, client_id: 1, client_name: "合成客户", account_number: "SYN-001", platform_id: 1, platform_name: "合成平台", scheme_name: "合成平台", fee_plan_id: 1, fee_plan_name: "20%计划", start_date: "2026-01-01", status: "ACTIVE" };
const records: Record<string, unknown[]> = {
  "/api/clients": [{ id: 1, name: "合成客户", fc_id: 1, status: "ACTIVE" }],
  "/api/accounts": [account],
  "/api/fcs": [{ id: 1, name: "合成FC" }],
  "/api/fee-plans": [{ id: 1, name: "20%计划" }],
};

// jsdom does not populate FormData file parts after a synthetic file-input event.
function captureSyntheticFiles() {
  const BrowserFormData = FormData;
  vi.stubGlobal("FormData", class extends BrowserFormData {
    constructor(form?: HTMLFormElement) {
      super(form);
      form?.querySelectorAll<HTMLInputElement>('input[type="file"]').forEach((input) => {
        this.delete(input.name);
        Array.from(input.files || []).forEach((file) => this.append(input.name, file));
      });
    }
  });
}

async function setup(onWrite?: (path: string, options: RequestInit) => Response | Promise<Response>, secondAccount = false) {
  captureSyntheticFiles();
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => options?.method
    ? onWrite?.(path, options) ?? new Response(JSON.stringify({ id: 91 }))
    : new Response(JSON.stringify(path === "/api/accounts" && secondAccount ? [account, { ...account, id: 2, account_number: "SYN-002" }] : records[path] || [])));
  vi.stubGlobal("fetch", fetcher);
  render(<TransactionsPage notify={vi.fn()} />);
  fireEvent.focus(screen.getByRole("combobox", { name: "资金与余额客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "合成客户" }));
  return fetcher;
}

async function fillTransaction() {
  const panel = screen.getByRole("region", { name: "导入供款加款取款", exact: true });
  fireEvent.focus(within(panel).getByRole("combobox", { name: "资金记录账户" }));
  fireEvent.click(await within(screen.getByRole("listbox")).findByRole("option", { name: /SYN-001/ }));
  fireEvent.change(within(panel).getByLabelText("资金生效日期"), { target: { value: "2026-06-30" } });
  fireEvent.change(within(panel).getByLabelText("金额 (HKD)"), { target: { value: "200.00" } });
  return panel;
}

test("新流水缺凭证拒绝提交，加款备注必填且三种类型可选", async () => {
  const fetcher = await setup();
  const panel = await fillTransaction();
  fireEvent.change(within(panel).getByLabelText("资金类型"), { target: { value: "CONTRIBUTION" } });
  expect((within(panel).getByLabelText(/加款备注/) as HTMLTextAreaElement).required).toBe(true);
  expect(within(panel).getByRole("option", { name: "供款（月供）" })).toBeTruthy();
  expect(within(panel).getByRole("option", { name: "取款" })).toBeTruthy();
  fireEvent.submit(panel.querySelector("form")!);
  expect((await screen.findByRole("alert")).textContent).toContain("至少选择一份凭证");
  expect(fetcher.mock.calls.filter(([, options]) => options?.method)).toHaveLength(0);
});

test("先上传全部凭证，再自动关联本次资金记录，提交中阻止重复操作", async () => {
  let finish!: (response: Response) => void;
  const fetcher = await setup((path) => path === "/api/attachments" ? new Promise<Response>((resolve) => { finish = resolve; }) : new Response(JSON.stringify({ id: 1 })));
  const panel = await fillTransaction();
  fireEvent.change(within(panel).getByLabelText(/原始凭证（必填/), { target: { files: [new File(["synthetic"], "proof.png", { type: "image/png" })] } });
  fireEvent.submit(panel.querySelector("form")!);
  expect((within(panel).getByRole("button", { name: "保存中…" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.submit(panel.querySelector("form")!);
  expect(fetcher.mock.calls.filter(([path, options]) => path === "/api/transactions" && options?.method)).toHaveLength(0);
  finish(new Response(JSON.stringify({ id: 91 })));
  await waitFor(() => expect(fetcher.mock.calls.filter(([path, options]) => path === "/api/transactions" && options?.method)).toHaveLength(1));
  const request = fetcher.mock.calls.find(([path, options]) => path === "/api/transactions" && options?.method)!;
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({ account_id: 1, transaction_type: "MONTHLY_CONTRIBUTION", transaction_date: "2026-06-30", amount: "200.00", attachment_ids: [91] });
  expect(fetcher.mock.calls.filter(([path, options]) => path === "/api/attachments" && options?.method)).toHaveLength(1);
});

test("上传失败不创建资金记录，已填写金额保持", async () => {
  const fetcher = await setup(() => new Response(JSON.stringify({ detail: "凭证上传失败" }), { status: 500 }));
  const panel = await fillTransaction();
  fireEvent.change(within(panel).getByLabelText(/原始凭证（必填/), { target: { files: [new File(["synthetic"], "proof.png", { type: "image/png" })] } });
  fireEvent.submit(panel.querySelector("form")!);
  expect((await screen.findByRole("alert")).textContent).toContain("凭证上传失败");
  expect(fetcher.mock.calls.filter(([path, options]) => path === "/api/transactions" && options?.method)).toHaveLength(0);
  expect((within(panel).getByLabelText("金额 (HKD)") as HTMLInputElement).value).toBe("200.00");
});

test("手动结余多张图片同次关联，不提供资格复选框或关联记录选择器", async () => {
  let id = 80;
  const fetcher = await setup(() => new Response(JSON.stringify({ id: ++id })));
  fireEvent.click(screen.getByRole("button", { name: "手动填写并附图" }));
  const panel = screen.getByRole("region", { name: "导入季度结余", exact: true });
  fireEvent.focus(within(panel).getByRole("combobox", { name: "季度结余账户" }));
  fireEvent.click(await within(screen.getByRole("listbox")).findByRole("option", { name: /SYN-001/ }));
  fireEvent.change(within(panel).getByLabelText("结余金额 (HKD)"), { target: { value: "1234.56" } });
  fireEvent.change(within(panel).getByLabelText(/图片凭证/), { target: { files: [new File(["a"], "a.png"), new File(["b"], "b.jpg")] } });
  fireEvent.submit(panel.querySelector("form")!);
  await waitFor(() => expect(fetcher.mock.calls.some(([path, options]) => path === "/api/balance-snapshots" && options?.method)).toBe(true));
  const request = fetcher.mock.calls.find(([path, options]) => path === "/api/balance-snapshots" && options?.method)!;
  expect(JSON.parse(String(request[1]?.body))).toMatchObject({ account_id: 1, total_balance: "1234.56", attachment_ids: [81, 82] });
  expect(JSON.parse(String(request[1]?.body))).not.toHaveProperty("eligible_for_closing");
  expect(screen.queryByLabelText("关联记录")).toBeNull();
  expect(screen.queryByRole("checkbox")).toBeNull();
});

test("两张录入表单分别选择账户，填写结余不会改变已填资金记录的户口或金额", async () => {
  const fetcher = await setup(undefined, true);
  const cashPanel = await fillTransaction();
  fireEvent.click(screen.getByRole("button", { name: "手动填写并附图" }));
  const balancePanel = screen.getByRole("region", { name: "导入季度结余", exact: true });
  fireEvent.focus(within(balancePanel).getByRole("combobox", { name: "季度结余账户" }));
  fireEvent.click(within(screen.getByRole("listbox", { name: "季度结余账户候选" })).getByRole("option", { name: /SYN-002/ }));
  expect((within(cashPanel).getByRole("combobox", { name: "资金记录账户" }) as HTMLInputElement).value).toContain("SYN-001");
  expect((within(cashPanel).getByLabelText("金额 (HKD)") as HTMLInputElement).value).toBe("200.00");
  fireEvent.change(within(balancePanel).getByLabelText("结余金额 (HKD)"), { target: { value: "987.65" } });
  fireEvent.change(within(cashPanel).getByLabelText(/原始凭证（必填/), { target: { files: [new File(["cash"], "cash.png")] } });
  fireEvent.submit(cashPanel.querySelector("form")!);
  await waitFor(() => expect(fetcher.mock.calls.some(([path, options]) => path === "/api/transactions" && options?.method)).toBe(true));
  expect((within(balancePanel).getByRole("combobox", { name: "季度结余账户" }) as HTMLInputElement).value).toContain("SYN-002");
  expect((within(balancePanel).getByLabelText("结余金额 (HKD)") as HTMLInputElement).value).toBe("987.65");
});
