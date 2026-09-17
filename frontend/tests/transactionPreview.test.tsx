import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import TransactionsPage from "../src/pages/TransactionsPage";

const records: Record<string, unknown[]> = {
  "/api/accounts": [{ id: 1, client_id: 1, client_name: "合成客户", account_number: "SYNTHETIC", platform_id: 1, platform_name: "平台", fee_plan_id: 1, fee_plan_name: "计划" }],
  "/api/balance-snapshots": [{ id: 2, account_id: 1, as_of_date: "2026-03-31", total_balance: "1000.00", holdings: [], source_type: "STATEMENT_IMPORT", statement_import_id: 7 }],
  "/api/attachments": [{ id: 8, entity_type: "SNAPSHOT", entity_id: 2, original_name: "synthetic-proof.png", created_at: "2026-09-17T01:00:00" }],
};
async function setup(file: () => Promise<Response> = async () => new Response("synthetic-image", { headers: { "Content-Type": "image/png" } })) {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  URL.createObjectURL = vi.fn(() => "blob:synthetic-preview");
  URL.revokeObjectURL = vi.fn();
  const fetcher = vi.fn(async (path: string, options?: RequestInit) => path.endsWith("/file") ? file() : new Response(JSON.stringify(records[path] ?? [])));
  vi.stubGlobal("fetch", fetcher);
  render(<TransactionsPage notify={vi.fn()} />);
  fireEvent.focus(screen.getByRole("combobox", { name: "资金与余额客户" }));
  fireEvent.click(await screen.findByRole("option", { name: "合成客户" }));
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

test("快照来源只写查看原账单，弹窗预览PDF不提供下载入口，Escape关闭", async () => {
  const fetcher = await setup(async () => new Response("%PDF-synthetic", { headers: { "Content-Type": "application/pdf" } }));
  const ledger = within(screen.getByRole("region", { name: "余额快照", exact: true }));
  expect(ledger.queryByText(/Statement Import/)).toBeNull();
  fireEvent.click(ledger.getByRole("button", { name: "查看原账单" }));
  const dialog = screen.getByRole("dialog", { name: "原账单" });
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
