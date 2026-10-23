export type Dataset = { id: string; name: string };
export type DatasetState = { datasets: Dataset[]; active_id: string; token: string };
let state: DatasetState | null = null;
let frozen = false;
let stale = false;
let requests = 0;
let submission: HTMLFormElement | null = null;
const listeners = new Set<() => void>();
type Control = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;
const originals = new Map<Control, string>();
const dirty = new Map<Control, string>();
const value = (element: Control) => element instanceof HTMLInputElement && ["checkbox", "radio"].includes(element.type)
  ? String(element.checked) : element instanceof HTMLInputElement && element.type === "file"
    ? Array.from(element.files || []).map(file => `${file.name}:${file.size}:${file.lastModified}`).join("|") : element.value;

export function datasetState() { return state; }
export function datasetLocked() { return frozen || stale; }
export function datasetBusy() { return requests > 0; }
export function datasetIsStale() { return stale; }
export function subscribeDataset(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
function publish() { listeners.forEach(listener => listener()); }
export function setDatasetState(next: DatasetState) { state = next; publish(); }
export function markDatasetStale() { stale = true; publish(); }
export function datasetToken() { return state?.token; }
export function datasetUrl(path: string) {
  return state ? `${path}${path.includes("?") ? "&" : "?"}dataset_token=${encodeURIComponent(state.token)}` : path;
}

export function hasUnsavedDatasetChanges() {
  for (const [element, saved] of dirty) {
    if (!element.isConnected || value(element) === saved) dirty.delete(element);
  }
  return dirty.size > 0;
}

export function trackDatasetForms() {
  function controls() {
    document.querySelectorAll<Control>("input,select,textarea").forEach(element => {
      if (!originals.has(element)) originals.set(element, value(element));
    });
    for (const element of originals.keys()) if (!element.isConnected) { originals.delete(element); dirty.delete(element); }
  }
  controls();
  const observer = new MutationObserver(controls);
  observer.observe(document.body, { childList: true, subtree: true });
  function change(event: Event) {
    const element = event.target as Control;
    if (!element.matches?.("input,select,textarea") || element.closest("[data-dataset-control]")) return;
    const initial = originals.get(element) ?? "";
    if (value(element) !== initial) dirty.set(element, initial); else dirty.delete(element);
  }
  function submit(event: Event) {
    submission = event.target as HTMLFormElement;
    queueMicrotask(() => { submission = null; });
  }
  function reset(event: Event) {
    const form = event.target as HTMLFormElement;
    queueMicrotask(() => { for (const element of originals.keys()) if (form.contains(element)) {
      dirty.delete(element); originals.set(element, value(element));
    } });
  }
  function leave(event: BeforeUnloadEvent) {
    if (hasUnsavedDatasetChanges() && !stale) { event.preventDefault(); event.returnValue = ""; }
  }
  document.addEventListener("input", change, true);
  document.addEventListener("change", change, true);
  document.addEventListener("submit", submit, true);
  document.addEventListener("reset", reset, true);
  window.addEventListener("beforeunload", leave);
  return () => {
    observer.disconnect(); originals.clear(); dirty.clear();
    document.removeEventListener("input", change, true); document.removeEventListener("change", change, true);
    document.removeEventListener("submit", submit, true); document.removeEventListener("reset", reset, true);
    window.removeEventListener("beforeunload", leave);
  };
}

export async function datasetFetch(path: string, options: RequestInit): Promise<Response> {
  if (datasetLocked() && !path.startsWith("/api/datasets")) throw new Error("数据包正在切换或已变化，请刷新后操作");
  const management = path.startsWith("/api/datasets");
  const form = submission;
  const saved = new Map<Control, string>();
  if (form && options.method && !["GET", "HEAD"].includes(options.method.toUpperCase())) {
    form.querySelectorAll<Control>("input,select,textarea").forEach(element => saved.set(element, value(element)));
  }
  if (!management) { requests += 1; publish(); }
  try {
    const response = await fetch(path, options);
    if (response.status === 409) {
      const error = await response.clone().json().catch(() => null);
      if (error?.dataset_changed) markDatasetStale();
    }
    if (response.ok) for (const [element, snapshot] of saved) if (value(element) === snapshot) {
      dirty.delete(element); originals.set(element, snapshot);
    }
    return response;
  } finally { if (!management) { requests -= 1; publish(); } }
}

export function handleDatasetMessage(socket: WebSocket, text: string) {
  let message;
  try { message = JSON.parse(text); } catch { return; }
  if (message.type === "dataset-prepare") {
    frozen = true;
    // Freeze synchronously before acknowledging: no new edit can race switching.
    document.querySelector(".app-shell")?.setAttribute("inert", "");
    publish();
    socket.send(JSON.stringify({ type: "dataset-ready", nonce: message.nonce,
      ready: !stale && requests === 0 && !hasUnsavedDatasetChanges() }));
  } else if (message.type === "dataset-finish") {
    frozen = false;
    if (message.token !== state?.token) stale = true;
    if (!stale) document.querySelector(".app-shell")?.removeAttribute("inert");
    publish();
  }
}
