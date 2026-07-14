import { useEffect, useState } from "react";
import { api, ApiError, type AqspSnapshot } from "@/lib/api";

export interface AqspSnapshotState {
  data: AqspSnapshot | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}

export function useAqspSnapshot(): AqspSnapshotState {
  const [data, setData] = useState<AqspSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api.aqspSnapshot()
      .then((snapshot) => {
        if (active) setData(snapshot);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof ApiError ? reason.message : "AQSP 快照加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return { data, loading, error, refresh: () => setReloadKey((value) => value + 1) };
}

export function isAqspSnapshotStale(snapshot: AqspSnapshot): boolean {
  if (!snapshot.stale_after) return true;
  const deadline = Date.parse(snapshot.stale_after);
  return Number.isNaN(deadline) || Date.now() >= deadline;
}

export function formatAqspTime(value: string): string {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return value;
  return new Date(timestamp).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
