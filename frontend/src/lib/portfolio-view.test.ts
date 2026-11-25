// 持仓台账归一化的契约断言（由 npm test 真正执行）。
//
// 背景：`GET /api/portfolio` 在异常/降级时可能返回 `{}`，页面直接读
// `data.holdings.length` 会当场抛错白屏 —— 本次实测确实崩了。这里把边界钉死。
import { holdingDateError, holdingT1Status, normalizePortfolio, shanghaiDate } from "./portfolio-view";

export const holdingT1Contract = {
  padsSingleDigitMonthAndDay: shanghaiDate(new Date("2026-01-02T01:00:00Z")) === "2026-01-02",
  changesDateAtShanghaiMidnight: shanghaiDate(new Date("2026-10-22T16:00:00Z")) === "2026-10-23",
  keepsDateBeforeShanghaiMidnight: shanghaiDate(new Date("2026-10-22T15:59:59Z")) === "2026-10-22",
  acceptsLeapDay: holdingDateError("2024-02-29", "2026-10-23") === "",
  rejectsImpossibleDay: holdingDateError("2026-02-30", "2026-10-23") !== "",
  rejectsFutureDay: holdingDateError("2026-10-24", "2026-10-23") !== "",
  rejectsEmptyDay: holdingDateError("", "2026-10-23") !== "",
  rejectsUnpaddedDay: holdingDateError("2026-1-2", "2026-10-23") !== "",
  newHoldingIsRestricted: holdingT1Status("2026-10-23", "2026-10-23").kind === "today",
  priorHoldingIsIdentified: holdingT1Status("2026-10-22", "2026-10-23").kind === "prior",
  missingDateKeepsLegacyMeaning: holdingT1Status("", "2026-10-23").kind === "legacy",
  futureDateNeedsReview: holdingT1Status("2026-10-24", "2026-10-23").kind === "invalid",
  storedEmptyDateNeedsReview: holdingT1Status(normalizePortfolio({ holdings: [{ opened_at: "" }] }).holdings[0].opened_at, "2026-10-23").kind === "invalid",
  storedNumericDateNeedsReview: holdingT1Status(normalizePortfolio({ holdings: [{ opened_at: 20261023 }] }).holdings[0].opened_at, "2026-10-23").kind === "invalid",
  storedNullDateKeepsLegacyMeaning: holdingT1Status(normalizePortfolio({ holdings: [{ opened_at: null }] }).holdings[0].opened_at, "2026-10-23").kind === "legacy",
  storedMissingDateKeepsLegacyMeaning: holdingT1Status(normalizePortfolio({ holdings: [{}] }).holdings[0].opened_at, "2026-10-23").kind === "legacy",
};

const fullFixture = {
  holdings: [
    {
      code: "300750",
      name: "宁德时代",
      price: 218.2,
      shares: 200,
      cost: 205.5,
      opened_at: "2026-10-09",
      market_value: 43640,
      pnl: 2540,
      pnl_pct: 6.18,
    },
  ],
  totals: { market_value: 43640, cost: 41100, pnl: 2540, pnl_pct: 6.18 },
  closed: [
    { code: "600036", name: "招商银行", date: "2026-08-20", price: 45, shares: 500, cost: 40, pnl: 2500, pnl_pct: 12.5 },
  ],
  realized_pnl: 2500,
  updated: "2026-09-11 15:30",
  last_refresh: "2026-09-11 15:00",
};

const full = normalizePortfolio(fullFixture);
const emptyFromObject = normalizePortfolio({});
const emptyFromNull = normalizePortfolio(null);
const emptyFromString = normalizePortfolio("boom");

