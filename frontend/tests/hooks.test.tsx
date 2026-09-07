import { act, renderHook, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { useApiList } from "../src/hooks";

function pendingFetches() {
  const resolve: ((response: Response) => void)[] = [];
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => resolve.push(done))));
  return resolve;
}

test("较早的列表请求不能覆盖保存后重新加载的结果", async () => {
  const resolve = pendingFetches();
  const { result } = renderHook(() => useApiList<{ id: number }>("/api/companies"));
  let latest!: Promise<boolean>;
  act(() => { latest = result.current.reload(); });
  await act(async () => { resolve[1](Response.json([{ id: 2 }])); await latest; });
  expect(result.current.data).toEqual([{ id: 2 }]);
  await act(async () => { resolve[0](Response.json([{ id: 1 }])); });
  expect(result.current.data).toEqual([{ id: 2 }]);
});

test("切换查询后旧响应和旧错误均不能污染当前列表", async () => {
  const resolve = pendingFetches();
  const { result, rerender } = renderHook(({ path }) => useApiList<{ id: number }>(path), { initialProps: { path: "/api/companies" } });
  rerender({ path: "/api/fcs" });
  await act(async () => { resolve[1](Response.json([{ id: 3 }])); });
  await act(async () => { resolve[0](Response.json({ detail: "旧请求失败" }, { status: 500 })); });
  expect(result.current.data).toEqual([{ id: 3 }]);
  expect(result.current.error).toBe("");
  await waitFor(() => expect(result.current.loading).toBe(false));
});
