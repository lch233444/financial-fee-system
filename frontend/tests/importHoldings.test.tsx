import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ImportsPage from "../src/pages/ImportsPage";

const holdings = [
  { fund_name: "SYNTHETIC FUND A", market_value: "600.00" },
  { fund_name: "SYNTHETIC FUND B", market_value: "400.00" },
];

function mockImports() {
  const extracted = {
    document_type: "empf_account_page", client_name: "测试客户", account_number: "TEST-ONLY",
    scheme_name: "测试计划", as_of_date: "2026-06-30", total_balance: "1000.00", holdings: [],
  };
  const records = [1, 2].map((id) => ({
    id, original_name: `合成账单${id}.png`, mime_type: "image/png", status: "NEEDS_REVIEW",
    extracted, confidence: {}, warnings: [],
    ai_recognition: {
      status: "CONFLICT", values: { ...extracted, holdings },
      uncorroborated: [{ field: "holdings", ocr_value: [], ai_value: holdings }],
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
  render(<ImportsPage notify={vi.fn()} />);
  return submitted;
}

async function openFirst() {
  fireEvent.click(await screen.findByRole("button", { name: /合成账单1/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /我已人工核对完整清单/ }));
  return screen.getByRole("button", { name: /生成余额快照/ }) as HTMLButtonElement;
}

test("OCR为空时不自动采用AI，统一勾选不能保存空明细；主动选择后保存两项", async () => {
  const submitted = mockImports();
  const save = await openFirst();
  expect(screen.getByText("本地OCR最高置信度：0%")).toBeTruthy();
  expect(screen.queryByText(/Infinity/)).toBeNull();
  expect(save.disabled).toBe(true);
  expect(screen.getByText(/将保存：0项/)).toBeTruthy();
  fireEvent.submit(save.closest("form")!);
  expect(submitted).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "采用Sol持仓（2项）" }));
  expect(save.disabled).toBe(false);
  expect(screen.getByText(/将保存：2项/)).toBeTruthy();
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  expect(submitted[0].holdings).toEqual(holdings);
  expect(submitted[0].holdings_difference_reason).toBeNull();
});

test("保留较少明细须单独填写原因，切换来源或文件清空原因", async () => {
  const submitted = mockImports();
  const save = await openFirst();
  const reason = () => screen.getByRole("textbox", { name: /持仓差异原因/ }) as HTMLTextAreaElement;
  fireEvent.change(reason(), { target: { value: "   " } });
  expect(save.disabled).toBe(true);
  fireEvent.change(reason(), { target: { value: "原件无基金明细，AI误识别" } });
  fireEvent.click(save);
  await waitFor(() => expect(submitted).toHaveLength(1));
  await screen.findByText("测试停在提交边界");
  expect(submitted[0].holdings).toEqual([]);
  expect(submitted[0].holdings_difference_reason).toBe("原件无基金明细，AI误识别");
  fireEvent.click(screen.getByRole("button", { name: "采用Sol持仓（2项）" }));
  fireEvent.click(screen.getByRole("button", { name: "采用本地持仓（0项）" }));
  expect(reason().value).toBe("");
  fireEvent.change(reason(), { target: { value: "仅用于第一份原件" } });
  fireEvent.click(screen.getByRole("button", { name: /合成账单2/ }));
  expect(reason().value).toBe("");
  expect((screen.getByRole("button", { name: /生成余额快照/ }) as HTMLButtonElement).disabled).toBe(true);
});
