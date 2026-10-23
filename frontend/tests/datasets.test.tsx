import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, afterEach, expect, test, vi } from "vitest";

beforeEach(() => vi.resetModules());

test("创建独立包只发新增请求并保留当前选择", async () => {
  const session = await import("../src/datasetSession");
  const { DatasetManagement } = await import("../src/Datasets");
  const initial = { active_id: "primary", token: "token-a", datasets: [{ id: "primary", name: "原有数据" }] };
  session.setDatasetState(initial);
  const fetcher = vi.fn(async (_path, options) => new Response(JSON.stringify(options?.method === "POST"
    ? { id: "b", name: "第二套" } : { ...initial, datasets: [...initial.datasets, { id: "b", name: "第二套" }] })));
  vi.stubGlobal("fetch", fetcher);
  render(<DatasetManagement notify={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("新数据包名称"), { target: { value: "第二套" } });
  fireEvent.click(screen.getByRole("button", { name: "新建空白数据包" }));
  await waitFor(() => expect(session.datasetState()?.datasets).toHaveLength(2));
  expect(session.datasetState()?.active_id).toBe("primary");
  expect(fetcher.mock.calls[0][0]).toBe("/api/datasets");
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("X-Financial-Dataset")).toBe("token-a");
});

test("其他表单未保存内容不会因保存一个表单而清除", async () => {
  const session = await import("../src/datasetSession");
  const { postJson } = await import("../src/api");
  vi.stubGlobal("fetch", vi.fn(async () => new Response('{}')));
  render(<><form onSubmit={event => { event.preventDefault(); void postJson("/api/clients", {}); }}>
    <input aria-label="A" defaultValue="before" /><button>保存A</button></form><form><input aria-label="B" /></form></>);
  const stop = session.trackDatasetForms();
  fireEvent.change(screen.getByLabelText("A"), { target: { value: "after" } });
  fireEvent.change(screen.getByLabelText("B"), { target: { value: "unsaved" } });
  fireEvent.click(screen.getByText("保存A"));
  await waitFor(() => expect(fetch).toHaveBeenCalled());
  expect(session.hasUnsavedDatasetChanges()).toBe(true);
  fireEvent.change(screen.getByLabelText("B"), { target: { value: "" } });
  expect(session.hasUnsavedDatasetChanges()).toBe(false);
  stop();
});

test("保存失败保留未保存状态，切换选择不提交请求", async () => {
  const session = await import("../src/datasetSession");
  const { DatasetSelector } = await import("../src/Datasets");
  session.setDatasetState({ active_id: "a", token: "token-a", datasets: [{ id: "a", name: "A" }, { id: "b", name: "B" }] });
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  render(<><DatasetSelector /><input aria-label="姓名" /></>);
  const stop = session.trackDatasetForms();
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "draft" } });
  fireEvent.change(screen.getByLabelText("当前数据包"), { target: { value: "b" } });
  expect(screen.getByRole("alert").textContent).toContain("未保存");
  expect(fetcher).not.toHaveBeenCalled();
  stop();
});

test("跨页准备切换时冻结界面且报告未保存状态，取消后恢复", async () => {
  const session = await import("../src/datasetSession");
  session.setDatasetState({ active_id: "a", token: "token-a", datasets: [] });
  render(<div className="app-shell"><input aria-label="未保存输入" /></div>);
  const stop = session.trackDatasetForms();
  fireEvent.change(screen.getByLabelText("未保存输入"), { target: { value: "draft" } });
  const socket = { send: vi.fn() } as unknown as WebSocket;
  session.handleDatasetMessage(socket, JSON.stringify({ type: "dataset-prepare", nonce: "n" }));
  expect(JSON.parse(vi.mocked(socket.send).mock.calls[0][0] as string)).toEqual({ type: "dataset-ready", nonce: "n", ready: false });
  expect(document.querySelector(".app-shell")?.hasAttribute("inert")).toBe(true);
  session.handleDatasetMessage(socket, JSON.stringify({ type: "dataset-finish", token: "token-a" }));
  expect(session.datasetLocked()).toBe(false);
  expect(document.querySelector(".app-shell")?.hasAttribute("inert")).toBe(false);
  stop();
});

test("旧页面收到切换完成后锁定，链接包含原数据包令牌", async () => {
  const session = await import("../src/datasetSession");
  session.setDatasetState({ active_id: "a", token: "token-a", datasets: [] });
  expect(session.datasetUrl("/api/attachments/1/file")).toContain("dataset_token=token-a");
  session.handleDatasetMessage({} as WebSocket, JSON.stringify({ type: "dataset-finish", token: "token-b" }));
  expect(session.datasetIsStale()).toBe(true);
  await expect(session.datasetFetch("/api/clients", {})).rejects.toThrow("已变化");
});
