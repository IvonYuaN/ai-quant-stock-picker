// 事件日历展示层纯逻辑的契约断言（由 node scripts/run-contracts.mjs 求值）。
//
// 覆盖：severity→徽标/标签映射、净额→方向文案与红涨绿跌配色、解禁排序、空/缺失输入安全。
import {
  eventsAvailability,
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
};
