import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, isAqspAbortError, type AqspSnapshot } from "@/lib/api";
import { normalizeSnapshot } from "@/lib/daily-view";

export const AQSP_LIVE_REFRESH_INTERVAL_MS = 60_000;

/**
 * 所选交易日只放在 URL 的 `?date=` 上，不放组件状态。
 *
 * 为什么：日期是"你在看哪一天"的唯一事实。放 state 会带来三个后果 ——
 * 刷新后选择丢失、链接无法分享、前进/后退与内容不一致。
 * 金融场景下"我明明点了 2026-09-08，刷新后却看到今天"是必须避免的。
 */
export interface AqspSnapshotState {
  data: AqspSnapshot | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
  /** 当前所选交易日；空串表示"实时快照"。 */
  selectedDate: string;
  selectDate: (date: string) => void;
  /**
   * 已经请求了另一个交易日、但拿到的快照还是上一个日期的。
   *
   * 存在的意义：切换日期时**不能**把 data 置空（那样整页连同页头、刷新按钮、日期条一起消失），
   * 但也**绝不能**把上一个日期的候选显示在新日期标签下 —— 金融场景里那等于误导。
   * 所以保留页框、由页面只把内容区切成加载态，直到快照与所选日期对齐。
   */
  switching: boolean;
}

const AqspWorkspaceContext = createContext<AqspSnapshotState | null>(null);

export function AqspWorkspaceProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AqspSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const activeRequest = useRef<AbortController | null>(null);
  const requestSequence = useRef(0);

  // URL 是日期的唯一来源（见接口注释）；空串 = 实时快照。
  const searchParams = useSearchParams()[0];
  const { pathname, hash } = useLocation();
  const navigate = useNavigate();
  const selectedDate = searchParams.get("date") ?? "";
  /**
   * 只改 `date` 参数，其余（pathname 与 hash）原样保留。
   *
   * 不能用 `setSearchParams`：它会丢掉 hash，而今日页的页签（候选/证据/讨论）就活在 hash 上 ——
   * 一旦丢失，切日期会把用户弹回默认页签。
   */
  const selectDate = useCallback(
    (date: string) => {
      const next = new URLSearchParams(searchParams);
      if (date) next.set("date", date);
      else next.delete("date");
      const search = next.toString();
      navigate({ pathname, hash, search: search ? `?${search}` : "" }, { replace: false });
    },
    [hash, navigate, pathname, searchParams],
  );

  useEffect(() => {
    let active = true;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    activeRequest.current?.abort();
    const controller = new AbortController();
    activeRequest.current = controller;
    setLoading(true);
    setError(null);
    const snapshotRequest = api.aqspSnapshot(selectedDate || undefined, {
      signal: controller.signal,
    });
    snapshotRequest
      .then((snapshot) => {
        if (!active || requestSequence.current !== sequence) return;
        // 归一化只做这一次：下游页面/派生函数都拿到结构完整的对象，不必各自防御。
        setData(normalizeSnapshot(snapshot));
      })
      .catch((reason: unknown) => {
        if (!active || requestSequence.current !== sequence || isAqspAbortError(reason)) return;
        // A browser can retain a date that has been pruned from the server index.
        // Recover to the live snapshot instead of leaving the whole workspace in a 404 state.
        if (reason instanceof ApiError && reason.status === 404 && selectedDate) {
          selectDate("");
          return;
        }
        setError(reason instanceof ApiError ? reason.message : "研究快照加载失败");
      })
      .finally(() => {
        if (active && requestSequence.current === sequence) setLoading(false);
      });
    return () => {
      active = false;
      controller.abort();
      if (activeRequest.current === controller) activeRequest.current = null;
    };
  }, [reloadKey, selectedDate]);

  useEffect(() => {
    // Historical dates are static records; only the current snapshot polls.
    if (data?.meta?.historical) return;
    const timer = window.setInterval(() => setReloadKey((value) => value + 1), AQSP_LIVE_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [data?.meta?.historical]);

  const switching = Boolean(data && selectedDate && data.selected_date !== selectedDate);

  const value = useMemo<AqspSnapshotState>(
    () => ({
      data,
      loading,
      error,
      refresh: () => setReloadKey((value) => value + 1),
      selectedDate,
      // 刻意不在这里清空 data：清空会让整页闪烁，且刷新入口一起消失。
      // 只改 URL 上的 date 参数，由上面的 effect 负责重新取数。
      selectDate,
      switching,
    }),
    [data, error, loading, selectDate, selectedDate, switching],
  );

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
