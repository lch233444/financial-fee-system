import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { searchClientOptions, type ClientSearchOption } from "../src/clientSimilarity";
import ImportsPage from "../src/pages/ImportsPage";
import { completeProfile, profileRecords } from "./importProfileFixtures";

const base = { document_type: "empf_account_page", client_name: "CHAN TAI MAM", account_number: "NEW-001", scheme_name: "示例平台", trustee: "测试受托人", currency: "HKD", as_of_date: "2026-06-30", total_balance: "1000.00" };
type Fixture = { id: number; original_name: string; extracted: Record<string, unknown>; ai_recognition?: Record<string, unknown> };
function setup(records: Fixture[], clients: Record<string, unknown>[] = [], accounts: Record<string, unknown>[] = []) {
  const submitted: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/confirm")) {
      submitted.push(JSON.parse(options?.body as string));
      return new Response(JSON.stringify({ detail: "合成测试提交边界" }), { status: 409 });
    }
    return new Response(JSON.stringify(url === "/api/statement-imports" ? records.map((record) => ({ mime_type: "image/png", status: "NEEDS_REVIEW", confidence: {}, warnings: [], ...record }))
      : url === "/api/clients" ? clients : url === "/api/accounts" ? accounts
      : url === "/api/ai-assistant/status" ? { status: "unavailable" } : profileRecords[url] ?? []));
  }));
  render(<ImportsPage notify={vi.fn()} embedded />);
  return submitted;
}

test("姓名相似推荐支持中文错字及英文全角空白，不根据FC文字推断姓名，空姓名保留顺序", () => {
  const options: ClientSearchOption[] = [
    { value: "1", name: "王芳", label: "王芳", searchText: "王芳 陳小明FC" },
    { value: "2", name: "陳小敏", label: "陳小敏", searchText: "陳小敏" },
    { value: "3", name: "陳小明", label: "陳小明", searchText: "陳小明 SYN-003" },
    { value: "4", name: "Chan Tai Man", label: "Chan Tai Man", searchText: "Chan Tai Man contact-004" },
  ];
  expect(searchClientOptions(options, "", "陳小明").map((option) => option.value)).toEqual(["3", "2", "1", "4"]);
  expect(searchClientOptions(options, "", "　ＣＨＡＮ　ＴＡＩ　ＭＡＮ ")[0].value).toBe("4");
  expect(searchClientOptions(options, "", "")).toEqual(options);
  expect(searchClientOptions(options, "SYN-003", "")[0].value).toBe("3");
  expect(searchClientOptions(options, "contact-004", "")[0].value).toBe("4");
  expect(searchClientOptions(options, "陳小朋", "")[0].value).toBe("2");
  expect(options.map((option) => option.value)).toEqual(["1", "2", "3", "4"]);
});

test("相似排序先于100项截断，选客不自动改名，手工改名保留客户并清空账户，匹配后方可确认", async () => {
  const clients = Array.from({ length: 120 }, (_, i) => ({ id: i + 1, name: `其他客户${i}`, status: "ACTIVE" }));
  clients.push({ id: 201, name: "CHAN TAI MAN", status: "ACTIVE" }, { id: 202, name: "CHAN TAI MAM", status: "CLOSED" });
  const submitted = setup([{ id: 1, original_name: "姓名推荐.png", extracted: base }], clients,
    [{ id: 301, client_id: 201, client_name: "CHAN TAI MAN", account_number: "OLD-001", platform_name: "示例平台", status: "ACTIVE" }]);
  fireEvent.click(await screen.findByRole("button", { name: /姓名推荐.png/ }));
  const input = screen.getByRole("combobox", { name: "选择已有客户" });
  expect((input as HTMLInputElement).value).toBe("");
  fireEvent.focus(input);
  const list = screen.getByRole("listbox", { name: "选择已有客户候选" });
  expect(within(list).getAllByRole("option")).toHaveLength(100);
  expect(within(list).getAllByRole("option")[0].textContent).toBe("CHAN TAI MAN");
  expect(within(list).queryByRole("option", { name: "CHAN TAI MAM", exact: true })).toBeNull();
  fireEvent.change(input, { target: { value: "CHAN TAI MAM" } });
  fireEvent.click(screen.getByRole("option", { name: "CHAN TAI MAN", exact: true }));
  const name = screen.getByLabelText(/^客户姓名/) as HTMLInputElement;
  const save = screen.getByRole("button", { name: /生成历史结余/ }) as HTMLButtonElement;
  expect(name.value).toBe("CHAN TAI MAM");
  expect(save.disabled).toBe(true);
  fireEvent.submit(save.closest("form")!);
  expect(submitted).toHaveLength(0);
  const account = screen.getByRole("combobox", { name: "匹配已有账户" }) as HTMLInputElement;
  fireEvent.focus(account);
  fireEvent.click(screen.getByRole("option", { name: /OLD-001/ }));
  fireEvent.change(name, { target: { value: "CHAN TAI MA" } });
  expect((input as HTMLInputElement).value).toBe("CHAN TAI MAN");
  expect(account.value).toBe("");
  expect(save.disabled).toBe(true);
  fireEvent.change(name, { target: { value: "CHAN TAI MAN" } });
  completeProfile();
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0]).toMatchObject({ client_id: 201, client_name: "CHAN TAI MAN", account_id: null });
});

