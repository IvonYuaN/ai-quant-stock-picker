// 市场环境数据拉取。
//
// 每个源返回后立即发布；慢接口不阻塞指数和行业，刷新取消旧请求。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { DataSourceHealthRow } from "@/lib/api";
import { buildMarketView, type MarketView } from "@/lib/market-view";
import { loadMarketSections, MARKET_SECTIONS, MARKET_SECTION_LABELS, type MarketSection } from "@/lib/market-load";

/** 后端各接口缓存 5 分钟，前端同频刷新即可。 */
export const MARKET_REFRESH_INTERVAL_MS = 300_000;

export interface MarketDataState {
  view: MarketView | null;
  loading: boolean;
  /** 本次没取到的板块（接口失败），用于提示"可重试"；与"数据源没覆盖"不是一回事。 */
  failedSections: string[];
  pendingSections: MarketSection[];
  refresh: () => void;
}

export function useMarketData(): MarketDataState {
  const [view, setView] = useState<MarketView | null>(null);
  const [loading, setLoading] = useState(true);
  const [failedSections, setFailedSections] = useState<string[]>([]);
  const [pendingSections, setPendingSections] = useState<MarketSection[]>([...MARKET_SECTIONS]);
  const [reloadKey, setReloadKey] = useState(0);
  const requestSequence = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    void loadMarketSections({
      indices: (signal) => api.indices({ signal }),
      global: (signal) => api.globalIndices({ signal }),
      overview: (signal) => api.marketOverview({ signal }),
      emotion: (signal) => api.emotion({ signal }),
      turnover: (signal) => api.turnoverTop({ signal }),
      industry: (signal) => api.industry(20, { signal }),
    }, controller.signal, ({ input, pending, failed }) => {
      if (controller.signal.aborted || requestSequence.current !== sequence) return;
      setView(buildMarketView(input));
      setPendingSections(pending);
      setFailedSections(failed.map((key) => MARKET_SECTION_LABELS[key]));
      setLoading(pending.length > 0);
    });
    return () => controller.abort();
  }, [reloadKey]);

  useEffect(() => {
    const timer = window.setInterval(() => setReloadKey((value) => value + 1), MARKET_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const refresh = useCallback(() => setReloadKey((value) => value + 1), []);

  return useMemo(() => ({ view, loading, failedSections, pendingSections, refresh }), [view, loading, failedSections, pendingSections, refresh]);
}

/**
 * 数据源健康（后端进程内台账，重启清空）。
 *
 * 单独一个 hook 而不是并进 `useMarketData`：台账与行情数据是**两件事**，
 * 它失败不该让任何板块消失，行情成功也不代表源健康。
 *
 * 取不到就返回 `null`（不提示）——健康信息本身失败时，静默比误报好：
 * 编一条「数据源不可达」出来，比不提示更糟。
 */
export function useSourceHealth(): DataSourceHealthRow[] | null {
  const [sources, setSources] = useState<DataSourceHealthRow[] | null>(null);

  useEffect(() => {
    let active = true;
    const load = () => {
      api
        .health()
        .then((payload) => {
          if (!active) return;
          // `sources` 缺失（旧版后端）按「没有台账」处理，不编造空对象
          setSources(Array.isArray(payload?.sources) ? payload.sources : null);
        })
        .catch(() => {
          if (active) setSources(null);
        });
    };
    load();
    const timer = window.setInterval(load, MARKET_REFRESH_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  return sources;
}
