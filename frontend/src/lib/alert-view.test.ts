// P0-2/P1-2 该卖提醒收件箱归一化的契约断言（由 npm test 真正执行）。
//
// 背景：`GET /api/alerts` 在异常/降级（alert_event 簿损坏、新版后端未部署）时
// 可能给 {} 或部分字段。归一化层要把这些边界钉死，页面才不会白屏；同时
// buildAlertInbox 的两区派生（actionable vs watch，Q3=A T+1 语义）必须可复现。
import { buildAlertInbox, formatAlertPrice, groupPositionAlerts, normalizeAlerts, normalizeAlertEvent } from "./alert-view";
import type { Holding } from "./api";

const fullFixture = {
  open_count: 2,
  events: [
    {
      id: "1700000000000_600000_L1",
      level: "L1",
      symbol: "600000",
      position_id: null,
      trigger_at: 11.0,
      threshold_at: 11.59,
      pnl_pct: -8.33,
      sellable_now: true,
      action_hint: "该卖",
      t1_note: "",
      basis_json: { rule: "成本价 12.20 × (1−5%) = 11.59", actual: "现价 11.00", evidence: "L1 硬止损" },
      status: "OPEN",
      ignore_streak: 0,
      created_at: "2026-10-20T09:30:00+08:00",
      updated_at: "2026-10-20T09:30:00+08:00",
    },
    {
      id: "1700000000000_600036_L1",
      level: "L1",
      symbol: "600036",
      position_id: null,
      trigger_at: 26.1,
      threshold_at: 26.22,
      pnl_pct: -8.42,
      sellable_now: false,
      action_hint: "明日生效",
      t1_note: "T+1 限制：当日买入，今日不可卖出",
      basis_json: { rule: "成本价 28.50 × (1−8%) = 26.22", actual: "现价 26.10", evidence: "L1 硬止损" },
      status: "OPEN",
      ignore_streak: 0,
      created_at: "2026-10-20T09:30:00+08:00",
      updated_at: "2026-10-20T09:30:00+08:00",
    },
    {
      id: "999",
      level: "L5",
      symbol: "600001",
      position_id: null,
      trigger_at: 5.0,
      threshold_at: 4.8,
      pnl_pct: -4,
      sellable_now: true,
      action_hint: "该卖",
      t1_note: "",
      basis_json: { rule: "动态止损位 4.80", actual: "现价 5.00", evidence: "L5" },
      status: "HANDLED",
      ignore_streak: 0,
      created_at: "2026-10-19T09:00:00+08:00",
      updated_at: "2026-10-19T10:00:00+08:00",
    },
  ],
};

const full = normalizeAlerts(fullFixture);

export const alertViewContract = {
  groupedRulesCountAsOnePosition: groupPositionAlerts(normalizeAlerts({ events: [fullFixture.events[0], { ...fullFixture.events[0], id: "other", level: "L5" }] })).length === 1,
  groupedRulesKeepBothEvidence: groupPositionAlerts(normalizeAlerts({ events: [fullFixture.events[0], { ...fullFixture.events[0], id: "other", level: "L5" }] }))[0].events.length === 2,
  differentPositionsNeverMerge: groupPositionAlerts(normalizeAlerts({ events: [ { ...fullFixture.events[0], position_id: "old" }, { ...fullFixture.events[0], id: "other", position_id: "new" } ] })).length === 2,
  legacyEventUsesCurrentHoldingIdentity: groupPositionAlerts(
    normalizeAlerts({ events: [fullFixture.events[0], { ...fullFixture.events[0], id: "new", position_id: "current", level: "L5" }] }),
    [{ code: "600000", position_id: "current" } as Holding],
  ).length === 1,
  lockedLotsDoNotMergeWithSellable: groupPositionAlerts(normalizeAlerts({ events: [fullFixture.events[0], { ...fullFixture.events[0], sellable_now: false }] })).length === 2,
  floatingPointTailNeverDisplayed: formatAlertPrice(9.969299999999999) === "9.9693",
  invalidPriceUnknown: formatAlertPrice(Number.NaN) === "—" && formatAlertPrice(0) === "—",
  missingHoldingDaysStaysUnknown: normalizeAlertEvent({ position_context: {} }).position_context?.holding_trade_days === null,
  reviewChoiceSurvivesNormalization: normalizeAlertEvent({ review_plan: { choice: "t_watch", updated_at: "now" } }).review_plan?.choice === "t_watch",
  badReviewChoiceNotAccepted: normalizeAlertEvent({ review_plan: { choice: "buy_more" } }).review_plan === undefined,
  /* ---- 完整数据原样通过 ---- */
  eventsKept: full.events.length === 3,
  openCountKept: full.open_count === 2,
  firstEventFieldsKept:
    full.events[0].symbol === "600000" &&
    full.events[0].trigger_at === 11.0 &&
    full.events[0].sellable_now === true &&
    full.events[0].basis_json.rule.includes("11.59"),
  nonOpenStatusKept: full.events[2].status === "HANDLED",

  /* ---- 空 / 异常负载不得崩 ---- */
  emptyObjectYieldsEmpty: normalizeAlerts({}).events.length === 0 && normalizeAlerts({}).open_count === 0,
  nullYieldsEmpty: normalizeAlerts(null).events.length === 0,
  eventsWrongTypeYieldsEmpty: normalizeAlerts({ events: "oops" }).events.length === 0,
  openCountMissingDerivedFromList:
    // open_count 缺失时用明细里 OPEN 条数（这里给 2 个 OPEN）
    normalizeAlerts({ events: [fullFixture.events[0], fullFixture.events[1], { id: "x", status: "OPEN" }] }).open_count === 3,

  /* ---- 缺字段的回退 ---- */
  missingIdFallsBackEmpty: normalizeAlertEvent({ symbol: "600000" }).id === "",
  missingStatusBecomesOpen: normalizeAlertEvent({ symbol: "600000" }).status === "OPEN",
  missingSellableBecomesFalse: normalizeAlertEvent({ symbol: "600000" }).sellable_now === false,
  missingBasisBecomesEmptyStrings: normalizeAlertEvent({ symbol: "600000" }).basis_json.rule === "",
  missingNumbersBecomeZero: normalizeAlertEvent({ symbol: "600000" }).trigger_at === 0,
  frequencyReductionNoteSurvivesRefresh: normalizeAlertEvent({ frequency_note: "连续忽略两次，已降为每日一次；可手动修改" }).frequency_note === "连续忽略两次，已降为每日一次；可手动修改",
  legacyFrequencyNoteIsEmpty: normalizeAlertEvent({ symbol: "600000" }).frequency_note === "",
  wrongTypeStatusBecomesOpen: normalizeAlertEvent({ symbol: "600000", status: 123 }).status === "OPEN",

  /* ---- buildAlertInbox 两区派生（Q3=A T+1 语义）---- */
  inboxActionableHoldsSellable:
    buildAlertInbox(full).actionable.length === 1 && buildAlertInbox(full).actionable[0].symbol === "600000",
  inboxWatchHoldsNotSellable:
    buildAlertInbox(full).watch.length === 1 && buildAlertInbox(full).watch[0].symbol === "600036",
  inboxExcludesHandled: buildAlertInbox(full).openCount === 2 && buildAlertInbox(full).watch[0].sellable_now === false,
  inboxWatchKeepsT1Note: buildAlertInbox(full).watch[0].t1_note.includes("T+1"),
  emptyInboxAllZero: (() => {
    const v = buildAlertInbox(normalizeAlerts({}));
    return v.actionable.length === 0 && v.watch.length === 0 && v.openCount === 0;
  })(),
};
