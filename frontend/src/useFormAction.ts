import { type FormEvent, useRef, useState } from "react";

type FormAction = {
  save: (data: FormData) => Promise<unknown>;
  refresh: () => Promise<boolean | boolean[]>;
  message: string;
  afterSave?: () => void;
  reset?: boolean;
};

/** Capture the form before awaiting; distinguish a saved record from a failed refresh. */
export function useFormAction(setError: (message: string) => void, notify: (message: string) => void) {
  const inFlight = useRef(false);
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>, action: FormAction) {
    event.preventDefault();
    if (inFlight.current) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    inFlight.current = true;
    setPending(true);
    setError("");
    let saved = false;
    try {
      await action.save(data);
      saved = true;
      if (action.reset !== false && form.isConnected) form.reset();
      action.afterSave?.();
      const refreshed = await action.refresh();
      if (refreshed === false || (Array.isArray(refreshed) && refreshed.includes(false))) {
        setError(`${action.message}，但列表刷新失败；请重新载入页面核对，不要重复提交。`);
      }
      notify(action.message);
    } catch (error) {
      const detail = error instanceof Error ? error.message : "请求失败";
      setError(saved ? `${action.message}，但页面更新失败：${detail}；请重新载入页面核对，不要重复提交。` : detail);
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return { pending, submit };
}
