import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type RefObject } from "react";

export function useDraggableDialog(dialog: RefObject<HTMLDialogElement | null>) {
  const offset = useRef({ x: 0, y: 0 });
  const drag = useRef<{ pointerId: number; x: number; y: number; handle: HTMLElement } | null>(null);
  const [dragging, setDragging] = useState(false);

  function move(dx: number, dy: number) {
    const element = dialog.current;
    if (!element?.open) return;
    const rect = element.getBoundingClientRect();
    const margin = 8;
    const x = Math.max(margin - rect.left, Math.min(dx, window.innerWidth - margin - rect.right));
    const y = Math.max(margin - rect.top, Math.min(dy, window.innerHeight - margin - rect.bottom));
    offset.current = { x: offset.current.x + x, y: offset.current.y + y };
    element.style.transform = `translate(${offset.current.x}px, ${offset.current.y}px)`;
  }

  function finish() {
    const current = drag.current;
    drag.current = null;
    setDragging(false);
    if (current?.handle.hasPointerCapture(current.pointerId)) current.handle.releasePointerCapture(current.pointerId);
  }

  function center() {
    finish();
    offset.current = { x: 0, y: 0 };
    if (dialog.current) dialog.current.style.transform = "";
  }

  useEffect(() => {
    const keepVisible = () => { finish(); move(0, 0); };
    const observer = new ResizeObserver(keepVisible);
    if (dialog.current) observer.observe(dialog.current);
    window.addEventListener("resize", keepVisible);
    window.addEventListener("blur", finish);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", keepVisible);
      window.removeEventListener("blur", finish);
    };
  }, []);

  return {
    center,
    headerProps: {
      className: `dialog-drag-handle${dragging ? " dragging" : ""}`,
      tabIndex: 0,
      "aria-label": "移动预览窗口：按住标题栏拖动，或使用方向键；Home键居中",
      onPointerDown(event: PointerEvent<HTMLElement>) {
        if (event.button !== 0 || drag.current || (event.target as HTMLElement).closest("button, a, input, select, textarea")) return;
        event.preventDefault();
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, handle: event.currentTarget };
        setDragging(true);
      },
      onPointerMove(event: PointerEvent<HTMLElement>) {
        const current = drag.current;
        if (!current || current.pointerId !== event.pointerId) return;
        if (!(event.buttons & 1)) { finish(); return; }
        move(event.clientX - current.x, event.clientY - current.y);
        current.x = event.clientX; current.y = event.clientY;
      },
      onPointerUp: finish,
      onPointerCancel: finish,
      onLostPointerCapture: finish,
      onKeyDown(event: KeyboardEvent<HTMLElement>) {
        if (event.target !== event.currentTarget) return;
        const step = event.shiftKey ? 40 : 10;
        const delta: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
        if (event.key === "Home") { event.preventDefault(); center(); }
        else if (delta[event.key]) { event.preventDefault(); move(...delta[event.key]); }
      },
    },
  };
}
