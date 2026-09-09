import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SearchableSelect from "../src/SearchableSelect";
import ClientsPage from "../src/pages/ClientsPage";

const options = [{ value: "11", label: "陳大文" }, { value: "12", label: "Chan Mei Ling" }, { value: "13", label: "陳小明" }];
function Form({ disabled = false, initial = "11" }: { disabled?: boolean; initial?: string }) {
  const [value, setValue] = useState(initial);
  return <form aria-label="新增账户"><SearchableSelect label="客户" name="client_id" value={value} onChange={setValue} options={options} required disabled={disabled} /></form>;
}
const submitted = () => new FormData(screen.getByRole("form", { name: "新增账户" }) as HTMLFormElement).get("client_id");

test("搜索中英文及全角片段不改变客户，明确选择后提交ID", () => {
  render(<Form />);
  const input = screen.getByRole("combobox", { name: "客户" });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "  ＣＨＡＮ  " } });
  expect(screen.queryByRole("option", { name: "陳小明" })).toBeNull();
  expect(submitted()).toBe("11");
  expect(screen.getByRole("status").textContent).toContain("当前选择已保留：陳大文");
  fireEvent.click(screen.getByRole("option", { name: "Chan Mei Ling" }));
  expect(submitted()).toBe("12");
  expect((input as HTMLInputElement).value).toBe("Chan Mei Ling");
  expect(input.getAttribute("aria-expanded")).toBe("false");
});

test("无结果、Escape和失焦均保留选择；回车搜索不提交或自动换客户", () => {
  render(<Form />);
  const input = screen.getByRole("combobox");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "不存在" } });
  expect(screen.getByText("没有匹配项，请更换关键词")).toBeTruthy();
  expect(fireEvent.keyDown(input, { key: "Enter" })).toBe(false);
  expect(submitted()).toBe("11");
  fireEvent.keyDown(input, { key: "Escape" });
  expect((input as HTMLInputElement).value).toBe("陳大文");
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "小明" } });
  fireEvent.blur(input);
  expect(submitted()).toBe("11");
  expect(screen.queryByRole("listbox")).toBeNull();
});

test("方向键和回车完成明确选择，组合输入过程不会误选", () => {
  render(<Form initial="" />);
  const input = screen.getByRole("combobox") as HTMLInputElement;
  expect(input.validity.valid).toBe(false);
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "小明" } });
  expect(input.validity.valid).toBe(false);
  fireEvent.keyDown(input, { key: "ArrowDown" });
  expect(input.getAttribute("aria-activedescendant")).toBeTruthy();
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(submitted()).toBe("");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(submitted()).toBe("13");
  expect(input.validity.valid).toBe(true);
});

test("禁用锁定选择并从表单提交中排除；打开后禁用会关闭候选", () => {
  const view = render(<Form />);
  fireEvent.focus(screen.getByRole("combobox"));
  view.rerender(<Form disabled />);
  expect((screen.getByRole("combobox") as HTMLInputElement).disabled).toBe(true);
  expect(screen.queryByRole("listbox")).toBeNull();
  expect(submitted()).toBeNull();
});

test("长候选列表限制渲染，关键词仍可找到第100项之后的客户", () => {
  render(<SearchableSelect label="大列表" value="" onChange={vi.fn()} options={Array.from({ length: 150 }, (_, index) => ({ value: String(index), label: `客户 ${index}` }))} />);
  const input = screen.getByRole("combobox");
  fireEvent.focus(input);
  expect(screen.getAllByRole("option")).toHaveLength(100);
  fireEvent.change(input, { target: { value: "客户 149" } });
  expect(screen.getByRole("option", { name: "客户 149" })).toBeTruthy();
});

test("新增账户页面通过搜索选择客户ID，分页切换不影响输入", async () => {
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
  await screen.findByText("Chan Mei Ling");
  const input = screen.getByRole("combobox", { name: "新增账户客户" });
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value: "mei" } });
  fireEvent.click(screen.getByRole("option", { name: "Chan Mei Ling" }));
  const form = screen.getByRole("button", { name: "保存Sub Account" }).closest("form")!;
  for (const [name, value] of Object.entries({ platform_id: "1", fee_plan_id: "1", account_number: "SAMPLE", start_date: "2026-01-01" })) fireEvent.change(form.querySelector(`[name="${name}"]`)!, { target: { value } });
  fireEvent.submit(form);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toMatchObject({ path: "/api/accounts", body: { client_id: 12 } });
});
