import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";
import SetupPage from "../src/pages/SetupPage";
import ClientsPage from "../src/pages/ClientsPage";
import TransactionsPage from "../src/pages/TransactionsPage";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function mockDatabase() {
  const records: Record<string, Record<string, unknown>[]> = {
    "/api/companies": [{ id: 1, name: "原公司", code: "OLD", payment_terms_days: 14 }],
    "/api/fcs": [{ id: 1, company_id: 1, company_name: "原公司", name: "原FC", code: "FC" }],
    "/api/platforms": [{ id: 1, name: "原平台", code: "PL" }],
    "/api/fee-plans": [{ id: 1, company_id: 1, name: "20%计划", code: "PS20", fee_rate_percent: "20.00" }],
    "/api/clients": [{ id: 1, name: "原客户", company_id: 1, fc_id: 1, status: "ACTIVE", management_start_date: "2026-01-01" }],
    "/api/accounts": [],
  };
  const pending = deferred<void>();
  const requests: string[] = [];
  const fetcher = vi.fn(async (input: string, options?: RequestInit) => {
    if (options?.method === "POST") {
      requests.push(input);
      await pending.promise;
      const saved = { id: 2, ...JSON.parse(options.body as string) };
      records[input].push(saved);
      return new Response(JSON.stringify(saved), { status: 201 });
    }
    return new Response(JSON.stringify(records[input] ?? []));
  });
  vi.stubGlobal("fetch", fetcher);
  return { pending, requests, fetcher, records };
}

function fill(form: HTMLElement, values: Record<string, string>) {
  for (const [name, value] of Object.entries(values)) {
    const input = form.querySelector(`[name="${name}"]`)!;
    expect(input).not.toBeNull();
    fireEvent.change(input, { target: { value } });
  }
}

