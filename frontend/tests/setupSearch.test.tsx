import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import SetupPage from "../src/pages/SetupPage";

test("基础资料搜索的结果和空状态一致，切换类别时清空上一个搜索", async () => {
  const records: Record<string, unknown[]> = {
    "/api/companies": [
      { id: 1, code: "AC", name: "Alpha Company", payment_terms_days: 14 },
      { id: 2, code: "BC", name: "Beta Company", payment_terms_days: 30 },
    ],
    "/api/fcs": [{ id: 1, name: "Tony Wu" }, { id: 2, name: "Alice Lee" }],
    "/api/platforms": [
      { id: 1, code: "PA", name: "Alpha Platform", trustee: null },
      { id: 2, code: "PB", name: "Beta Platform", trustee: null },
    ],
    "/api/fee-plans": [
      { id: 1, name: "Alpha Plan", fee_rate_percent: "20" },
      { id: 2, name: "Beta Plan", fee_rate_percent: "30" },
    ],
  };
  vi.stubGlobal("fetch", vi.fn(async (path: string) => new Response(JSON.stringify(records[path] ?? []))));
  render(<SetupPage notify={vi.fn()} />);

  const company = within(screen.getByRole("region", { name: "现有Company" }));
  await company.findByText("Alpha Company");
  fireEvent.change(company.getByRole("searchbox", { name: "搜索Company" }), { target: { value: "BC" } });
  expect(company.queryByText("Alpha Company")).toBeNull();
  expect(company.getByText("Beta Company")).toBeTruthy();
  fireEvent.change(company.getByRole("searchbox", { name: "搜索Company" }), { target: { value: "missing" } });
  expect(company.getByText("没有匹配项")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "中介人 FC", exact: true }));
  const fc = within(screen.getByRole("region", { name: "现有FC" }));
  await fc.findByText("Tony Wu");
  expect((fc.getByRole("searchbox", { name: "搜索FC" }) as HTMLInputElement).value).toBe("");
  fireEvent.change(fc.getByRole("searchbox", { name: "搜索FC" }), { target: { value: "Alice" } });
  expect(fc.queryByText("Tony Wu")).toBeNull();
  expect(fc.getByText("Alice Lee")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "投资平台 Platform", exact: true }));
  const platform = within(screen.getByRole("region", { name: "现有Platform" }));
  await platform.findByText("Alpha Platform");
  expect((platform.getByRole("searchbox", { name: "搜索Platform" }) as HTMLInputElement).value).toBe("");
  fireEvent.change(platform.getByRole("searchbox", { name: "搜索Platform" }), { target: { value: "PB" } });
  expect(platform.queryByText("Alpha Platform")).toBeNull();
  expect(platform.getByText("Beta Platform")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "收费计划 Fee Plan", exact: true }));
  const plan = within(screen.getByRole("region", { name: "现有Fee Plan" }));
  await plan.findByText("Alpha Plan");
  expect((plan.getByRole("searchbox", { name: "搜索Fee Plan" }) as HTMLInputElement).value).toBe("");
  fireEvent.change(plan.getByRole("searchbox", { name: "搜索Fee Plan" }), { target: { value: "Beta" } });
  expect(plan.queryByText("Alpha Plan")).toBeNull();
  expect(plan.getByText("Beta Plan")).toBeTruthy();
  fireEvent.change(plan.getByRole("searchbox", { name: "搜索Fee Plan" }), { target: { value: "missing" } });
  await waitFor(() => expect(plan.getByText("没有匹配项")).toBeTruthy());
});