export const portfolioViewContract = {
  /* ---- 完整数据原样通过 ---- */
  holdingsAreKept: full.holdings.length === 1 && full.holdings[0].code === "300750",
  totalsAreKept: full.totals.market_value === 43640 && full.totals.pnl === 2540,
  closedAreKept: full.closed.length === 1 && full.closed[0].pnl === 2500,
  realizedPnlIsKept: full.realized_pnl === 2500,
  updatedIsKept: full.updated === "2026-09-11 15:30",
  lastRefreshIsKept: full.last_refresh === "2026-09-11 15:00",
  // P2-8：开仓日期原样通过（T+1 可卖性据此推导）
  openedAtIsKept: full.holdings[0].opened_at === "2026-10-09",

  /* ---- 空 / 异常负载不得崩 ---- */
  emptyObjectYieldsEmptyLists: emptyFromObject.holdings.length === 0 && emptyFromObject.closed.length === 0,
  emptyObjectYieldsZeroTotals: emptyFromObject.totals.market_value === 0 && emptyFromObject.totals.pnl === 0,
  nullYieldsEmptyLists: emptyFromNull.holdings.length === 0,
  stringYieldsEmptyLists: emptyFromString.holdings.length === 0,
  lastRefreshNonStringBecomesNull: normalizePortfolio({ last_refresh: 123 }).last_refresh === null,
  holdingsWrongTypeBecomesEmpty: normalizePortfolio({ holdings: "oops" }).holdings.length === 0,
  closedWrongTypeBecomesEmpty: normalizePortfolio({ closed: {} }).closed.length === 0,

  /* ---- 缺字段时的回退 ---- */
  // 名称缺失回退成代码，而不是显示空白
  missingNameFallsBackToCode: normalizePortfolio({ holdings: [{ code: "300750" }] }).holdings[0].name === "300750",
  // 明细字段缺失时用 0 而不是 NaN
  missingNumbersBecomeZero: normalizePortfolio({ holdings: [{ code: "300750" }] }).holdings[0].price === 0,
  // P2-8：老持仓（P2-8 前录入）无 opened_at / 后端给 null ⇒ 归一成 ""（按老仓可卖，不误伤）
  missingOpenedAtBecomesEmpty: normalizePortfolio({ holdings: [{ code: "300750" }] }).holdings[0].opened_at === "",
  nullOpenedAtBecomesEmpty: normalizePortfolio({ holdings: [{ code: "300750", opened_at: null }] }).holdings[0].opened_at === "",
  // 后端没给汇总时用明细自己算，而不是显示 0（0 会被误读成"没亏没赚"）
  totalsAreDerivedWhenMissing:
    normalizePortfolio({
      holdings: [{ code: "300750", shares: 100, cost: 10, market_value: 1200, pnl: 200, pnl_pct: 20 }],
    }).totals.market_value === 1200,
  derivedPnlIsConsistent:
    normalizePortfolio({
      holdings: [{ code: "300750", shares: 100, cost: 10, market_value: 1200, pnl: 200, pnl_pct: 20 }],
    }).totals.pnl === 200,
  derivedRealizedPnlSumsClosed:
    normalizePortfolio({
      closed: [
        { code: "600036", pnl: 100 },
        { code: "300750", pnl: -30 },
      ],
    }).realized_pnl === 70,
  // 部分字段有值时只补缺失的那部分
  partialTotalsAreCompleted:
    normalizePortfolio({ totals: { market_value: 999 }, holdings: [] }).totals.market_value === 999,
};

/**
 * M24：成本未知的平仓成交不得被折成「0 元盈亏 / 0% 收益」。
 *
 * 后端 `_apply_close` 过去在 `cost == 0`（补录历史成交、账本里没有对应持仓）时
 * 写 `pnl = price * shares`、`pnl_pct = 0.0`。现在写 `None`，并且读取时纠正老账本。
 * 前端这一层必须**原样保留 null** —— 用 `asNumber` 会把它折回 0，等于把
 * 「算不出盈亏」重新显示成「没赚没亏」，后端改了也白改。
 */
const costUnknown = normalizePortfolio({
  holdings: [],
  closed: [
    { code: "600000", name: "无成本依据", date: "2026-10-20", price: 12, shares: 100, cost: 0, pnl: null, pnl_pct: null },
    { code: "600001", name: "有据", date: "2026-10-21", price: 12, shares: 100, cost: 10, pnl: 200, pnl_pct: 20 },
  ],
  realized_pnl: 200,
  realized_pnl_unknown_count: 1,
});

export const portfolioCostUnknownContract = {
  // 🔴 关键：null 必须活下来，不能被 asNumber 折成 0
  unknownPnlStaysNull: costUnknown.closed[0].pnl === null,
  unknownPnlPctStaysNull: costUnknown.closed[0].pnl_pct === null,
  // 对照组：有成本依据的行不受影响
  knownPnlIsKept: costUnknown.closed[1].pnl === 200,
  knownPnlPctIsKept: costUnknown.closed[1].pnl_pct === 20,
  // 汇总与披露
  realizedPnlKept: costUnknown.realized_pnl === 200,
  unknownCountKept: costUnknown.realized_pnl_unknown_count === 1,

  // 后端没给汇总时的本地兜底：只加有据的，null 不能当 0 混进求和
  localSumSkipsUnknown:
    normalizePortfolio({
      closed: [
        { code: "600000", date: "2026-10-20", price: 12, shares: 100, cost: 0, pnl: null, pnl_pct: null },
        { code: "600001", date: "2026-10-21", price: 12, shares: 100, cost: 10, pnl: 200, pnl_pct: 20 },
      ],
    }).realized_pnl === 200,
  // 本地兜底也要数出未知笔数，否则卡片不会披露
  localUnknownCount:
    normalizePortfolio({
      closed: [{ code: "600000", date: "2026-10-20", price: 12, shares: 100, cost: 0, pnl: null, pnl_pct: null }],
    }).realized_pnl_unknown_count === 1,
  // 全部有据时披露数为 0（不该凭空冒出提示）
  noUnknownWhenAllKnown:
    normalizePortfolio({
      closed: [{ code: "600001", date: "2026-10-21", price: 12, shares: 100, cost: 10, pnl: 200, pnl_pct: 20 }],
    }).realized_pnl_unknown_count === 0,
};
