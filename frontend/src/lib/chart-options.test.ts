// 图表配置构建器的契约断言（由 npm test 真正执行）。
//
// 这些 option 是纯对象，可以直接断言 —— 比"截图对不对"可靠得多。
// 重点：数据长度、正负配色（A 股红涨绿跌）、单位换算（元→亿）、空值过滤。
import type { EChartsOption } from "echarts";
import {
  DOWN_COLOR,
  UP_COLOR,
  chartPalette,
  industryOption,
  ladderOption,
  sectorFlowOption,
} from "./chart-options";
import type { LadderRow } from "./market-view";

type BarSeries = { type?: string; data: Array<{ value: number; itemStyle?: { color?: string } }> };
type CategoryAxis = { type?: string; data?: string[] };

function firstSeries(option: EChartsOption): BarSeries {
  const series = option.series as unknown as BarSeries[];
  return series[0];
}
function xAxis(option: EChartsOption): CategoryAxis {
  return option.xAxis as unknown as CategoryAxis;
}
function yAxis(option: EChartsOption): CategoryAxis {
  return option.yAxis as unknown as CategoryAxis;
}

const sectors = [
  { name: "电池", pct: 3.2, net: 12.5e8, firms: 62 },
  { name: "银行", pct: 0.4, net: 2.1e8, firms: 42 },
  { name: "地产", pct: -2.1, net: -9.8e8, firms: 88 },
];
const ladder: LadderRow[] = [
  { boards: 2, count: 11, plus: false },
  { boards: 3, count: 4, plus: false },
  { boards: 5, count: 2, plus: true },
];
const industries = [
  { rank: 1, name: "电池", code: "BK1", changePct: 3.2, upCount: 55, downCount: 7 },
  { rank: 2, name: "矿业", code: "BK2", changePct: -1.5, upCount: 8, downCount: 20 },
  // 占位串已被上游归一成 null，这里必须被过滤掉而不是画成 0
  { rank: 3, name: "地产", code: "BK3", changePct: null, upCount: 0, downCount: 0 },
];

const sectorChart = sectorFlowOption(sectors, "dark");
const ladderChart = ladderOption(ladder, "dark");
const industryChart = industryOption(industries, "dark");

export const chartOptionsContract = {
  /* ---- 板块资金流：按净额降序、红绿按方向 ---- */
  sectorSortedDescending: (yAxis(sectorChart).data ?? []).join("|") === "电池|银行|地产",
  sectorInflowIsRed: firstSeries(sectorChart).data[0].itemStyle?.color === UP_COLOR,
  sectorOutflowIsGreen: firstSeries(sectorChart).data[2].itemStyle?.color === DOWN_COLOR,
  sectorValuesInYi: firstSeries(sectorChart).data[0].value === 12.5,
  sectorToleratesEmpty: firstSeries(sectorFlowOption([], "dark")).data.length === 0,

  /* ---- 连板梯队 ---- */
  ladderCategoriesUseChinese: (xAxis(ladderChart).data ?? []).join("|") === "2 板|3 板|5 板+",
  ladderDataIsCount: firstSeries(ladderChart).data.map((item) => item.value).join("|") === "11|4|2",
  ladderUsesUpColor: firstSeries(ladderChart).data[0].itemStyle?.color === UP_COLOR,
  ladderToleratesEmpty: firstSeries(ladderOption([], "dark")).data.length === 0,

  /* ---- 行业涨跌幅：空值必须被过滤 ---- */
  industryFiltersNullChangePct: firstSeries(industryChart).data.length === 2,
  industrySortedDescending: (yAxis(industryChart).data ?? []).join("|") === "电池|矿业",
  industryRisingIsRed: firstSeries(industryChart).data[0].itemStyle?.color === UP_COLOR,
  industryFallingIsGreen: firstSeries(industryChart).data[1].itemStyle?.color === DOWN_COLOR,
  industryToleratesEmpty: firstSeries(industryOption([], "dark")).data.length === 0,

  /* ---- 主题配色 ---- */
  darkPaletteUsesLightText: chartPalette("dark").text === "#8b95a7",
  lightPaletteUsesDarkText: chartPalette("light").text === "#5a6472",
  palettesDiffer: chartPalette("dark").splitLine !== chartPalette("light").splitLine,
};
