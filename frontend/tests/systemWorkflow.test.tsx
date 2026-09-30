import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SystemPage from "../src/pages/SystemPage";

function setup() {
  const writes: Array<{ path: string; init: RequestInit }> = [];
  const notify = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (path: string, init: RequestInit = {}) => {
    if (init.method === "POST") {
      writes.push({ path, init });
      return path.startsWith("/api/backups?") ? new Response("synthetic zip")
        : new Response(JSON.stringify({ staged: true, restart_required: true, shutdown_scheduled: true, cleanup_warning: null }));
    }
    return new Response(JSON.stringify(path === "/api/system-info"
      ? { app_name: "合成测试", data_root: "F:/synthetic/data", database_path: "F:/synthetic/data/system.db", template_path: "F:/synthetic/template.xlsx", template_exists: true, local_only: true }
      : { available: false, authenticated: false, status: "unavailable", message: "合成环境未配置识别" }));
  }));
  render(<SystemPage notify={notify} />);
  return { writes, notify };
}

test("选择检查批次后，只在明确导出时发送对应请求", async () => {
  const { writes, notify } = setup();
  await screen.findByLabelText("检查年度");
  fireEvent.change(screen.getByLabelText("检查年度"), { target: { value: "2025" } });
  fireEvent.change(screen.getByLabelText("检查季度"), { target: { value: "2" } });
  expect((screen.getByLabelText("检查年度") as HTMLInputElement).value).toBe("2025");
  expect((screen.getByLabelText("检查季度") as HTMLSelectElement).value).toBe("2");
  expect(writes).toHaveLength(0);
  expect((screen.getByRole("button", { name: "登录ChatGPT Pro" }) as HTMLButtonElement).disabled).toBe(true);
  const downloadClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const revoke = vi.fn();
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => "blob:synthetic");
    static revokeObjectURL = revoke;
  });
  fireEvent.click(screen.getByRole("button", { name: "生成并下载ZIP数据包" }));
  await waitFor(() => expect(notify).toHaveBeenCalledWith("2025 Q2完整数据包已生成并通过校验"));
  expect(writes).toHaveLength(1);
  expect(writes[0].path).toBe("/api/backups?year=2025&quarter=2");
  expect(new Headers(writes[0].init.headers).get("X-Financial-System-Request")).toBe("1");
  expect(downloadClick).toHaveBeenCalledOnce();
  expect(revoke).toHaveBeenCalledWith("blob:synthetic");
});

test("选择导入文件后，取消覆盖确认不发送请求，确认后才安排恢复", async () => {
  const { writes, notify } = setup();
  const fileInput = await screen.findByLabelText("选择完整数据包ZIP") as HTMLInputElement;
  const file = new File(["synthetic zip"], "synthetic.zip", { type: "application/zip" });
  fireEvent.change(fileInput, { target: { files: [file] } });
  expect(fileInput.files?.[0]).toBe(file);
  expect(writes).toHaveLength(0);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.submit(fileInput.closest("form")!);
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining("完整覆盖"));
  expect(writes).toHaveLength(0);
  expect(notify).not.toHaveBeenCalled();
  confirm.mockReturnValue(true);
  fireEvent.submit(fileInput.closest("form")!);
  await waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining("重新启动以完成导入")));
  expect(writes).toHaveLength(1);
  expect(writes[0].path).toBe("/api/backups/restore");
  expect(writes[0].init.body).toBeInstanceOf(FormData);
  expect(new Headers(writes[0].init.headers).get("X-Financial-System-Request")).toBe("1");
});
