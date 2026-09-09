import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export function useApiList<T>(path: string, refreshKey = 0) {
  const [data, setData] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    const id = ++requestId.current;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setLoading(true);
    setError("");
    try {
      const next = await api<T[]>(path, { signal: request.signal });
      if (id !== requestId.current) return false;
      setData(next);
      return true;
    } catch (err) {
      if (id !== requestId.current) return false;
      setError(err instanceof Error ? err.message : "读取失败");
      return false;
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setData([]);
    void reload();
    return () => {
      ++requestId.current;
      controller.current?.abort();
    };
  }, [reload, refreshKey]);

  return { data, loading, error, reload, setData };
}

export function todayIso(value = new Date()) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function usePagination<T>(items: T[], resetKey: string, pageSize = 20) {
  const [selection, setSelection] = useState({ key: resetKey, page: 1 });
  const page = Math.min(selection.key === resetKey ? selection.page : 1, Math.max(1, Math.ceil(items.length / pageSize)));
  return {
    page, pageSize, total: items.length,
    rows: items.slice((page - 1) * pageSize, page * pageSize),
    onChange: (next: number) => setSelection({ key: resetKey, page: next }),
  };
}

export function quarterDates(year: number, quarter: number): [string, string] {
  const values: Record<number, [string, string]> = {
    1: [`${year}-01-01`, `${year}-03-31`],
    2: [`${year}-04-01`, `${year}-06-30`],
    3: [`${year}-07-01`, `${year}-09-30`],
    4: [`${year}-10-01`, `${year}-12-31`],
  };
  return values[quarter];
}
