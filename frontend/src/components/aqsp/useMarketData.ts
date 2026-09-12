// 市场环境数据拉取。
//
// 六个接口用 allSettled 并发拉取：任一失败只影响它自己那一块，其余照常显示。
// 这是刻意的 —— 行情源（东财/百度/腾讯）各挂各的，串行 + 单点失败会让整页空白。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { buildMarketView, type MarketInput, type MarketView } from "@/lib/market-view";

/** 后端各接口缓存 5 分钟，前端同频刷新即可。 */
export const MARKET_REFRESH_INTERVAL_MS = 300_000;

export interface MarketDataState {
  view: MarketView | null;
  loading: boolean;
  /** 本次没取到的板块（接口失败），用于提示"可重试"；与"数据源没覆盖"不是一回事。 */
  failedSections: string[];
  refresh: () => void;
}

const SECTION_LABELS: Readonly<Record<string, string>> = {
  indices: "大盘指数",
  global: "隔夜外围",
  overview: "涨跌家数 / 板块资金",
  emotion: "短线情绪",
  turnover: "成交额榜",
  industry: "行业涨跌榜",
};

export function useMarketData(): MarketDataState {
  const [view, setView] = useState<MarketView | null>(null);
  const [loading, setLoading] = useState(true);
  const [failedSections, setFailedSections] = useState<string[]>([]);
  const [reloadKey, setReloadKey] = useState(0);
  const requestSequence = useRef(0);

  useEffect(() => {
    let active = true;
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);

    const jobs: Array<[keyof MarketInput, Promise<unknown>]> = [
      ["indices", api.indices()],
      ["global", api.globalIndices()],
      ["overview", api.marketOverview()],
      ["emotion", api.emotion()],
      ["turnover", api.turnoverTop()],
      ["industry", api.industry(20)],
    ];

    Promise.allSettled(jobs.map(([, promise]) => promise)).then((results) => {
      if (!active || requestSequence.current !== sequence) return;
      const input: MarketInput = {};
      const failed: string[] = [];
      results.forEach((result, index) => {
        const key = jobs[index][0];
        if (result.status === "fulfilled") {
          // turnover 接口返回 {stocks, updated}，取其中的列表
          if (key === "turnover") {
            input.turnover = (result.value as { stocks?: never[] } | null)?.stocks ?? [];
          } else {
            input[key] = result.value as never;
          }
        } else {
          failed.push(SECTION_LABELS[key] ?? key);
        }
      });
      setView(buildMarketView(input));
      setFailedSections(failed);
      setLoading(false);
    });

    return () => {
      active = false;
    };
  }, [reloadKey]);

  useEffect(() => {
    const timer = window.setInterval(() => setReloadKey((value) => value + 1), MARKET_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const refresh = useCallback(() => setReloadKey((value) => value + 1), []);

  return useMemo(() => ({ view, loading, failedSections, refresh }), [view, loading, failedSections, refresh]);
}
