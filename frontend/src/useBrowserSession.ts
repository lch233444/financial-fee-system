import { useEffect } from "react";

export function useBrowserSession() {
  useEffect(() => {
    let socket: WebSocket | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let active = true;

    function connect() {
      if (!active || socket) return;
      const url = new URL("/api/browser-session", window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const current = new WebSocket(url);
      socket = current;
      current.addEventListener("close", () => {
        if (socket !== current) return;
        socket = undefined;
        if (active) retry = setTimeout(connect, 1000);
      });
    }

    function release() {
      active = false;
      clearTimeout(retry);
      const current = socket;
      socket = undefined;
      current?.close();
    }

    function resume() {
      active = true;
      connect();
    }

    connect();
    window.addEventListener("pagehide", release);
    window.addEventListener("pageshow", resume);
    return () => {
      window.removeEventListener("pagehide", release);
      window.removeEventListener("pageshow", resume);
      release();
    };
  }, []);
}