describe("保存成功后立即更新页面", () => {
  test.each([
    { tab: "Company", button: "保存Company", path: "/api/companies", title: "现有Company", values: { name: "新公司", code: "NEW" }, text: "新公司" },
    { tab: "FC", button: "保存FC", path: "/api/fcs", title: "现有FC", values: { company_id: "1", name: "新FC", code: "NEWFC" }, text: "新FC" },
    { tab: "Platform", button: "保存Platform", path: "/api/platforms", title: "现有Platform", values: { name: "新平台", code: "NEWPL" }, text: "新平台" },
    { tab: "Fee Plan", button: "保存Fee Plan", path: "/api/fee-plans", title: "现有Fee Plan", values: { company_id: "1", name: "新计划", code: "NEWPS", rate: "20" }, text: "新计划" },
  ])("$tab：异步完成后重置表单并显示新记录", async ({ tab, button, path, title, values, text }) => {
    const db = mockDatabase();
    const notify = vi.fn();
    render(<SetupPage notify={notify} />);
    await screen.findByText("原公司");
    fireEvent.click(screen.getByRole("button", { name: tab, exact: true }));
    const form = screen.getByRole("button", { name: button }).closest("form")!;
    fill(form, values);
    fireEvent.submit(form);
    await act(async () => db.pending.resolve());
    const panel = screen.getByRole("heading", { name: title }).closest("section")!;
    await waitFor(() => expect(within(panel).getByText(text)).toBeTruthy());
    expect((form.querySelector('[name="name"]') as HTMLInputElement).value).toBe("");
    expect(db.requests).toEqual([path]);
    expect(notify).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Cannot read properties/)).toBeNull();
  });

  test.each([
    { button: "保存Client", path: "/api/clients", values: { company_id: "1", fc_id: "1", name: "新客户", start_date: "2026-01-01" }, text: "新客户" },
    { button: "保存Sub Account", path: "/api/accounts", values: { client_id: "1", platform_id: "1", fee_plan_id: "1", account_number: "NEW-ACCOUNT", start_date: "2026-01-01" }, text: "NEW-ACCOUNT" },
  ])("$button：列表和依赖选项同时更新", async ({ button, path, values, text }) => {
    const db = mockDatabase();
    render(<ClientsPage notify={vi.fn()} />);
    await screen.findByRole("option", { name: "原客户" });
    const form = screen.getByRole("button", { name: button }).closest("form")!;
    fill(form, values);
    fireEvent.submit(form);
    await act(async () => db.pending.resolve());
    const panel = screen.getByRole("heading", { name: "客户与账户清单" }).closest("section")!;
    await waitFor(() => expect(within(panel).getByText(text)).toBeTruthy());
    expect(db.requests).toEqual([path]);
  });

  test("保存中的同一表单不重复发送请求", async () => {
    const db = mockDatabase();
    render(<SetupPage notify={vi.fn()} />);
    await screen.findByText("原公司");
    const form = screen.getByRole("button", { name: "保存Company" }).closest("form")!;
    fill(form, { name: "新公司", code: "NEW" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(db.requests).toEqual(["/api/companies"]);
    await act(async () => db.pending.resolve());
  });

  test("保存失败时保留输入并显示服务端原因", async () => {
    const db = mockDatabase();
    const originalFetch = db.fetcher.getMockImplementation()!;
    db.fetcher.mockImplementation(async (input, options) => options?.method === "POST"
      ? new Response(JSON.stringify({ detail: "Company名称或Code已存在" }), { status: 409 })
      : originalFetch(input, options));
    render(<SetupPage notify={vi.fn()} />);
    await screen.findByText("原公司");
    const form = screen.getByRole("button", { name: "保存Company" }).closest("form")!;
    fill(form, { name: "重复公司", code: "OLD" });
    fireEvent.submit(form);
    await screen.findByText("Company名称或Code已存在");
    expect((form.querySelector('[name="name"]') as HTMLInputElement).value).toBe("重复公司");
  });

  test("写入成功但刷新失败时明确告知已保存，避免重复录入", async () => {
    const db = mockDatabase();
    const originalFetch = db.fetcher.getMockImplementation()!;
    db.fetcher.mockImplementation(async (input, options) => input === "/api/companies" && !options?.method && db.records[input].length > 1
      ? new Response(JSON.stringify({ detail: "暂时无法读取列表" }), { status: 503 })
      : originalFetch(input, options));
    render(<SetupPage notify={vi.fn()} />);
    await screen.findByText("原公司");
    const form = screen.getByRole("button", { name: "保存Company" }).closest("form")!;
    fill(form, { name: "新公司", code: "NEW" });
    fireEvent.submit(form);
    await act(async () => db.pending.resolve());
    await screen.findByText(/已保存，但列表刷新失败/);
    expect(db.requests).toEqual(["/api/companies"]);
    expect(db.records["/api/companies"]).toHaveLength(2);
  });

  test("异步上传凭证后清空选择、更新凭证列表及快照完整性", async () => {
    const db = mockDatabase();
    db.records["/api/accounts"] = [{ id: 1, client_id: 1, client_name: "原客户", platform_id: 1, platform_name: "原平台", account_number: "A1" }];
    db.records["/api/balance-snapshots"] = [{ id: 7, account_id: 1, account_number: "A1", as_of_date: "2026-03-31", total_balance: "1000.00", holdings: [], evidence_complete: false }];
    db.records["/api/attachments"] = [];
    const originalFetch = db.fetcher.getMockImplementation()!;
    let submitted: FormData | undefined;
    db.fetcher.mockImplementation(async (input, options) => {
      if (input !== "/api/attachments" || options?.method !== "POST") return originalFetch(input, options);
      submitted = options.body as FormData;
      await db.pending.promise;
      const saved = { id: 3, entity_type: submitted.get("entity_type"), entity_id: Number(submitted.get("entity_id")), original_name: "凭证.pdf", created_at: "2026-09-07T00:00:00Z" };
      db.records[input].push(saved);
      Object.assign(db.records["/api/balance-snapshots"][0], { evidence_complete: true, evidence_count: 1 });
      return new Response(JSON.stringify(saved), { status: 201 });
    });
    const notify = vi.fn();
    render(<TransactionsPage notify={notify} />);
    await screen.findByRole("option", { name: /2026-03-31/ });
    const form = screen.getByRole("button", { name: "上传并关联" }).closest("form")!;
    fill(form, { entity_id: "7" });
    fireEvent.change(form.querySelector('[name="file"]')!, { target: { files: [new File(["synthetic"], "凭证.pdf", { type: "application/pdf" })] } });
    fireEvent.submit(form);
    await act(async () => db.pending.resolve());
    await screen.findByText("凭证.pdf");
    await screen.findByText("完整 (1)");
    expect(submitted?.get("entity_type")).toBe("SNAPSHOT");
    expect(submitted?.get("entity_id")).toBe("7");
    expect((form.querySelector('[name="entity_id"]') as HTMLSelectElement).value).toBe("");
    expect(notify).toHaveBeenCalledOnce();
    expect(screen.queryByText(/Cannot read properties/)).toBeNull();
  });
});
