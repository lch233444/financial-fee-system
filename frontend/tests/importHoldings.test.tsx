import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ImportsPage from "../src/pages/ImportsPage";

const holdings = [
  { fund_name: "SYNTHETIC FUND A", market_value: "600.00" },
  { fund_name: "SYNTHETIC FUND B", market_value: "400.00" },
];

function mockImports({ balanceConflict = false, embedded = false, recognitionFailed = false } = {}) {
  const extracted = {
    document_type: "empf_account_page", client_name: "测试客户", account_number: "TEST-ONLY",
    scheme_name: "测试计划", as_of_date: "2026-06-30", total_balance: "1000.00", holdings: [],
  };
  const records = [1, 2].map((id) => ({
    id, original_name: `合成账单${id}.png`, mime_type: "image/png", status: "NEEDS_REVIEW",
    extracted, confidence: {}, warnings: [],
    ai_recognition: {
      status: recognitionFailed ? "FAILED" : "CONFLICT", values: { ...extracted, holdings, ...(balanceConflict ? { total_balance: "1100.00" } : {}) },
      uncorroborated: [{ field: "holdings", ocr_value: [], ai_value: holdings }],
      recognition_requires_human_review: true,
      validation_failures: ["total_equals_sum_of_holding_market_values"],
    },
  }));
  const submitted: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith("/confirm")) {
      submitted.push(JSON.parse(options?.body as string));
      return new Response(JSON.stringify({ detail: "测试停在提交边界" }), { status: 409 });
    }
    return new Response(JSON.stringify(url === "/api/statement-imports" ? records
      : url === "/api/ai-assistant/status" ? { status: "unavailable" } : []));
  }));
  render(<ImportsPage notify={vi.fn()} embedded={embedded} />);
  return submitted;
}

async function openFirst() {
  fireEvent.click(await screen.findByRole("button", { name: /合成账单1/ }));
  return screen.getByRole("button", { name: /生成历史结余/ }) as HTMLButtonElement;
}

test("历史持仓差异不阻断新确认，提交仅保存结余及身份资料", async () => {
  const submitted = mockImports();
  const save = await openFirst();
  expect(screen.getByText("本地OCR最高置信度：0%")).toBeTruthy();
  expect(screen.queryByText(/Infinity/)).toBeNull();
  expect(save.disabled).toBe(false);
  expect(screen.queryByText(/持仓/)).toBeNull();
  expect(screen.queryByRole("checkbox", { name: /我已人工核对完整清单/ })).toBeNull();
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0]).not.toHaveProperty("holdings");
  expect(submitted[0]).not.toHaveProperty("holdings_difference_reason");
  expect(submitted[0]).not.toHaveProperty("eligible_for_closing");
  expect(submitted[0]).toMatchObject({ total_balance: "1000.00", as_of_date: "2026-06-30" });
  expect(screen.getAllByText("2026年06月30日").length).toBeGreaterThan(0);
});

test("余额差异仍须独立人工确认，切换文件会清空确认", async () => {
  const submitted = mockImports({ balanceConflict: true });
  const save = await openFirst();
  fireEvent.click(save);
  expect(submitted).toHaveLength(0);
  fireEvent.click(screen.getByRole("checkbox", { name: /我已人工核对完整清单中的1项/ }));
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  await screen.findByText("测试停在提交边界");
  expect(submitted[0].ai_conflicts_reviewed).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: /合成账单2/ }));
  expect((screen.getByRole("checkbox", { name: /我已人工核对完整清单/ }) as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: /生成历史结余/ }));
  expect(submitted).toHaveLength(1);
});

test("嵌入模式保留上传及复核流程并隐藏独立页导航", async () => {
  mockImports({ embedded: true });
  await openFirst();
  expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  expect(screen.queryByRole("navigation")).toBeNull();
  expect(screen.getByRole("button", { name: "上传并本地识别" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "导入季度结余" })).toBeTruthy();
});

test("识别失败后仍能明确人工复核并提交本地候选", async () => {
  const submitted = mockImports({ recognitionFailed: true });
  const save = await openFirst();
  fireEvent.click(save);
  expect(submitted).toHaveLength(0);
  fireEvent.click(screen.getByRole("checkbox", { name: /我已人工核对完整清单/ }));
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0].ai_conflicts_reviewed).toBe(true);
});
