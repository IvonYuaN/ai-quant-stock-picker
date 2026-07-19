import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ApiError, type AqspSnapshot } from "@/lib/api";

export interface AqspSnapshotState {
  data: AqspSnapshot | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  selectedDate: string;
  selectDate: (date: string) => void;
}

const AqspWorkspaceContext = createContext<AqspSnapshotState | null>(null);

function readSelectedDate(): string {
  try {
    return localStorage.getItem("aqsp-selected-date") || "";
  } catch {
    return "";
  }
}

export function AqspWorkspaceProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AqspSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState(readSelectedDate);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    const snapshotRequest = api.aqspSnapshot(selectedDate || undefined);
    snapshotRequest
      .then((snapshot) => {
        if (!active) return;
        setData(snapshot);
        if (!selectedDate && snapshot.selected_date) {
          setSelectedDate(snapshot.selected_date);
          try {
            localStorage.setItem("aqsp-selected-date", snapshot.selected_date);
          } catch {
            // Storage is optional; the in-memory selection remains usable.
          }
        }
      })
      .catch((reason: unknown) => {
        if (!active) return;
        // A browser can retain a date that has been pruned from the server index.
        // Recover to the live snapshot instead of leaving the whole workspace in a 404 state.
        if (reason instanceof ApiError && reason.status === 404 && selectedDate) {
          setSelectedDate("");
          try {
            localStorage.removeItem("aqsp-selected-date");
          } catch {
            // Storage is optional; the next request still uses the live snapshot.
          }
          return;
        }
        setError(reason instanceof ApiError ? reason.message : "研究快照加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reloadKey, selectedDate]);

  const value = useMemo<AqspSnapshotState>(() => ({
    data,
    loading,
    error,
    refresh: () => setReloadKey((value) => value + 1),
    selectedDate,
    selectDate: (date: string) => {
      if (date !== selectedDate) setData(null);
      setSelectedDate(date);
      try {
        localStorage.setItem("aqsp-selected-date", date);
      } catch {
        // Storage is optional; the in-memory selection remains usable.
      }
    },
  }), [data, error, loading, selectedDate]);

  return <AqspWorkspaceContext.Provider value={value}>{children}</AqspWorkspaceContext.Provider>;
}

export function useWorkspaceSnapshot(): AqspSnapshotState {
  const value = useContext(AqspWorkspaceContext);
  if (!value) throw new Error("useWorkspaceSnapshot 必须在 AqspWorkspaceProvider 内使用");
  return value;
}

export function useAqspSnapshot(): AqspSnapshotState {
  return useWorkspaceSnapshot();
}

export function isAqspSnapshotStale(snapshot: AqspSnapshot): boolean {
  if (snapshot.meta) return snapshot.meta.historical || snapshot.meta.stale;
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
