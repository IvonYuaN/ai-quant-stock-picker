import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { PositionPricePath } from "./api";
import { PositionPricePathEvidence } from "../components/aqsp/PositionPricePathEvidence";

const pathFixture: PositionPricePath = {
  schema: "completed-price-path-v1", scope: "completed_daily_history", price_mode: "raw",
  status: "available", source: "合成不复权日线源", as_of: "2026-10-28",
  windows: [{ window: 5, start_date: "2026-10-22", end_date: "2026-10-28",
    return_pct: -2.5, max_drawdown_pct: -4.2, volume_ratio: 1.25, high: 12, low: 10 }],
  issues: ["20 个完成交易日不足"], notice: "已完成日历史事实",
};
const render = (path?: PositionPricePath) => renderToStaticMarkup(createElement(PositionPricePathEvidence, { path }));
const ready = render(pathFixture);
const unavailable = render({ ...pathFixture, status: "unavailable", windows: [] });
const invalid = render({ ...pathFixture, windows: [{ ...pathFixture.windows[0], volume_ratio: NaN }] });
export const completedPricePathContracts = {
  legacyResponseAddsNothing: render() === "",
  partialWindowKeepsFactsAndGap: ready.includes("5 个已完成交易日") && ready.includes("20 个完成交易日不足"),
  sourcePeriodAndRawModeVisible: ready.includes("合成不复权日线源") && ready.includes("2026-10-22 至 2026-10-28") && ready.includes("不复权日线"),
  ratioExplainsDenominator: ready.includes("此前日均量的 1.25 倍"),
  observedRangeNeverPresentedAsSignal: ready.includes("10.00–12.00 元") && ready.includes("区间高低也不等于买卖信号"),
  missingDataDoesNotInventZero: unavailable.includes("暂缺可核验") && !unavailable.includes("0.00 倍"),
  malformedValuesNeverRenderNaN: invalid.includes("暂缺可核验") && !invalid.includes("NaN"),
  malformedRowsDoNotCrashPositionPage: render({ ...pathFixture, windows: [null] as unknown as PositionPricePath["windows"] }).includes("暂缺可核验"),
  malformedSourceDoesNotRenderAnObject: render({ ...pathFixture, source: {} as unknown as string }).includes("暂缺可核验"),
  unknownContractNeverBecomesEvidence: render({ ...pathFixture, schema: "unrecognized" as PositionPricePath["schema"] }).includes("暂缺可核验"),
};
