import { fireEvent } from "@testing-library/react";
import { vi } from "vitest";

// jsdom cannot decode images or measure a scroll viewport. Only these browser
// boundaries are supplied; zoom controls and source changes use real React.
export function mockImagePreviewLayout() {
  let width = 800;
  let height = 600;
  const callbacks = new Set<() => void>();
  vi.stubGlobal("ResizeObserver", class {
    notify: () => void;
    constructor(callback: () => void) { this.notify = callback; }
    observe(element: HTMLElement) {
      Object.defineProperties(element, {
        clientWidth: { configurable: true, get: () => width },
        clientHeight: { configurable: true, get: () => height },
      });
      callbacks.add(this.notify);
      this.notify();
    }
    disconnect() { callbacks.delete(this.notify); }
  });
  return (nextWidth: number, nextHeight: number) => {
    width = nextWidth;
    height = nextHeight;
    callbacks.forEach((callback) => callback());
  };
}

export function loadPreviewImage(image: HTMLElement, width = 1200, height = 1600) {
  Object.defineProperties(image, {
    naturalWidth: { configurable: true, value: width },
    naturalHeight: { configurable: true, value: height },
  });
  fireEvent.load(image);
}
