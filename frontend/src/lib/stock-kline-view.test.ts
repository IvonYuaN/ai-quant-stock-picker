import type { EChartsOption } from "echarts";
import { normalizeKlineBars, stockKlineOption, KLINE_RANGE_BARS } from "./stock-kline-view";

const rows = normalizeKlineBars([
  { date: "2026-03-03", open: 11, close: 12, high: 13, low: 10, vol: 300 },
  { date: "2026-03-01", open: 9, close: 8, high: 10, low: 7, volume: 200 },
  { date: "2026-03-02", open: 12, close: 10, high: 13, low: 9, vol: 100 },
  { date: "2026-03-03", open: 12, close: 13, high: 14, low: 11, vol: 400 },
  { date: "bad-date", open: 1, close: 1, high: 1, low: 1, vol: 1 },
  { date: "2026-03-04", open: 1, close: 4, high: 3, low: 0.5, vol: 1 },
]);
const option = stockKlineOption(rows, "dark");
const series = option.series as Array<{ type: string; data: unknown[] }>;

export const stockKlineViewContract = {
  sortsBarsByTradingDate: rows.map((row) => row.date).join("|") === "2026-03-01|2026-03-02|2026-03-03",
  keepsLastDuplicate: rows[2]?.close === 13 && rows[2]?.volume === 400,
  dropsMalformedAndInvalidBars: rows.length === 3,
  acceptsBothVolumeFields: rows.map((row) => row.volume).join("|") === "200|100|400",
  candlestickUsesOpenCloseLowHigh: JSON.stringify(series[0]?.data[0]) === JSON.stringify([9, 8, 7, 10]),
  includesVolumeSeries: series[1]?.type === "bar" && series[1].data.length === rows.length,
  hasRangeSlider: Array.isArray(option.dataZoom) && option.dataZoom.some((zoom) => (zoom as { type?: string }).type === "slider"),
  definesTickFlowTimeRanges: KLINE_RANGE_BARS["3m"] === 65 && KLINE_RANGE_BARS["6m"] === 130 && KLINE_RANGE_BARS["1y"] === 260,
  remainsAnEchartsOption: Boolean(option as EChartsOption),
};
