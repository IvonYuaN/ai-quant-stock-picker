import type { EChartsOption } from "echarts";
import { macdEvidenceOption, normalizeMacdChart } from "./alert-chart";
import { normalizeAlertEvent } from "./alert-view";

export const macdChartFixture = {
  kind: "macd_dead_cross", price_adjustment: "none", fast: 12, slow: 26, signal: 9,
  source: "合成不复权日线", trigger_date: "2026-10-23",
  points: [
    { date: "2026-10-21", close: 10.2, dif: 0.2, dea: 0.1 },
    { date: "2026-10-22", close: 10.1, dif: 0.1, dea: 0.1 },
    { date: "2026-10-23", close: 9.8, dif: -0.1, dea: 0.05 },
  ],
};
const checkedAt = "2026-10-23T09:40:00+08:00";
const chart = normalizeMacdChart(macdChartFixture, checkedAt)!;
const invalid = (patch: object) => normalizeMacdChart({ ...macdChartFixture, ...patch }, checkedAt) === undefined;
const invalidPoint = (patch: object) => invalid({ points: [macdChartFixture.points[0], { ...macdChartFixture.points[1], ...patch }, macdChartFixture.points[2]] });
const event = (patch: object) => normalizeAlertEvent({ level: "L4", updated_at: checkedAt,
  basis_json: { rule: "MACD死叉", actual: "DIF < DEA", evidence: "文字依据", chart: macdChartFixture }, ...patch });
type ChartSeries = { data: unknown[]; markLine?: { data: { xAxis: string }[] }; markPoint?: { data: { coord: [string, number] }[] } };
const series = (option: EChartsOption) => option.series as unknown as ChartSeries[];
const option = macdEvidenceOption(chart, "dark");

export const alertChartContract = {
  validSnapshotKept: chart.points.length === 3 && chart.trigger_date === "2026-10-23",
  preservesExactNumbers: chart.points[2].dif === -0.1 && chart.points[2].close === 9.8,
  noChartForLegacy: normalizeAlertEvent({ level: "L4", basis_json: { rule: "old", actual: "old values" } }).basis_json.chart === undefined,
  keepsLegacyText: normalizeAlertEvent({ basis_json: { rule: "old", actual: "old values" } }).basis_json.actual === "old values",
  acceptsL4OptionalField: event({}).basis_json.chart?.trigger_date === "2026-10-23",
  ignoresChartForOtherRules: event({ level: "L1" }).basis_json.chart === undefined,
  rejectsMissingTimestamp: event({ updated_at: "", created_at: "" }).basis_json.chart === undefined,
  invalidChartKeepsText: event({ basis_json: { rule: "old", actual: "old values", chart: {} } }).basis_json.actual === "old values",
  rejectsAdjustedPrices: invalid({ price_adjustment: "qfq" }),
  rejectsUnknownKind: invalid({ kind: "golden_cross" }),
  rejectsOtherFormula: invalid({ fast: 5 }),
  rejectsMissingSource: invalid({ source: "" }),
  rejectsNoPoints: invalid({ points: [] }),
  rejectsPartialSnapshot: invalid({ points: [macdChartFixture.points[2]] }),
  rejectsOversizedSnapshot: invalid({ points: Array(61).fill(macdChartFixture.points[0]) }),
  rejectsImpossibleDate: invalidPoint({ date: "2026-02-30" }),
  rejectsDuplicateDate: invalidPoint({ date: "2026-10-21" }),
  rejectsWrongOrder: invalidPoint({ date: "2026-10-24" }),
  rejectsZeroClose: invalidPoint({ close: 0 }),
  rejectsMissingValue: invalidPoint({ dif: null }),
  rejectsNumericStrings: invalidPoint({ dea: "0.1" }),
  rejectsInfinity: invalidPoint({ dea: Infinity }),
  rejectsNaN: invalidPoint({ dif: NaN }),
  rejectsTriggerNotLast: invalid({ trigger_date: "2026-10-22" }),
  rejectsNoCross: invalidPoint({ dif: 0.01 }),
  rejectsFutureTrigger: normalizeMacdChart(macdChartFixture, "2026-10-22T23:59:59+08:00") === undefined,
  handlesShanghaiDay: normalizeMacdChart(macdChartFixture, "2026-10-22T16:00:00Z")?.trigger_date === "2026-10-23",
  usesStoredClose: series(option)[0].data.join() === "10.2,10.1,9.8",
  usesStoredDif: series(option)[1].data.join() === "0.2,0.1,-0.1",
  usesStoredDea: series(option)[2].data.join() === "0.1,0.1,0.05",
  marksPriceTriggerDate: series(option)[0].markLine?.data[0].xAxis === chart.trigger_date,
  marksMacdTriggerDate: series(option)[1].markLine?.data[0].xAxis === chart.trigger_date,
  marksExactDeadCross: series(option)[1].markPoint?.data[0].coord.join() === "2026-10-23,-0.1",
  lightModeAlsoMarksCross: series(macdEvidenceOption(chart, "light"))[1].markPoint?.data[0].coord[0] === chart.trigger_date,
};
