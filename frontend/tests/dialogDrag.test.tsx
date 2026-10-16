import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import DocumentPreviewDialog from "../src/DocumentPreviewDialog";

function setup() {
  vi.stubGlobal("innerWidth", 1000); vi.stubGlobal("innerHeight", 800);
  vi.stubGlobal("PointerEvent", class extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) { super(type, init); this.pointerId = init.pointerId ?? 1; }
  });
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value() { this.open = false; } });
  const close = vi.fn();
  const view = render(<DocumentPreviewDialog document={{ path: "/synthetic", title: "测试原件", filename: "test.png" }} onClose={close} />);
  const dialog = screen.getByRole("dialog") as HTMLDialogElement;
  const header = screen.getByLabelText(/移动预览窗口/);
  let captured = false;
  header.setPointerCapture = vi.fn(() => { captured = true; });
  header.hasPointerCapture = () => captured;
  header.releasePointerCapture = vi.fn(() => { captured = false; });
  vi.spyOn(dialog, "getBoundingClientRect").mockImplementation(() => {
    const [x, y] = dialog.style.transform.match(/-?\d+/g)?.map(Number) ?? [0, 0];
    return { left: 100 + x, top: 80 + y, right: 700 + x, bottom: 480 + y, width: 600, height: 400 } as DOMRect;
  });
  return { dialog, header, close, view };
}

test("左键标题栏拖动整个弹窗，边界内停止，释放及取消后不再移动", () => {
  const { dialog, header } = setup();
  fireEvent.pointerDown(header, { button: 2, clientX: 200, clientY: 100 });
  fireEvent.pointerMove(header, { buttons: 1, clientX: 300, clientY: 180 });
  expect(dialog.style.transform).toBe("");
  fireEvent.pointerDown(header, { button: 0, clientX: 200, clientY: 100 });
  fireEvent.pointerMove(header, { buttons: 1, clientX: 300, clientY: 180 });
  expect(dialog.style.transform).toBe("translate(100px, 80px)");
  fireEvent.pointerMove(header, { buttons: 1, clientX: 3000, clientY: 3000 });
  expect(dialog.getBoundingClientRect().right).toBe(992);
  expect(dialog.getBoundingClientRect().bottom).toBe(792);
  fireEvent.pointerUp(header);
  const previous = dialog.style.transform;
  fireEvent.pointerMove(header, { buttons: 1, clientX: 0, clientY: 0 });
  expect(dialog.style.transform).toBe(previous);
  fireEvent.pointerDown(header, { button: 0, clientX: 300, clientY: 200 });
  fireEvent.pointerCancel(header);
  fireEvent.pointerMove(header, { buttons: 1, clientX: 0, clientY: 0 });
  expect(dialog.style.transform).toBe(previous);
});

test("标题栏按钮不启动拖动，窗口居中、键盘和缩小窗口保持可操作", () => {
  const { dialog, header, close } = setup();
  fireEvent.keyDown(header, { key: "ArrowRight" });
  expect(dialog.style.transform).toBe("translate(10px, 0px)");
  fireEvent.click(screen.getByRole("button", { name: "窗口居中" }));
  expect(dialog.style.transform).toBe("");
  fireEvent.keyDown(header, { key: "ArrowRight", shiftKey: true });
  vi.stubGlobal("innerWidth", 680);
  fireEvent.resize(window);
  expect(dialog.getBoundingClientRect().right).toBe(672);
  fireEvent.keyDown(header, { key: "Home" });
  expect(dialog.style.transform).toBe("");
  const button = screen.getByRole("button", { name: "关闭预览" });
  fireEvent.pointerDown(button, { button: 0, clientX: 600, clientY: 100 });
  expect(header.setPointerCapture).not.toHaveBeenCalled();
  fireEvent.click(button);
  expect(close).toHaveBeenCalledOnce();
});
