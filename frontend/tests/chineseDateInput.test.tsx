import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import ChineseDateInput from "../src/ChineseDateInput";

test("中文日期保留ISO提交，拒绝不存在的日期及超出范围的日期", () => {
  const change = vi.fn();
  render(<form aria-label="日期表单"><label>管理日期<ChineseDateInput aria-label="管理日期" name="day" required min="2024-01-01" max="2026-12-31" onValueChange={change} /></label></form>);
  const input = screen.getByRole("textbox", { name: "管理日期" }) as HTMLInputElement;
  const form = screen.getByRole("form") as HTMLFormElement;
  expect(input.placeholder).toBe("年/月/日");
  expect(form.checkValidity()).toBe(false);
  fireEvent.change(input, { target: { value: "2024/2/29" } });
  fireEvent.blur(input);
  expect(input.value).toBe("2024年02月29日");
  expect(new FormData(form).get("day")).toBe("2024-02-29");
  expect(change).toHaveBeenLastCalledWith("2024-02-29");
  expect(form.checkValidity()).toBe(true);
  fireEvent.change(input, { target: { value: "2025年02月29日" } });
  expect(form.checkValidity()).toBe(false);
  fireEvent.change(input, { target: { value: "2027/01/01" } });
  expect(form.checkValidity()).toBe(false);
  fireEvent.change(input, { target: { value: "" } });
  expect(new FormData(form).get("day")).toBe("");
  expect(form.checkValidity()).toBe(false);
});

test("日历选择、外部选中记录和表单重置同步中文显示", () => {
  const { rerender } = render(<form><label>结余日期<ChineseDateInput name="day" defaultValue="2026-01-01" /></label></form>);
  fireEvent.change(screen.getByLabelText("打开日历：日期"), { target: { value: "2026-10-08" } });
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("2026年10月08日");
  fireEvent.reset(screen.getByRole("textbox").closest("form")!);
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("2026年01月01日");
  rerender(<form><ChineseDateInput value="2026-09-30" /></form>);
  expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("2026年09月30日");
});
