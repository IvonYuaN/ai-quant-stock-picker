// Each source settles independently; cancellation prevents stale partial results.
import type { TurnoverTop } from "./api";
import type { MarketInput } from "./market-view";

export const MARKET_SECTIONS = ["indices", "global", "overview", "emotion", "turnover", "industry"] as const;
export type MarketSection = (typeof MARKET_SECTIONS)[number];
export const MARKET_SECTION_LABELS: Readonly<Record<MarketSection, string>> = {
  indices: "大盘指数",
  global: "隔夜外围",
  overview: "涨跌家数 / 板块资金",
  emotion: "短线情绪",
  turnover: "成交额榜",
  industry: "行业涨跌榜",
};
export type MarketLoaders = Record<MarketSection, (signal: AbortSignal) => Promise<unknown>>;
export interface MarketLoadSnapshot {
  input: MarketInput;
  pending: MarketSection[];
  failed: MarketSection[];
}

/** allSettled only marks completion; every source publishes before its peers. */
export async function loadMarketSections(
  loaders: MarketLoaders,
  signal: AbortSignal,
  onUpdate: (snapshot: MarketLoadSnapshot) => void,
): Promise<void> {
  const input: MarketInput = {};
  const pending = new Set<MarketSection>(MARKET_SECTIONS);
  const failed = new Set<MarketSection>();
  const publish = () => {
    if (!signal.aborted) onUpdate({
      input: { ...input },
      pending: MARKET_SECTIONS.filter((key) => pending.has(key)),
      failed: MARKET_SECTIONS.filter((key) => failed.has(key)),
    });
  };
  publish();
  await Promise.allSettled(MARKET_SECTIONS.map(async (key) => {
    if (signal.aborted) return;
    try {
      const value = await loaders[key](signal);
      if (signal.aborted) return;
      if (key === "turnover") {
        const payload = value as TurnoverTop | null;
        input.turnover = payload?.stocks ?? [];
        input.turnoverUpdated = payload?.updated ?? "";
      } else {
        input[key] = value as never;
      }
    } catch {
      if (signal.aborted) return;
      failed.add(key);
    }
    pending.delete(key);
    publish();
  }));
}
