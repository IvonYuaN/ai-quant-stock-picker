import type { StockPriceLevel } from "@/lib/api";

export const STOCK_LEVEL_LABELS: Readonly<Record<string, string>> = {
  rolling_range: "滚动区间支撑/压力",
  previous_session: "前高/前低",
  classic_pivot: "经典枢轴点",
  volume_profile: "成交密集区",
  keltner_short: "Keltner 短期通道",
  keltner_mid: "Keltner 中期通道",
  keltner_long: "Keltner 长期通道",
  atr_channel: "ATR 波动通道",
  gap_levels: "缺口位",
  fibonacci: "斐波那契回撤",
  round_numbers: "整数关口",
};

export function availableStockLevels(levels: readonly StockPriceLevel[]): StockPriceLevel[] {
  return levels.filter((level) => level.status === "available" && level.levels.length > 0);
}

export function unavailableStockLevels(levels: readonly StockPriceLevel[]): StockPriceLevel[] {
  return levels.filter((level) => level.status !== "available" || level.levels.length === 0);
}
