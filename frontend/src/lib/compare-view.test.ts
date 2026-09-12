// 多日对比的契约断言（由 npm test 真正执行）。
import { buildDailyView } from "./daily-view";
import { dailyViewFixture } from "./daily-view.test";
import {
  MAX_COMPARE,
  buildCompareSummary,
  parseCompareDates,
  toggleCompareDate,
} from "./compare-view";

const available = ["2026-09-11", "2026-09-10", "2026-09-09", "2026-09-08"];

const day = (date: string, candidates = dailyViewFixture.candidates) =>
  buildDailyView({ ...dailyViewFixture, selected_date: date, candidates });

const d1 = day("2026-09-11");
const d2 = day("2026-09-10");
// 第三天只剩一只，用来验证"连续在榜"与"昙花一现"
const d3 = day("2026-09-09", dailyViewFixture.candidates.slice(0, 1));

const summary = buildCompareSummary([d1, d2, d3]);
const twoDay = buildCompareSummary([d1, d2]);

export const compareViewContract = {
  /* ---- 日期解析：只认存在的日期、去重、守上限 ---- */
  parsesKnownDates: parseCompareDates("2026-09-11,2026-09-10", available).join("|") === "2026-09-11|2026-09-10",
  dropsUnknownDates: parseCompareDates("2026-09-11,1999-01-01", available).join("|") === "2026-09-11",
  dedupesRepeated: parseCompareDates("2026-09-11,2026-09-11", available).length === 1,
  nullYieldsEmpty: parseCompareDates(null, available).length === 0,
  emptyYieldsEmpty: parseCompareDates("", available).length === 0,
  respectsMaxCap: parseCompareDates(available.join(","), available).length === 0 ||
    parseCompareDates(available.join(","), available).length <= MAX_COMPARE,
  trimsWhitespace: parseCompareDates(" 2026-09-11 , 2026-09-10 ", available).length === 2,

  /* ---- 加/去日期 ---- */
  toggleAdds: toggleCompareDate(["2026-09-11"], "2026-09-10").join("|") === "2026-09-10|2026-09-11",
  toggleRemoves: toggleCompareDate(["2026-09-11", "2026-09-10"], "2026-09-10").join("|") === "2026-09-11",
  // 满了就不动（由 UI 提示），不静默丢弃用户的选择
  toggleRespectsMax:
    toggleCompareDate(["2026-09-11", "2026-09-10", "2026-09-09"], "2026-09-08", 3).length === 3,
  toggleAllowsWhenBelowMax:
    toggleCompareDate(["2026-09-11"], "2026-09-10", 3).length === 2,

  /* ---- 汇总 ---- */
  rowCountMatchesDates: summary.rows.length === 3,
  // 归档按日回看：新的在前
  rowsSortedNewestFirst: summary.rows.map((r) => r.date).join("|") === "2026-09-11|2026-09-10|2026-09-09",
  totalDatesIsRight: summary.totalDates === 3,
  rowHasCandidateCount: summary.rows[0].candidateCount === dailyViewFixture.candidates.length,
  rowHasReadyCount: summary.rows[0].readyCount === 1,
  rowHasGateLabel: summary.rows[0].gateLabel.length > 0,
  topSymbolsCappedAtThree: summary.rows[0].topSymbols.length <= 3,

  /* ---- 持续在榜 / 昙花一现 ---- */
  // 300750 三天都在 → 持续在榜
  persistentDetected: summary.persistentSymbols.includes("300750"),
  // 600036 只在头两天 → 不是持续
  notPersistentExcluded: !summary.persistentSymbols.includes("600036"),
  twoDayPersistentIncludesBoth:
    twoDay.persistentSymbols.length === dailyViewFixture.candidates.length,
  allSymbolsDeduplicated: new Set(summary.allSymbols).size === summary.allSymbols.length,

  /* ---- 边界 ---- */
  emptyViewsSafe: buildCompareSummary([]).rows.length === 0,
  emptyViewsTotalZero: buildCompareSummary([]).totalDates === 0,
  // 单日时每只候选都只出现一次 —— 所以「昙花一现」这个口径只在 totalDates >= 2 时才有意义（UI 侧门控）
  singleDayAllCountAsOneOff:
    buildCompareSummary([d1]).oneOffCount === dailyViewFixture.candidates.length,
  twoDayOneOffIsZero: twoDay.oneOffCount === 0,
};
