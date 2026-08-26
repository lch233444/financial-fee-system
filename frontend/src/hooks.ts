import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function useApiList<T>(path: string, refreshKey = 0) {
  const [data, setData] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api<T[]>(path));
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
  }, [reload, refreshKey]);

  return { data, loading, error, reload, setData };
}

export function todayIso() {
  return new Date().toISOString().slice(0, 10);
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

