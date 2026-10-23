import type { EChartsOption } from "echarts";
import type { MacdChartEvidence } from "./api";
import { chartPalette } from "./chart-options";
import { asRecord } from "./safe";
import type { ThemeMode } from "./theme-mode";

function validDay(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

/** Reject an incomplete snapshot as a whole; never replace missing observations with zero. */
export function normalizeMacdChart(raw: unknown, checkedAt: string): MacdChartEvidence | undefined {
  const row = asRecord(raw);
  const stamp = new Date(checkedAt);
  if (row.kind !== "macd_dead_cross" || row.price_adjustment !== "none" ||
      row.fast !== 12 || row.slow !== 26 || row.signal !== 9 ||
      !validDay(row.trigger_date) || !Number.isFinite(stamp.getTime()) ||
      typeof row.source !== "string" || !row.source.trim() ||
      !Array.isArray(row.points) || row.points.length < 2 || row.points.length > 60) return undefined;
  const checkedDay = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(stamp);
  if (row.trigger_date > checkedDay) return undefined;
  const points: MacdChartEvidence["points"] = [];
  for (const value of row.points) {
    const p = asRecord(value);
    if (!validDay(p.date) || (points.length > 0 && p.date <= points[points.length - 1].date) ||
        typeof p.close !== "number" || !Number.isFinite(p.close) || p.close <= 0 ||
        typeof p.dif !== "number" || !Number.isFinite(p.dif) ||
        typeof p.dea !== "number" || !Number.isFinite(p.dea)) return undefined;
    points.push({ date: p.date, close: p.close, dif: p.dif, dea: p.dea });
  }
  const previous = points[points.length - 2], trigger = points[points.length - 1];
  if (trigger.date !== row.trigger_date || previous.dif < previous.dea || trigger.dif >= trigger.dea) return undefined;
  return { kind: row.kind, price_adjustment: row.price_adjustment, trigger_date: row.trigger_date,
    source: row.source, fast: row.fast, slow: row.slow, signal: row.signal, points };
}

/** Values come from the trigger snapshot, including the EMA warm-up before the display window. */
export function macdEvidenceOption(chart: MacdChartEvidence, mode: ThemeMode): EChartsOption {
  const palette = chartPalette(mode);
  const dates = chart.points.map((p) => p.date);
  const trigger = chart.points[chart.points.length - 1];
  const marker = { symbol: "none", lineStyle: { color: "#e5484d", type: "dashed" as const },
    label: { formatter: "死叉", position: "insideEndTop" as const, color: palette.text },
    data: [{ xAxis: chart.trigger_date }] };
  const axis = { type: "category" as const, data: dates, boundaryGap: false,
    axisLabel: { color: palette.text, fontSize: 10, showMaxLabel: true, formatter: (day: string) => day.slice(5) },
    axisLine: { lineStyle: { color: palette.axisLine } } };
  const yAxis = { type: "value" as const, scale: true, axisLabel: { color: palette.text, fontSize: 10 },
    splitLine: { lineStyle: { color: palette.splitLine } } };
  return {
    animation: false,
    grid: [{ left: 48, right: 30, top: 24, height: "39%" }, { left: 48, right: 30, top: "61%", bottom: 28 }],
    tooltip: { trigger: "axis", confine: true, valueFormatter: (value) => Number(value).toFixed(6) },
    xAxis: [{ ...axis, gridIndex: 0 }, { ...axis, gridIndex: 1 }],
    yAxis: [{ ...yAxis, gridIndex: 0, name: "收盘价（元）", nameTextStyle: { color: palette.text, fontSize: 10 } },
      { ...yAxis, gridIndex: 1, name: "DIF / DEA", nameTextStyle: { color: palette.text, fontSize: 10 } }],
    series: [
      { name: "不复权收盘价", type: "line", data: chart.points.map((p) => p.close), showSymbol: false,
        lineStyle: { color: mode === "light" ? "#222222" : "#dddddd", width: 1.5 }, markLine: marker },
      { name: "DIF", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: chart.points.map((p) => p.dif),
        showSymbol: false, lineStyle: { color: "#e5484d", width: 1.5 }, markLine: marker,
        markPoint: { symbol: "circle", symbolSize: 10, label: { show: false }, itemStyle: { color: "#e5484d" },
          data: [{ name: "MACD 死叉", coord: [trigger.date, trigger.dif] }] } },
      { name: "DEA", type: "line", xAxisIndex: 1, yAxisIndex: 1, data: chart.points.map((p) => p.dea),
        showSymbol: false, lineStyle: { color: "#1f9dbe", width: 1.5 } },
    ],
  };
}
