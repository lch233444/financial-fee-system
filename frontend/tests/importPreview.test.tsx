import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ImportsPage from "../src/pages/ImportsPage";
import { loadPreviewImage, mockImagePreviewLayout } from "./imagePreviewFixtures";

async function setup(file: (path: string) => Promise<Response>) {
  mockImagePreviewLayout();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  URL.createObjectURL = vi.fn(() => "blob:import-preview");
  URL.revokeObjectURL = vi.fn();
  const records = ["合成图片.png", "合成文档.pdf"].map((original_name, i) => ({
    id: i + 1, original_name, mime_type: i ? "application/pdf" : "image/png", status: "NEEDS_REVIEW",
    extracted: { document_type: "unknown" }, confidence: {}, warnings: [],
  }));
  const fetcher = vi.fn(async (path: string, _options?: RequestInit) => path.endsWith("/file") ? file(path)
    : new Response(JSON.stringify(path === "/api/statement-imports" ? records
      : path === "/api/ai-assistant/status" ? { status: "unavailable" } : [])));
  vi.stubGlobal("fetch", fetcher);
  render(<ImportsPage notify={vi.fn()} embedded />);
  fireEvent.click(await screen.findByRole("button", { name: /合成图片.png/ }));
  return fetcher;
}

test("自动导入原件在弹窗放大核对，关闭释放资源、回到原按钮且不写入财务记录", async () => {
  const fetcher = await setup(async () => new Response("synthetic", { headers: { "Content-Type": "image/png" } }));
  expect(screen.queryByRole("link", { name: "打开文件" })).toBeNull();
  const open = screen.getByRole("button", { name: "打开文件" });
  open.focus(); fireEvent.click(open);
  const dialog = screen.getByRole("dialog", { name: "原始文件预览" });
  const image = await within(dialog).findByRole("img");
  loadPreviewImage(image);
  fireEvent.click(within(dialog).getByRole("button", { name: "放大图片" }));
  expect(within(dialog).getByRole("status", { name: "图片缩放比例" }).textContent).toBe("125%");
  expect(within(dialog).getByRole("link", { name: "下载" }).getAttribute("download")).toBe("合成图片.png");
  fireEvent.click(within(dialog).getByRole("button", { name: "关闭预览" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(open);
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:import-preview");
  expect(fetcher).toHaveBeenCalledWith("/api/statement-imports/1/file", expect.anything());
  expect(fetcher.mock.calls.every(([, options]) => !options?.method || options.method === "GET")).toBe(true);
});

test("切换导入记录后弹窗使用新PDF原件，Escape关闭", async () => {
  const fetcher = await setup(async () => new Response("%PDF-synthetic", { headers: { "Content-Type": "application/pdf" } }));
  fireEvent.click(screen.getByRole("button", { name: /合成文档.pdf/ }));
  fireEvent.click(screen.getByRole("button", { name: "打开文件" }));
  const dialog = screen.getByRole("dialog", { name: "原始文件预览" });
  await waitFor(() => expect(dialog.querySelector("object")?.getAttribute("type")).toBe("application/pdf"));
  expect(within(dialog).getByRole("link", { name: "下载" }).getAttribute("download")).toBe("合成文档.pdf");
  expect(fetcher).toHaveBeenCalledWith("/api/statement-imports/2/file", expect.anything());
  fireEvent(dialog, new Event("cancel", { cancelable: true }));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("原件读取失败在弹窗明确提示，关闭后仍可继续核对", async () => {
  await setup(async () => new Response(JSON.stringify({ detail: "原件不存在" }), { status: 404 }));
  fireEvent.click(screen.getByRole("button", { name: "打开文件" }));
  const dialog = screen.getByRole("dialog");
  expect((await within(dialog).findByRole("alert")).textContent).toContain("原件不存在");
  expect(within(dialog).queryByRole("link", { name: "下载" })).toBeNull();
  fireEvent.click(within(dialog).getByRole("button", { name: "关闭预览" }));
  expect(screen.getByRole("button", { name: "打开文件" })).toBeTruthy();
});
