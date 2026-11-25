// 事件日历展示层纯逻辑的契约断言（由 node scripts/run-contracts.mjs 求值）。
//
// 覆盖：severity→徽标/标签映射、净额→方向文案与红涨绿跌配色、解禁排序、空/缺失输入安全。
import {
  eventsAvailability,
  eventsEmptyDetail,
  eventsFaceGaps,
  netAmountView,
  severityBadgeClass,
  severityLabel,
  sortUnlocksByDaysUntil,
  unlockRatioText,
} from "./event-view";

export const eventViewContract = {
  // severity → 徽标：高=红 / 中=橙 / 低与未知=灰
  severityBadgeHigh: severityBadgeClass("high") === "aq-badge-up",
  severityBadgeMedium: severityBadgeClass("medium") === "aq-badge-warn",
  severityBadgeLow: severityBadgeClass("low") === "aq-badge-neutral",
  severityBadgeNegligible: severityBadgeClass("negligible") === "aq-badge-neutral",
  severityBadgeUnknownSafe: severityBadgeClass("") === "aq-badge-neutral",

  // severity → 中文标签
  severityLabelHigh: severityLabel("high") === "高",
  severityLabelMedium: severityLabel("medium") === "中",
  severityLabelLow: severityLabel("low") === "低",
  severityLabelUnknownSafe: severityLabel("") === "微",

  // 净额 → 方向：净买入=红（涨）、净卖出=绿（跌），与 WatchlistPage 口径一致
  netPositiveIsBuyRed:
    netAmountView(5000).text === "净买入 5000 万元" &&
    netAmountView(5000).cls === "aq-tone-up",
  netNegativeIsSellGreen:
    netAmountView(-3000.4).text === "净卖出 3000 万元" &&
    netAmountView(-3000.4).cls === "aq-tone-down",
  netZeroNeutral: netAmountView(0).text === "净额 0 万元" && netAmountView(0).cls === "",
  netNullIsUndisclosed:
    netAmountView(null).text === "净额未披露" && netAmountView(null).cls === "",
  netNanSafe:
    netAmountView(Number.NaN).text === "净额未披露" && netAmountView(Number.NaN).cls === "",

  // 解禁按 days_until 升序，且不修改输入数组
  sortUnlocksAscending: (() => {
    const input = [
      { symbol: "600000", name: "甲", event_type: "lockup_expiry", event_date: "2026-10-01", days_until: 8, severity: "low", ratio: 0.01, detail: "" },
      { symbol: "600000", name: "甲", event_type: "lockup_expiry", event_date: "2026-09-25", days_until: 2, severity: "high", ratio: 0.12, detail: "" },
      { symbol: "600000", name: "甲", event_type: "lockup_expiry", event_date: "2026-09-28", days_until: 5, severity: "medium", ratio: 0.05, detail: "" },
    ];
    const sorted = sortUnlocksByDaysUntil(input);
    return (
      sorted.map((event) => event.days_until).join(",") === "2,5,8" &&
      input[0].days_until === 8
    );
  })(),
  sortUnlocksEmptySafe: sortUnlocksByDaysUntil([]).length === 0,

  // 解禁比例文案
  ratioTextFormatted: unlockRatioText(0.0521) === "占总股本 5.21%",
  ratioTextNullSafe: unlockRatioText(null) === "占比未披露",

  // 「事件面读失败」与「确实没有事件」必须可区分（后端 fail-soft 时 has_*_data 全是 false，
  // 光看它们只能误诊成「缓存没生成」）。
  availabilityReadyWhenHealthy: eventsAvailability({ available: true, reason: "" }).state === "ready",
  availabilityReadyWhenFlagMissing: eventsAvailability({}).state === "ready",
  availabilityReadyWhenNoData: eventsAvailability(null).state === "ready",
  availabilityFailedOnFlagFalse: eventsAvailability({ available: false, reason: "RuntimeError: 缓存目录不可读" }).state === "failed",
  availabilityKeepsBackendReason: (() => {
    const view = eventsAvailability({ available: false, reason: "RuntimeError: 缓存目录不可读" });
    return view.state === "failed" && view.text.includes("RuntimeError: 缓存目录不可读");
  })(),
  availabilityExplainsEmptyIsNotTruth: (() => {
    const view = eventsAvailability({ available: false, reason: "RuntimeError: x" });
    return view.state === "failed" && view.text.includes("不是今天的真实状态");
  })(),
  availabilitySaysSoWhenReasonBlank: (() => {
    const view = eventsAvailability({ available: false, reason: "   " });
    return view.state === "failed" && view.text.includes("没有说明原因");
  })(),

  // 「缓存未装载」必须与「该股没有这类事件」区分（后端 has_*_data 的契约）。
  faceGapsAllLoaded: eventsFaceGaps({
    available: true,
    has_unlock_data: true,
    has_longhubang_data: true,
    has_suspend_resume_data: true,
    has_earnings_forecast_data: true,
    has_dividend_plan_data: true,
  }).length === 0,
  faceGapsListsMissingFaces: (() => {
    const gaps = eventsFaceGaps({
      available: true,
      has_unlock_data: true,
      has_longhubang_data: false,
      has_suspend_resume_data: false,
      has_earnings_forecast_data: true,
      has_dividend_plan_data: true,
    });
    return gaps.length === 2 && gaps.map((gap) => gap.label).join("/") === "龙虎榜/停复牌";
  })(),
  // 老后端没有这些键（undefined）时不下结论，别反过来误报「缺缓存」
  faceGapsUndefinedIsNotGap: eventsFaceGaps({ available: true }).length === 0,
  // 整体读失败由 eventsAvailability 负责，这里不再重复报「缺缓存」
  faceGapsEmptyWhenUnavailable: eventsFaceGaps({ available: false, has_unlock_data: false }).length === 0,
  faceGapsEmptyWhenNull: eventsFaceGaps(null).length === 0,

  emptyDetailSaysReallyEmptyWhenAllLoaded: eventsEmptyDetail({
    available: true,
    has_unlock_data: true,
    has_longhubang_data: true,
    has_suspend_resume_data: true,
    has_earnings_forecast_data: true,
    has_dividend_plan_data: true,
  }).includes("确实没有"),
  emptyDetailNamesMissingFaces: (() => {
    const text = eventsEmptyDetail({
      available: true,
      has_unlock_data: true,
      has_longhubang_data: true,
      has_suspend_resume_data: true,
      has_earnings_forecast_data: true,
      has_dividend_plan_data: false,
    });
    return text.includes("分红") && text.includes("缓存未装载") && text.includes("不能据此认为");
  })(),
  emptyDetailAllMissingSaysPreload: eventsEmptyDetail({
    available: true,
    has_unlock_data: false,
    has_longhubang_data: false,
    has_suspend_resume_data: false,
    has_earnings_forecast_data: false,
    has_dividend_plan_data: false,
  }).includes("预加载未落盘"),
};
