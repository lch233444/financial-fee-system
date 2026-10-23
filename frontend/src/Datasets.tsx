import { type FormEvent, type ReactNode, useEffect, useState, useSyncExternalStore } from "react";
import { api, postJson } from "./api";
import { ErrorBanner, Field, Panel } from "./components";
import { type DatasetState, datasetState, setDatasetState, subscribeDataset, datasetLocked, datasetIsStale,
  trackDatasetForms, hasUnsavedDatasetChanges, datasetBusy } from "./datasetSession";

export function DatasetBoundary({ children }: { children: ReactNode }) {
  const state = useSyncExternalStore(subscribeDataset, datasetState);
  const locked = useSyncExternalStore(subscribeDataset, datasetLocked);
  const stale = useSyncExternalStore(subscribeDataset, datasetIsStale);
  const [error, setError] = useState("");
  useEffect(() => {
    api<DatasetState>("/api/datasets").then(setDatasetState).catch(err => setError(String(err)));
    return trackDatasetForms();
  }, []);
  if (error) return <div className="content"><ErrorBanner message={error} /><button onClick={() => window.location.reload()}>重新连接</button></div>;
  if (!state) return <p role="status">正在读取当前数据包…</p>;
  return <><div inert={locked}>{children}</div>{locked && <div className="dataset-overlay" role="alertdialog" aria-modal="true" aria-label="数据包切换"><section>
    <h2>{stale ? "数据包已切换" : "正在核对所有打开的页面"}</h2>
    <p>{stale ? "当前页面属于切换前的数据包，请刷新后继续。" : "请稍候，系统正在检查未保存资料及进行中的操作。"}</p>
    {stale && <button className="primary" autoFocus onClick={() => { window.location.hash = "/dashboard"; window.location.reload(); }}>刷新并打开当前数据包</button>}
  </section></div>}</>;
}

export function DatasetSelector() {
  const state = useSyncExternalStore(subscribeDataset, datasetState);
  const processing = useSyncExternalStore(subscribeDataset, datasetBusy);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!state) return null;
  async function switchTo(id: string) {
    if (hasUnsavedDatasetChanges()) { setError("本页有未保存资料，请先保存；不需要保留的修改可刷新页面放弃。"); return; }
    setBusy(true); setError("");
    try {
      await postJson("/api/datasets/switch", { id });
      window.location.hash = "/dashboard";
      window.location.reload();
    } catch (err) { setError(err instanceof Error ? err.message : "切换失败"); }
    finally { setBusy(false); }
  }
  return <div className="dataset-selector" data-dataset-control>
    <label>当前数据包<select aria-label="当前数据包" value={state.active_id} disabled={busy || processing} onChange={event => void switchTo(event.target.value)}>
      {state.datasets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select></label>{busy && <span role="status">正在切换…</span>}{error && <span className="dataset-error" role="alert">{error}</span>}
  </div>;
}

export function DatasetManagement({ notify }: { notify: (message: string) => void }) {
  const state = useSyncExternalStore(subscribeDataset, datasetState);
  const processing = useSyncExternalStore(subscribeDataset, datasetBusy);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!state) return null;
  async function create(event: FormEvent<HTMLFormElement>, importing: boolean) {
    event.preventDefault(); const form = event.currentTarget;
    setBusy(true); setError("");
    try {
      const data = new FormData(form);
      if (importing) await api("/api/datasets/import", { method: "POST", body: data });
      else await postJson("/api/datasets", { name: data.get("name") });
      setDatasetState(await api<DatasetState>("/api/datasets"));
      form.reset(); notify("数据包已建立，可在顶部选择切换；当前数据包未被替换。");
    } catch (err) { setError(err instanceof Error ? err.message : "建立数据包失败"); }
    finally { setBusy(false); }
  }
  return <Panel title="独立数据包" subtitle="客户、账户、账单、收款、编号及业务文件各自独立">
    {error && <ErrorBanner message={error} />}
    <p>当前：<strong>{state.datasets.find(item => item.id === state.active_id)?.name}</strong>。新建或导入不会覆盖任何已有数据包。</p>
    <form className="inline-form" onSubmit={event => void create(event, false)}>
      <Field label="新数据包名称"><input name="name" required maxLength={60} disabled={busy} /></Field>
      <button className="primary" disabled={busy || processing}>{busy ? "正在处理…" : "新建空白数据包"}</button>
    </form>
    <details><summary>从已有完整ZIP建立另一套数据</summary>
      <p>支持当前版本及1.1.4、1.1.5、1.1.6完整数据包。旧包保留原件，经校验、升级及重新验证后加入列表；更高版本须先升级程序。</p>
      <form className="inline-form" onSubmit={event => void create(event, true)}>
        <Field label="导入数据包名称"><input name="name" required maxLength={60} disabled={busy} /></Field>
        <Field label="作为独立数据包导入ZIP"><input name="file" type="file" accept=".zip" required disabled={busy} /></Field>
        <button className="primary" disabled={busy || processing}>{busy ? "正在校验及升级…" : "导入为独立数据包"}</button>
      </form>
    </details>
    {processing && <p role="status">系统正在处理请求，完成后可管理或切换数据包。</p>}
  </Panel>;
}