test("异常清单合并重复字段并保留缺失、不确定、单边和校验问题，一致字段隐藏且人工确认仍必需", async () => {
  const extracted = { ...base, trustee: "", as_of_date: "" };
  const submitted = setup([{ id: 1, original_name: "异常清单.png", extracted,
    ai_recognition: { status: "INCOMPLETE", values: { ...extracted, total_balance: "1100.00", trustee: "测试受托人" },
      conflicts: [{ field: "total_balance", ocr_value: "1000.00", ai_value: "1100.00" }],
      uncorroborated: [{ field: "trustee", ocr_value: null, ai_value: "测试受托人" }],
      missing_critical_fields: ["as_of_date"], uncertain_critical_fields: ["client_name", "total_balance"],
      validation_failures: ["total_equals_lifetime_net_plus_gain_loss"],
      validation_checks: [{ check: "total_equals_lifetime_net_plus_gain_loss", status: "FAILED", difference: "100.00" }],
      recognition_requires_human_review: true,
    } }]);
  fireEvent.click(await screen.findByRole("button", { name: /异常清单.png/ }));
  const issues = screen.getByRole("region", { name: "待核对异常" });
  expect(within(issues).getAllByRole("listitem")).toHaveLength(5);
  expect(within(issues).queryByText("账户号码")).toBeNull();
  expect(within(issues).queryByText("平台计划名称")).toBeNull();
  expect(within(issues).getByText("客户姓名")).toBeTruthy();
  expect(within(issues).getByText("关键字段缺失")).toBeTruthy();
  expect(within(issues).getByText("仅单边识别")).toBeTruthy();
  expect(within(issues).getByText("数学校验失败")).toBeTruthy();
  const balance = within(issues).getByText("总余额").closest("li")!;
  expect(balance.textContent).toContain("值冲突 / 智能辅助标记不确定");
  fireEvent.click(within(balance).getByRole("button", { name: "采用智能辅助" }));
  expect((screen.getByLabelText(/^总余额/) as HTMLInputElement).value).toBe("1100.00");
  const missing = within(issues).getByText("结余日期").closest("li")!;
  expect(within(missing).queryByRole("button")).toBeNull();
  fireEvent.change(screen.getByLabelText(/^结余日期/), { target: { value: "2026-06-30" } });
  completeProfile();
  const save = screen.getByRole("button", { name: /生成历史结余/ });
  fireEvent.click(save);
  expect(submitted).toHaveLength(0);
  fireEvent.click(screen.getByRole("checkbox", { name: /我已人工核对完整清单中的5项/ }));
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0]).toMatchObject({ ai_conflicts_reviewed: true, total_balance: "1100.00" });
});

test("识别完全一致时无对比清单，人工复核字段保留且不会自动采用或提交", async () => {
  const submitted = setup([{ id: 1, original_name: "一致清单.png", extracted: base,
    ai_recognition: { status: "AGREED", values: { ...base, total_balance: "1,000.00" } } }]);
  fireEvent.click(await screen.findByRole("button", { name: /一致清单.png/ }));
  expect(screen.queryByRole("region", { name: "待核对异常" })).toBeNull();
  expect(screen.getByText(/未发现待核对异常，一致字段已隐藏/)).toBeTruthy();
  expect((screen.getByLabelText(/^客户姓名/) as HTMLInputElement).value).toBe(base.client_name);
  expect((screen.getByLabelText(/^总余额/) as HTMLInputElement).value).toBe("1000.00");
  expect(screen.queryByRole("button", { name: "采用智能辅助" })).toBeNull();
  expect(submitted).toHaveLength(0);
});
