import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ImportsPage from "../src/pages/ImportsPage";

function setup({ success = false, clientsFail = false } = {}) {
  const extracted = { document_type: "empf_account_page", client_name: "SYNTHETIC CLIENT",
    account_number: "NEW-ACCOUNT", scheme_name: "SYNTHETIC SCHEME", as_of_date: "2026-06-30",
    total_balance: "1000.00", holdings: [] };
  const records = [1, 2].map((id) => ({ id, original_name: `客户归属测试${id}.png`, mime_type: "image/png",
    status: "NEEDS_REVIEW", extracted, confidence: {}, warnings: [] }));
  const clients = [11, 12].map((id) => ({ id, name: "SYNTHETIC CLIENT", status: "ACTIVE",
    company_name: `示例公司${id}`, fc_name: "示例FC" }));
  const accounts = [11, 12].map((id) => ({ id: id + 10, client_id: id, client_name: "SYNTHETIC CLIENT",
    account_number: `EXISTING-${id}`, platform_id: 1, platform_name: "示例平台", status: "ACTIVE" }));
  const submitted: Record<string, unknown>[] = [];
  let clientLoads = 0;
  const notify = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/confirm")) {
      submitted.push(JSON.parse(options?.body as string));
      return success ? new Response(JSON.stringify({ statement_import: { ...records[0], status: "CONFIRMED" },
        account_id: 99, client_id: 11, created_client: false, created_draft: true,
        snapshot: { id: 99, as_of_date: "2026-06-30", total_balance: "1000.00" } }))
        : new Response(JSON.stringify({ detail: "测试停在提交边界" }), { status: 409 });
    }
    if (url === "/api/clients") {
      clientLoads += 1;
      if (clientsFail) return new Response(JSON.stringify({ detail: "客户清单暂不可用" }), { status: 503 });
    }
    return new Response(JSON.stringify(url === "/api/statement-imports" ? records
      : url === "/api/clients" ? clients : url === "/api/accounts" ? accounts
      : url === "/api/ai-assistant/status" ? { status: "unavailable" } : []));
  }));
  render(<ImportsPage notify={notify} />);
  return { submitted, notify, clientLoads: () => clientLoads };
}

async function openFirst() {
  fireEvent.click(await screen.findByRole("button", { name: /客户归属测试1/ }));
  return screen.getByRole("button", { name: /生成余额快照/ }) as HTMLButtonElement;
}

function chooseCustomer(id: number) {
  const input = screen.getByRole("combobox", { name: "选择已有客户" });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: `示例公司${id}` } });
  fireEvent.click(screen.getByRole("option", { name: new RegExp(`客户#${id}`) }));
}

test("新账户遇同名档案必须明确客户归属，提交客户ID并刷新清单", async () => {
  const { submitted, notify, clientLoads } = setup({ success: true });
  const save = await openFirst();
  expect(save.disabled).toBe(true);
  expect(screen.getByText(/发现2个同名客户档案/)).toBeTruthy();
  fireEvent.submit(save.closest("form")!);
  expect(submitted).toHaveLength(0);
  chooseCustomer(11);
  expect(save.disabled).toBe(false);
  fireEvent.click(save);
  await waitFor(() => expect(notify).toHaveBeenCalledWith("已为已有客户新增子账户草稿，余额快照已入账"));
  expect(submitted[0]).toMatchObject({ client_id: 11, account_id: null, account_number: "NEW-ACCOUNT" });
  expect(clientLoads()).toBe(2);
});

test("切换原件或修改客户姓名清空旧客户选择", async () => {
  setup();
  await openFirst();
  chooseCustomer(11);
  fireEvent.click(screen.getByRole("button", { name: /客户归属测试2/ }));
  expect((screen.getByRole("combobox", { name: "选择已有客户" }) as HTMLInputElement).value).toBe("");
  chooseCustomer(12);
  fireEvent.change(screen.getByRole("textbox", { name: /^Client Name/ }), { target: { value: "ANOTHER PERSON" } });
  expect((screen.getByRole("combobox", { name: "选择已有客户" }) as HTMLInputElement).value).toBe("");
});

test("选择已有账户同步所属客户，切换客户清空账户并只显示其账户", async () => {
  setup();
  await openFirst();
  const input = screen.getByRole("combobox", { name: "匹配已有账户" }) as HTMLInputElement;
  fireEvent.focus(input);
  fireEvent.click(screen.getByRole("option", { name: /EXISTING-11/ }));
  expect((screen.getByRole("combobox", { name: "选择已有客户" }) as HTMLInputElement).value).toContain("客户#11");
  chooseCustomer(12);
  expect(input.value).toBe("");
  fireEvent.focus(input);
  expect(screen.queryByRole("option", { name: /EXISTING-11/ })).toBeNull();
  expect(screen.getByRole("option", { name: /EXISTING-12/ })).toBeTruthy();
  fireEvent.click(screen.getByRole("option", { name: /EXISTING-12/ }));
  fireEvent.change(screen.getByRole("textbox", { name: /^Account Number/ }), { target: { value: "CHANGED" } });
  expect(input.value).toBe("");
});

test("客户清单加载失败时提示错误并阻止确认", async () => {
  const { submitted } = setup({ clientsFail: true });
  const save = await openFirst();
  await screen.findByText("客户清单暂不可用");
  expect(save.disabled).toBe(true);
  fireEvent.click(save);
  expect(submitted).toHaveLength(0);
  fireEvent.submit(save.closest("form")!);
  expect(submitted).toHaveLength(0);
});
