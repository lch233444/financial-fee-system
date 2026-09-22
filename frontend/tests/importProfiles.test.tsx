import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ImportsPage from "../src/pages/ImportsPage";
import { completeProfile, profileRecords } from "./importProfileFixtures";

function setup({ legacy = false, profilesFail = false, pending = false } = {}) {
  const extracted = { document_type: "empf_account_page", client_name: "新客户", account_number: "NEW-1",
    scheme_name: "示例平台", as_of_date: "2026-06-30", total_balance: "1000.00" };
  const record = { id: 1, original_name: "资料确认.png", mime_type: "image/png", confidence: {}, warnings: [],
    status: legacy ? "CONFIRMED" : "NEEDS_REVIEW", extracted, confirmed_account_id: legacy ? 7 : null,
    confirmed_snapshot_id: legacy ? 9 : null };
  const owner = { id: 3, name: "新客户", status: "DRAFT" };
  const account = { id: 7, client_id: 3, account_number: "NEW-1", platform_id: 1, status: "DRAFT" };
  const submitted: { url: string; body: Record<string, unknown> }[] = [];
  const notify = vi.fn();
  const onConfirmed = vi.fn();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      submitted.push({ url, body: JSON.parse(options.body as string) });
      if (pending) await gate;
      owner.status = account.status = "ACTIVE";
      return new Response(JSON.stringify({ statement_import: { ...record, status: "CONFIRMED" } }));
    }
    if (profilesFail && url === "/api/fee-plans") return new Response(JSON.stringify({ detail: "收费计划加载失败" }), { status: 503 });
    return new Response(JSON.stringify(url === "/api/statement-imports" ? [record]
      : url === "/api/clients" ? legacy ? [owner] : []
      : url === "/api/accounts" ? legacy ? [account] : []
      : url === "/api/balance-snapshots" ? legacy ? [{ id: 9, account_id: 7, as_of_date: "2026-06-30", total_balance: "1000.00" }] : []
      : url === "/api/ai-assistant/status" ? { status: "unavailable" } : profileRecords[url] ?? []));
  }));
  render(<ImportsPage embedded notify={notify} onConfirmed={onConfirmed} />);
  return { submitted, notify, onConfirmed, release };
}

async function open() {
  fireEvent.click(await screen.findByRole("button", { name: /资料确认.png/ }));
}

test("新客户在一次确认中提交FC、收费计划及两种管理日期，缺资料不能入账", async () => {
  const { submitted } = setup();
  await open();
  const save = screen.getByRole("button", { name: "确认并生成历史结余" });
  fireEvent.click(save);
  expect(submitted).toHaveLength(0);
  completeProfile();
  fireEvent.change(screen.getByLabelText("客户开始管理日期"), { target: { value: "2025-12-01" } });
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0]).toMatchObject({ url: "/api/statement-imports/1/confirm", body: {
    profile: { platform_id: 1, fee_plan_id: 1, fc_id: 1, start_date: "2026-01-01", management_start_date: "2025-12-01" },
  } });
});

test("旧版已入账草稿就地完成资料确认，不重新生成结余，连点仅提交一次", async () => {
  const { submitted, release, notify, onConfirmed } = setup({ legacy: true, pending: true });
  await open();
  expect(screen.queryByRole("button", { name: /生成历史结余/ })).toBeNull();
  completeProfile();
  const form = screen.getByRole("button", { name: "确认资料" }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  expect(submitted).toHaveLength(1);
  expect(submitted[0].url).toBe("/api/statement-imports/1/confirm-profile");
  expect(submitted[0].body).not.toHaveProperty("total_balance");
  await act(async () => release());
  await waitFor(() => expect(notify).toHaveBeenCalledWith("客户与账户资料已确认，原历史结余保持不变"));
  expect(onConfirmed).toHaveBeenCalledOnce();
  expect(screen.queryByRole("button", { name: "确认资料" })).toBeNull();
});

test("收费计划加载失败时阻止资料与结余确认", async () => {
  const { submitted } = setup({ profilesFail: true });
  await open();
  await screen.findByText("收费计划加载失败");
  const save = screen.getByRole("button", { name: /生成历史结余/ }) as HTMLButtonElement;
  expect(save.disabled).toBe(true);
  fireEvent.submit(save.closest("form")!);
  expect(submitted).toHaveLength(0);
});

test("更改账户身份会清空此前填写的收费计划及管理日期", async () => {
  setup();
  await open();
  completeProfile();
  fireEvent.change(screen.getByLabelText(/^Account Number/), { target: { value: "NEW-2" } });
  expect((screen.getByLabelText("账户收费计划") as HTMLSelectElement).value).toBe("");
  expect((screen.getByLabelText("账户开始管理日期") as HTMLInputElement).value).toBe("");
});
