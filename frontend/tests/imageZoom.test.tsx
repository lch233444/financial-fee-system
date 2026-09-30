import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import ZoomableImage from "../src/ZoomableImage";
import { loadPreviewImage, mockImagePreviewLayout } from "./imagePreviewFixtures";

test("凭证默认完整适配，缩放有边界，复位恢复全图与滚动位置，窗口变化重新适配", () => {
  const resize = mockImagePreviewLayout();
  render(<ZoomableImage src="blob:synthetic" alt="测试凭证" />);
  const larger = screen.getByRole("button", { name: "放大图片" }) as HTMLButtonElement;
  const smaller = screen.getByRole("button", { name: "缩小图片" }) as HTMLButtonElement;
  expect(larger.disabled).toBe(true);
  const image = screen.getByRole("img", { name: "测试凭证" });
  loadPreviewImage(image);
  expect(image.style.width).toBe("450px");
  expect(image.style.height).toBe("600px");
  for (let i = 0; i < 14; i += 1) fireEvent.click(larger);
  expect(screen.getByRole("status", { name: "图片缩放比例" }).textContent).toBe("400%");
  expect(larger.disabled).toBe(true);
  expect(image.style.width).toBe("1800px");
  const viewport = screen.getByRole("region", { name: "可滚动的凭证图片" });
  viewport.scrollLeft = 300;
  viewport.scrollTop = 900;
  fireEvent.click(screen.getByRole("button", { name: "复位图片（适应窗口）" }));
  expect(image.style.height).toBe("600px");
  expect(viewport.scrollLeft).toBe(0);
  expect(viewport.scrollTop).toBe(0);
  viewport.focus();
  expect(document.activeElement).toBe(viewport);
  for (let i = 0; i < 3; i += 1) fireEvent.click(smaller);
  expect(smaller.disabled).toBe(true);
  expect(screen.getByRole("status", { name: "图片缩放比例" }).textContent).toBe("50%");
  fireEvent.click(screen.getByRole("button", { name: "复位图片（适应窗口）" }));
  act(() => resize(300, 200));
  expect(image.style.width).toBe("150px");
  expect(image.style.height).toBe("200px");
});

test("图片解码失败显示错误并禁用缩放，不将空白预览当成成功", () => {
  mockImagePreviewLayout();
  render(<ZoomableImage src="blob:invalid-synthetic" alt="损坏的合成凭证" />);
  fireEvent.error(screen.getByRole("img"));
  expect(screen.getByRole("alert").textContent).toContain("图片无法显示");
  expect((screen.getByRole("button", { name: "放大图片" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("img")).toBeNull();
});
