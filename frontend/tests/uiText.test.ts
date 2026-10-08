import { expect, test, vi } from "vitest";
import { api } from "../src/api";
import { paymentMethodLabel } from "../src/uiText";

test("API校验错误以中文字段提示，不改变提交字段、枚举及业务名称", async () => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, _options?: RequestInit) => new Response(JSON.stringify({ detail: [{ loc: ["body", "account_lines", 0, "original_hwm"], msg: "Input should be greater than or equal to 0" }] }), { status: 422 }));
  vi.stubGlobal("fetch", fetcher);
  const body = JSON.stringify({ name: "Company Name", method: "BANK_TRANSFER" });
  await expect(api("/api/example", { method: "POST", body })).rejects.toThrow("账户明细／第1项／期初高水位线：不得小于0");
  expect(fetcher.mock.calls[0][1]?.body).toBe(body);
  expect(paymentMethodLabel("BANK_TRANSFER")).toBe("银行转账");
  expect(paymentMethodLabel("Custom Payment Method")).toBe("Custom Payment Method");
});
