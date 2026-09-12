// 持仓台账归一化的契约断言（由 npm test 真正执行）。
//
// 背景：`GET /api/portfolio` 在异常/降级时可能返回 `{}`，页面直接读
// `data.holdings.length` 会当场抛错白屏 —— 本次实测确实崩了。这里把边界钉死。
import { normalizePortfolio } from "./portfolio-view";

const fullFixture = {
  holdings: [
    {
      code: "300750",
      name: "宁德时代",
      price: 218.2,
      shares: 200,
      cost: 205.5,
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
