import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { useBrowserSession } from "../src/useBrowserSession";

class FakeSocket extends EventTarget {
  static instances: FakeSocket[] = [];
  close = vi.fn(() => this.dispatchEvent(new Event("close")));
  constructor(public url: URL) {
    super();
    FakeSocket.instances.push(this);
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
});
afterEach(() => vi.useRealTimers());

test("关闭页面释放连接，后台切换不退出，页面恢复重新注册", () => {
  const { unmount } = renderHook(useBrowserSession);
  const first = FakeSocket.instances[0];
  expect(first.url.pathname).toBe("/api/browser-session");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(first.close).not.toHaveBeenCalled();
  window.dispatchEvent(new Event("pagehide"));
  expect(first.close).toHaveBeenCalledOnce();
  act(() => vi.advanceTimersByTime(5000));
  expect(FakeSocket.instances).toHaveLength(1);
  window.dispatchEvent(new Event("pageshow"));
  expect(FakeSocket.instances).toHaveLength(2);
  window.dispatchEvent(new Event("pageshow"));
  expect(FakeSocket.instances).toHaveLength(2);
  unmount();
  expect(FakeSocket.instances[1].close).toHaveBeenCalledOnce();
});

test("连接意外断开会重连，卸载取消重连，不遗留旧页面连接", () => {
  const { unmount } = renderHook(useBrowserSession);
  FakeSocket.instances[0].dispatchEvent(new Event("close"));
  act(() => vi.advanceTimersByTime(1000));
  expect(FakeSocket.instances).toHaveLength(2);
  FakeSocket.instances[1].dispatchEvent(new Event("close"));
  unmount();
  act(() => vi.advanceTimersByTime(5000));
  window.dispatchEvent(new Event("pageshow"));
  expect(FakeSocket.instances).toHaveLength(2);
});
