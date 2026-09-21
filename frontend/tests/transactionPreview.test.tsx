import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import TransactionsPage from "../src/pages/TransactionsPage";

const records: Record<string, unknown[]> = {
  "/api/clients": [{ id: 1, name: "合成客户", status: "ACTIVE" }],
  "/api/accounts": [{ id: 1, client_id: 1, client_name: "合成客户", account_number: "SYNTHETIC", platform_id: 1, platform_name: "平台", fee_plan_id: 1, fee_plan_name: "计划" }],
  "/api/balance-snapshots": [{ id: 2, account_id: 1, as_of_date: "2026-03-31", total_balance: "1000.00", holdings: [], source_type: "STATEMENT_IMPORT", statement_import_id: 7 }],
  "/api/attachments": [{ id: 8, entity_type: "SNAPSHOT", entity_id: 2, original_name: "synthetic-proof.png", created_at: "2026-09-17T01:00:00" }],
};
async function setup(file: () => Promise<Response> = async () => new Response("synthetic-image", { headers: { "Content-Type": "image/png" } }), overrides: Record<string, unknown[]> = {}) {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  URL.createObjectURL = vi.fn(() => "blob:synthetic-preview");
  URL.revokeObjectURL = vi.fn();
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => path.endsWith("/file") ? file() : new Response(JSON.stringify(overrides[path] ?? records[path] ?? [])));
  vi.stubGlobal("fetch", fetcher);
  render(<TransactionsPage notify={vi.fn()} />);
  fireEvent.focus(screen.getByRole("combobox", { name: "资金与余额客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "合成客户" }));
  fireEvent.click(await screen.findByText(/结算凭证归档 ·/));
  return fetcher;
}

test("结算凭证先弹窗展示，下载单独提供，关闭释放预览并返回焦点", async () => {
  await setup();
  const button = within(screen.getByRole("region", { name: "结算凭证归档" })).getByRole("button", { name: "查看原件" });
  button.focus(); fireEvent.click(button);
  const dialog = screen.getByRole("dialog", { name: "结算凭证原件" });
  expect((await within(dialog).findByRole("img")).getAttribute("src")).toBe("blob:synthetic-preview");
  const download = within(dialog).getByRole("link", { name: "下载" });
  expect(download.getAttribute("download")).toBe("synthetic-proof.png");
  expect(download.getAttribute("href")).toBe("blob:synthetic-preview");
  fireEvent.click(within(dialog).getByRole("button", { name: "关闭预览" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:synthetic-preview");
  expect(document.activeElement).toBe(button);
});

test("历史结余显示查看原始凭证，弹窗预览PDF不提供下载入口，Escape关闭", async () => {
  const fetcher = await setup(async () => new Response("%PDF-synthetic", { headers: { "Content-Type": "application/pdf" } }));
  const ledger = within(screen.getByRole("region", { name: "历史结余表", exact: true }));
  expect(ledger.queryByText(/Statement Import/)).toBeNull();
  fireEvent.click(ledger.getByRole("button", { name: "查看原始凭证" }));
  const dialog = screen.getByRole("dialog", { name: "原始凭证" });
  await waitFor(() => expect(dialog.querySelector("object")?.getAttribute("type")).toBe("application/pdf"));
  expect(within(dialog).queryByRole("link")).toBeNull();
  expect(fetcher).toHaveBeenCalledWith("/api/statement-imports/7/file", expect.anything());
  fireEvent(dialog, new Event("cancel", { cancelable: true }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("原件读取失败在弹窗提示，不提供无效下载", async () => {
  await setup(async () => new Response(JSON.stringify({ detail: "原件不存在" }), { status: 404 }));
  fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
  const dialog = screen.getByRole("dialog");
  expect((await within(dialog).findByRole("alert")).textContent).toContain("原件不存在");
  expect(within(dialog).queryByRole("link", { name: "下载" })).toBeNull();
});

test("尚未加载完就关闭预览会取消请求，延迟响应不生成遗留预览", async () => {
  let finish!: (response: Response) => void;
  const fetcher = await setup(() => new Promise<Response>((resolve) => { finish = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "查看原件" }));
  const request = fetcher.mock.calls.find(([path]) => path.endsWith("/file"))!;
  fireEvent.click(screen.getByRole("button", { name: "关闭预览" }));
  expect(request[1]?.signal?.aborted).toBe(true);
  finish(new Response("synthetic-image", { headers: { "Content-Type": "image/png" } }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

test("资金凭证优先显示当前原件，可追溯旧凭证和旧版独立引用原件", async () => {
  const fetcher = await setup(undefined, {
    "/api/transactions": [{ id: 1, account_id: 1, transaction_date: "2026-03-01", transaction_type: "CONTRIBUTION", amount: "100.00", evidence_complete: true, attachment_ids: [9, 10], superseded_attachment_ids: [8] }],
    "/api/attachments": [
      { id: 8, entity_type: "TRANSACTION", entity_id: 1, original_name: "old.png", superseded: true },
      { id: 9, entity_type: "TRANSACTION", entity_id: 1, original_name: "current.png", superseded: false },
      { id: 10, entity_type: "ACCOUNT", entity_id: 1, original_name: "legacy.png", superseded: false },
    ],
  });
  fireEvent.click(within(screen.getByRole("region", { name: "供款、加款、取款记录表" })).getByRole("button", { name: "查看原件" }));
  const dialog = within(screen.getByRole("dialog"));
  expect((await dialog.findByRole("link", { name: "下载" })).getAttribute("download")).toBe("current.png");
  fireEvent.click(dialog.getByRole("button", { name: "旧凭证 · old.png" }));
  await waitFor(() => expect(dialog.getByRole("link", { name: "下载" }).getAttribute("download")).toBe("old.png"));
  expect(fetcher).toHaveBeenCalledWith("/api/attachments/8/file", expect.anything());
  fireEvent.click(dialog.getByRole("button", { name: "legacy.png" }));
  await waitFor(() => expect(dialog.getByRole("link", { name: "下载" }).getAttribute("download")).toBe("legacy.png"));
  expect(fetcher).toHaveBeenCalledWith("/api/attachments/10/file", expect.anything());
});
