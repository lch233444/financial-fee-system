import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SearchableSelect from "../src/SearchableSelect";
import ClientsPage from "../src/pages/ClientsPage";

const options = [
  { value: "11", label: "陳大文" },
  { value: "12", label: "Chan Mei Ling" },
  { value: "13", label: "陳小明" },
];

function Form({ disabled = false }: { disabled?: boolean }) {
  const [value, setValue] = useState("11");
  return <form aria-label="新增账户">
    <SearchableSelect label="客户" name="client_id" value={value} onChange={setValue}
      options={options} required disabled={disabled} />
  </form>;
}

test("按中英文片段搜索，输入不改变客户，明确选择后提交实际ID", () => {
  render(<Form />);
  const search = screen.getByRole("searchbox", { name: "客户搜索" });
  const select = screen.getByRole("combobox", { name: "客户" }) as HTMLSelectElement;
  fireEvent.change(search, { target: { value: "  ＣＨＡＮ  " } });
  expect(screen.queryByRole("option", { name: "陳小明" })).toBeNull();
  expect(screen.getByRole("option", { name: "Chan Mei Ling" })).toBeTruthy();
  expect(select.value).toBe("11");
  expect(screen.getByRole("status").textContent).toContain("当前选择已保留");
  fireEvent.change(select, { target: { value: "12" } });
  const data = new FormData(screen.getByRole("form", { name: "新增账户" }) as HTMLFormElement);
  expect([...data.entries()]).toEqual([["client_id", "12"]]);
  expect((search as HTMLInputElement).value).toBe("");
  fireEvent.change(search, { target: { value: "小明" } });
  expect(screen.getByRole("option", { name: "陳小明" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "陳大文" })).toBeNull();
});

test("无结果时保留当前客户；清空搜索恢复所有选项；回车不提交表单", () => {
  render(<Form />);
  const search = screen.getByRole("searchbox", { name: "客户搜索" });
  fireEvent.change(search, { target: { value: "不存在" } });
  expect(screen.getByRole("status").textContent).toContain("没有匹配项");
  expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("11");
  expect(fireEvent.keyDown(search, { key: "Enter" })).toBe(false);
  fireEvent.change(search, { target: { value: "" } });
  expect(screen.getAllByRole("option")).toHaveLength(4);
});

test("禁用期间搜索与选择一起锁定", () => {
  render(<Form disabled />);
  expect((screen.getByRole("searchbox") as HTMLInputElement).disabled).toBe(true);
  expect((screen.getByRole("combobox") as HTMLSelectElement).disabled).toBe(true);
});

test("新增账户实际页面按客户搜索，选择后提交匹配的客户ID", async () => {
  const requests: Array<{ path: string; body: Record<string, unknown> }> = [];
  const records: Record<string, unknown[]> = {
    "/api/clients": options.map((item) => ({ id: Number(item.value), name: item.label, status: "ACTIVE", company_id: 1 })),
    "/api/companies": [{ id: 1, name: "示例公司", code: "SAMPLE" }],
    "/api/fee-plans": [{ id: 1, name: "20%", company_id: 1 }],
    "/api/platforms": [{ id: 1, name: "示例平台" }],
  };
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    if (init?.method === "POST") requests.push({ path, body: JSON.parse(init.body as string) });
    return new Response(JSON.stringify(init?.method === "POST" ? { id: 1 } : records[path] ?? []));
  }));
  render(<ClientsPage notify={vi.fn()} />);
  await screen.findByRole("option", { name: "Chan Mei Ling" });
  fireEvent.change(screen.getByRole("searchbox", { name: "新增账户客户搜索" }), { target: { value: "mei" } });
  fireEvent.change(screen.getByRole("combobox", { name: "新增账户客户" }), { target: { value: "12" } });
  const form = screen.getByRole("button", { name: "保存Sub Account" }).closest("form")!;
  for (const [name, value] of Object.entries({ platform_id: "1", fee_plan_id: "1", account_number: "SAMPLE", start_date: "2026-01-01" })) {
    fireEvent.change(form.querySelector(`[name="${name}"]`)!, { target: { value } });
  }
  fireEvent.submit(form);
  expect(requests).toHaveLength(1);
  expect(requests[0].path).toBe("/api/accounts");
  expect(requests[0].body.client_id).toBe(12);
});
