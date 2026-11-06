import type { EChartsOption } from "echarts";
import type { KlineBar } from "@/lib/api";
import { chartPalette, DOWN_COLOR, UP_COLOR } from "@/lib/chart-options";
import type { ThemeMode } from "@/lib/theme-mode";

export type KlineRange = "3m" | "6m" | "1y";

export interface StockKlineRow {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number | null;
}

export const KLINE_RANGE_BARS: Readonly<Record<KlineRange, number>> = {
  "3m": 65,
  "6m": 130,
  "1y": 260,
};

export function normalizeKlineBars(bars: readonly KlineBar[]): StockKlineRow[] {
  const byDate = new Map<string, StockKlineRow>();
  for (const bar of bars) {
    const date = String(bar.date || bar.datetime || "").slice(0, 10);
    const prices = [bar.open, bar.close, bar.high, bar.low].map(Number);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || prices.some((value) => !Number.isFinite(value) || value <= 0)) continue;
    const [open, close, high, low] = prices;
    if (low > Math.min(open, close) || high < Math.max(open, close) || low > high) continue;
    const rawVolume = bar.vol ?? bar.volume;
    const volume = rawVolume == null || !Number.isFinite(Number(rawVolume)) ? null : Number(rawVolume);
    byDate.set(date, { date, open, close, high, low, volume });
  }
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

export function stockKlineOption(rows: readonly StockKlineRow[], mode: ThemeMode): EChartsOption {
  const palette = chartPalette(mode);
  const dates = rows.map((row) => row.date);
  return {
    animation: false,
    grid: [
      { left: 52, right: 18, top: 12, height: "62%" },
      { left: 52, right: 18, top: "76%", height: "14%" },
    ],
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    xAxis: [
      { type: "category", data: dates, boundaryGap: true, axisLabel: { show: false }, axisLine: { lineStyle: { color: palette.axisLine } } },
      { type: "category", gridIndex: 1, data: dates, boundaryGap: true, axisLabel: { color: palette.text, fontSize: 10 }, axisLine: { lineStyle: { color: palette.axisLine } } },
    ],
    yAxis: [
      { scale: true, axisLabel: { color: palette.text, fontSize: 10 }, splitLine: { lineStyle: { color: palette.splitLine } } },
      { gridIndex: 1, scale: true, axisLabel: { color: palette.text, fontSize: 9 }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: "slider", xAxisIndex: [0, 1], start: 0, end: 100, bottom: 0, height: 16, showDetail: false, borderColor: "transparent", backgroundColor: palette.splitLine, fillerColor: "rgba(229,72,77,0.14)" },
    ],
    series: [
      {
        name: "日K",
        type: "candlestick",
        data: rows.map((row) => [row.open, row.close, row.low, row.high]),
        itemStyle: { color: UP_COLOR, color0: DOWN_COLOR, borderColor: UP_COLOR, borderColor0: DOWN_COLOR },
      },
      {
        name: "成交量",
        type: "bar",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: rows.map((row) => ({
          value: row.volume,
          itemStyle: { color: row.close >= row.open ? UP_COLOR : DOWN_COLOR },
        })),
      },
    ],
  };
}
