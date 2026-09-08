import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import App from "../src/App";
vi.mock("../src/useBrowserSession", () => ({ useBrowserSession: vi.fn() }));

vi.mock("../src/pages/DashboardPage", () => ({ default: () => <div>概览页面</div> }));
vi.mock("../src/pages/SetupPage", () => ({ default: () => <div>设置页面</div> }));
vi.mock("../src/pages/ClientsPage", () => ({ default: () => <div>客户页面</div> }));

test.each([false, true])("初始化慢请求不覆盖用户导航（已导航=%s）", async (navigate) => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  render(<App />);
  expect(screen.getByText("概览页面")).toBeTruthy();
  if (navigate) fireEvent.click(screen.getByRole("button", { name: /客户与账户/ }));
  await act(async () => resolve(new Response("[]")));
  expect(screen.getByText(navigate ? "客户页面" : "设置页面")).toBeTruthy();
  expect(screen.queryByText("本地数据库已连接")).toBeNull();
  expect(screen.queryByRole("button", { name: "安全退出系统" })).toBeNull();
});
