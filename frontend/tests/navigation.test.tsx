import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import App from "../src/App";
vi.mock("../src/useBrowserSession", () => ({ useBrowserSession: vi.fn() }));

vi.mock("../src/pages/DashboardPage", () => ({ default: () => <div>概览页面</div> }));
vi.mock("../src/pages/SetupPage", () => ({ default: () => <div>设置页面</div> }));
vi.mock("../src/pages/ClientsPage", () => ({ default: () => <div>客户页面</div> }));
beforeEach(() => window.history.replaceState(null, "", "/"));

test.each([false, true])("初始化慢请求不覆盖用户导航（已导航=%s）", async (navigate) => {
  let resolve!: (response: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done; })));
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  render(<App />);
  expect(await screen.findByText("概览页面")).toBeTruthy();
  if (navigate) fireEvent.click(screen.getByRole("button", { name: /客户与账户/ }));
  await act(async () => resolve(new Response("[]")));
  expect(await screen.findByText(navigate ? "客户页面" : "设置页面")).toBeTruthy();
  expect(screen.queryByText("本地数据库已连接")).toBeNull();
  expect(screen.queryByRole("button", { name: "安全退出系统" })).toBeNull();
});

test("直接打开功能地址及前进后退不被首次设置检查覆盖", async () => {
  window.history.replaceState(null, "", "/#/clients");
  vi.stubGlobal("fetch", vi.fn(async () => new Response("[]")));
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  render(<App />);
  expect(await screen.findByText("客户页面")).toBeTruthy();
  expect(screen.getByRole("button", { name: /客户与账户/ }).getAttribute("aria-current")).toBe("page");
  await act(async () => { window.history.replaceState(null, "", "/#/setup"); window.dispatchEvent(new HashChangeEvent("hashchange")); });
  expect(await screen.findByText("设置页面")).toBeTruthy();
  expect(document.title).toContain("基础设置");
});

test("小窗口导航支持Escape，关闭后归还按钮焦点", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response('[{"id":1}]')));
  render(<App />);
  await screen.findByText("概览页面");
  const button = screen.getByRole("button", { name: "打开导航" });
  fireEvent.click(button);
  expect(document.body.style.overflow).toBe("hidden");
  await waitFor(() => expect(screen.getByRole("button", { name: "关闭导航" })).toBe(document.activeElement));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(document.body.style.overflow).toBe("");
  await waitFor(() => expect(document.activeElement).toBe(button));
});
