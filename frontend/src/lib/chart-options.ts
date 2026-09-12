// 图表配置构建（纯函数，可单测）。
//
// 只产出 echarts 的 option 对象，不碰 DOM —— 这样"图怎么画"能被断言，
// 而"图怎么渲染"由 components/ui/EChart.tsx 负责。
//
// 配色统一 A 股口径：流入 / 上涨 = 红，流出 / 下跌 = 绿。
import type { EChartsOption } from "echarts";
import type { IndustryRowView, LadderRow, SectorFlowRow } from "./market-view";
import type { ThemeMode } from "./theme-mode";

export const UP_COLOR = "#e5484d"; // 红：流入 / 上涨 / 盈利
export const DOWN_COLOR = "#30a46c"; // 绿：流出 / 下跌 / 亏损

interface Palette {
  text: string;
  splitLine: string;
  axisLine: string;
}

export function chartPalette(mode: ThemeMode): Palette {
  return mode === "light"
    ? { text: "#5a6472", splitLine: "rgba(0,0,0,0.08)", axisLine: "rgba(0,0,0,0.18)" }
    : { text: "#8b95a7", splitLine: "rgba(255,255,255,0.08)", axisLine: "rgba(255,255,255,0.18)" };
}

const BASE_GRID = { left: 8, right: 12, top: 18, bottom: 4, containLabel: true };

/** 板块资金流：横向条形图（净额，亿元）。 */
export function sectorFlowOption(rows: readonly SectorFlowRow[], mode: ThemeMode): EChartsOption {
  const palette = chartPalette(mode);
  const sorted = [...rows].sort((a, b) => b.net - a.net);
  return {
    grid: { ...BASE_GRID, left: 4 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value) => `${Number(value).toFixed(2)} 亿`,
    },
    xAxis: {
      type: "value",
      name: "净额(亿)",
      nameTextStyle: { color: palette.text, fontSize: 10 },
      axisLabel: { color: palette.text, fontSize: 10 },
      splitLine: { lineStyle: { color: palette.splitLine } },
    },
    yAxis: {
      type: "category",
      data: sorted.map((row) => row.name),
      axisLabel: { color: palette.text, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.axisLine } },
    },
    series: [
      {
        type: "bar",
        data: sorted.map((row) => ({
          value: Number((row.net / 1e8).toFixed(2)),
          itemStyle: { color: row.net >= 0 ? UP_COLOR : DOWN_COLOR },
        })),
        barMaxWidth: 12,
      },
    ],
  };
}

/** 连板梯队：每个板数多少家。 */
export function ladderOption(ladder: readonly LadderRow[], mode: ThemeMode): EChartsOption {
  const palette = chartPalette(mode);
  return {
    grid: BASE_GRID,
    tooltip: { trigger: "axis", valueFormatter: (value) => `${Number(value)} 家` },
    xAxis: {
      type: "category",
      data: ladder.map((tier) => (tier.plus ? `${tier.boards} 板+` : `${tier.boards} 板`)),
      axisLabel: { color: palette.text, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.axisLine } },
    },
    yAxis: {
      type: "value",
      name: "家数",
      nameTextStyle: { color: palette.text, fontSize: 10 },
      axisLabel: { color: palette.text, fontSize: 10 },
      splitLine: { lineStyle: { color: palette.splitLine } },
    },
    series: [
      {
        type: "bar",
        data: ladder.map((tier) => ({ value: tier.count, itemStyle: { color: UP_COLOR } })),
        barMaxWidth: 26,
        label: { show: true, position: "top", color: palette.text, fontSize: 10 },
      },
    ],
  };
}

/** 行业涨跌幅：横向条形图。 */
export function industryOption(rows: readonly IndustryRowView[], mode: ThemeMode): EChartsOption {
  const palette = chartPalette(mode);
  const sorted = [...rows]
    .filter((row) => row.changePct != null)
    .sort((a, b) => (b.changePct ?? 0) - (a.changePct ?? 0));
  return {
    grid: { ...BASE_GRID, left: 4 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value) => `${Number(value).toFixed(2)}%`,
    },
    xAxis: {
      type: "value",
      name: "涨跌幅(%)",
      nameTextStyle: { color: palette.text, fontSize: 10 },
      axisLabel: { color: palette.text, fontSize: 10 },
      splitLine: { lineStyle: { color: palette.splitLine } },
    },
    yAxis: {
      type: "category",
      data: sorted.map((row) => row.name),
      axisLabel: { color: palette.text, fontSize: 10 },
      axisLine: { lineStyle: { color: palette.axisLine } },
    },
    series: [
      {
        type: "bar",
        data: sorted.map((row) => ({
          value: Number((row.changePct ?? 0).toFixed(2)),
          itemStyle: { color: (row.changePct ?? 0) >= 0 ? UP_COLOR : DOWN_COLOR },
        })),
        barMaxWidth: 12,
      },
    ],
  };
}
