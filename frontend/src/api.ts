import type { AiAssistantStatus, StatementImport } from "./types";

const SAFE_REQUEST_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
type ApiErrorPayload = { detail?: string | { message?: string } };

async function apiErrorMessage(response: Response, fallback: string): Promise<string> {
  const payload = await response.json() as ApiErrorPayload;
  if (typeof payload.detail === "string") return payload.detail;
  if (payload.detail && typeof payload.detail.message === "string") return payload.detail.message;
  return fallback;
}

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
    throw new Error(await apiErrorMessage(response, `请求失败 (${response.status})`));
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

export async function download(path: string, suggestedName: string, options: RequestInit = {}): Promise<void> {
  const response = await fetch(path, withFinancialSystemRequestHeader(options));
  if (!response.ok) {
    throw new Error(await apiErrorMessage(response, `下载失败 (${response.status})`));
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
