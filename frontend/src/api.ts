import type { AiAssistantStatus, StatementImport } from "./types";

const SAFE_REQUEST_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export function withFinancialSystemRequestHeader(options: RequestInit = {}): RequestInit {
  const headers = new Headers(options.headers);
  const method = (options.method ?? "GET").toUpperCase();
  if (!SAFE_REQUEST_METHODS.has(method)) {
    headers.set("X-Financial-System-Request", "1");
  }
  return { ...options, headers };
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const requestOptions = withFinancialSystemRequestHeader(options);
  const headers = new Headers(requestOptions.headers);
  if (requestOptions.body && !(requestOptions.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, { ...requestOptions, headers });
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") detail = payload.detail;
      else if (payload.detail && typeof payload.detail.message === "string") detail = payload.detail.message;
    } catch {
      // Keep fallback message.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function patchJson<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
}

export function getAiAssistantStatus(): Promise<AiAssistantStatus> {
  return api<AiAssistantStatus>("/api/ai-assistant/status");
}

export function startAiAssistantLogin(): Promise<Partial<AiAssistantStatus> & { status: string; login_started?: boolean; already_authenticated?: boolean }> {
  return postJson<Partial<AiAssistantStatus> & { status: string; login_started?: boolean; already_authenticated?: boolean }>("/api/ai-assistant/login", {});
}

export function logoutAiAssistant(): Promise<Partial<AiAssistantStatus> & { status: string; logged_out?: boolean }> {
  return postJson<Partial<AiAssistantStatus> & { status: string; logged_out?: boolean }>("/api/ai-assistant/logout", {});
}

export function recognizeStatementWithLuna(importId: number): Promise<StatementImport> {
  return postJson<StatementImport>(`/api/statement-imports/${importId}/ai-recognize`, {});
}

export type ShutdownResult = {
  status: "shutting_down";
  accepted: boolean;
  already_requested: boolean;
  message: string;
};

export function shutdownFinancialSystem(): Promise<ShutdownResult> {
  return postJson<ShutdownResult>("/api/shutdown", {});
}

export async function download(path: string, suggestedName: string): Promise<void> {
  const response = await fetch(path);
  if (!response.ok) {
    let message = `下载失败 (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // Keep fallback message.
    }
    throw new Error(message);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = suggestedName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
