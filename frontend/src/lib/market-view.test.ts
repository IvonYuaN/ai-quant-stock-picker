// 市场环境展示模型的契约断言（由 npm test 真正执行）。
//
// 重点覆盖三件容易错的事：
//   1. "接口失败"与"数据源没覆盖"必须可区分（sectionAvailability 全 false vs 部分 true）
//   2. 涨跌比在分母为 0 时返回 null，不能伪装成 0
//   3. 行业涨跌幅可能是 "-" 占位串，必须归一成 null 而不是 NaN
import type { GlobalIndex, IndexQuote, IndustryData, MarketOverview, ShortTermEmotion, TurnoverStock } from "./api";
import { buildMarketView, marketStripSummary, sectionAvailability, type MarketInput } from "./market-view";

const indicesFixture = [
  { name: "上证指数", price: 3210.5, change_pct: 0.82, change_amt: 26.1 },
  { name: "深证成指", price: 10120.3, change_pct: -0.31, change_amt: -31.4 },
] satisfies IndexQuote[];

const globalFixture = [
  { key: "DJI", name: "道琼斯", region: "美股", price: 42100.2, change_pct: 0.45 },
  { key: "HSI", name: "恒生指数", region: "港股", price: 19200.0, change_pct: null },
] satisfies GlobalIndex[];

const overviewFixture = {
  sentiment: {
    up: 3200,
    down: 1500,
    flat: 210,
    zt: 88,
    zt_real: 72,
    dt: 6,
    dt_real: 4,
    active: "偏高",
    breadth: "普涨",
    speculation: "活跃",
    date: "2026-09-11",
  },
  sectors: [
    { name: "电池", pct: 3.2, net: 12.5e8, inflow: 30e8, outflow: 17.5e8, firms: 62 },
    { name: "银行", pct: 0.4, net: 2.1e8, inflow: 8e8, outflow: 5.9e8, firms: 42 },
    { name: "地产", pct: -2.1, net: -9.8e8, inflow: 4e8, outflow: 13.8e8, firms: 88 },
  ],
  updated: "2026-09-11 15:30",
} satisfies MarketOverview;

const emotionFixture = {
  date: "2026-09-11",
  zt_count: 88,
  dt_count: 6,
  zb_count: 22,
  max_boards: 5,
  lianban_count: 17,
  ladder: [
    { boards: 2, count: 11, plus: false },
    { boards: 3, count: 4, plus: false },
    { boards: 5, count: 2, plus: true },
  ],
  lianban_stocks: [
    {
      code: "300750",
      name: "宁德时代",
      boards: 2,
      price: 218.2,
      pct: 10.0,
      amount: 42e8,
      float_cap: 9600e8,
      industry: "电池",
    },
  ],
  seal_rate: 0.8,
  break_rate: 0.2,
  promotion_rate: 0.35,
  yzt_count: 60,
} satisfies ShortTermEmotion;

const turnoverFixture = [
  {
    code: "300750",
    name: "宁德时代",
    price: 218.2,
    pct: 10.0,
    amount: 42e8,
    mcap: 9600e8,
    float_cap: 8600e8,
    industry: "电池",
  },
] satisfies TurnoverStock[];

const industryFixture = {
  top: [{ rank: 1, name: "电池", change_pct: 3.2, code: "BK1033", up_count: 55, down_count: 7 }],
  // 后端可能给出 "-" 这类占位串，必须归一成 null
  bottom: [{ rank: 100, name: "地产", change_pct: "-", code: "BK0451", up_count: 8, down_count: 80 }],
  total: 100,
} satisfies IndustryData;

const fullInput: MarketInput = {
  indices: indicesFixture,
  global: globalFixture,
  overview: overviewFixture,
  emotion: emotionFixture,
  turnover: turnoverFixture,
  industry: industryFixture,
};

const full = buildMarketView(fullInput);

export const marketViewContract = {
  /* ---- 指数与外围 ---- */
  indicesAreMapped: full.indices.length === 2 && full.indices[0].name === "上证指数",
  indexKeepsSignedChange: full.indices[1].changePct === -0.31,
  globalKeepsNullChange: full.global[1].changePct === null,

  /* ---- 涨跌家数 ---- */
  breadthIsMapped: full.breadth !== null && full.breadth.up === 3200,
  breadthUsesRealLimitCounts: full.breadth?.ztReal === 72 && full.breadth?.dtReal === 4,
  breadthRatioIsComputed: full.breadth?.upDownRatio !== null && Math.abs((full.breadth?.upDownRatio ?? 0) - 3200 / 1500) < 1e-9,
  // 分母为 0 时必须是 null，不能伪装成 0
  breadthRatioIsNullWhenNoDecliners: buildMarketView({
    overview: { ...overviewFixture, sentiment: { ...overviewFixture.sentiment, down: 0 } },
  }).breadth?.upDownRatio === null,
  breadthIsNullWithoutSentiment: buildMarketView({ overview: null }).breadth === null,

  /* ---- 板块资金：头尾各取前列 ---- */
  sectorsTopTakesHead: full.sectorsTop.length === 3 && full.sectorsTop[0].name === "电池",
  sectorsBottomIsReversed: full.sectorsBottom[0].name === "地产",

  /* ---- 短线情绪 ---- */
  emotionIsMapped: full.emotion !== null && full.emotion.ztCount === 88,
  emotionLadderIsMapped: full.emotion?.ladder.length === 3 && full.emotion?.ladder[2].plus === true,
  emotionKeepsRates: full.emotion?.sealRate === 0.8 && full.emotion?.breakRate === 0.2,
  emotionIsNullWithoutDate: buildMarketView({
    emotion: { ...emotionFixture, date: "" },
  }).emotion === null,
  emotionLianbanIsMapped: full.emotion?.lianbanStocks[0].code === "300750",

  /* ---- 成交额榜与行业榜 ---- */
  turnoverIsMapped: full.turnover.length === 1 && full.turnover[0].amount === 42e8,
  industryTotalIsKept: full.industryTotal === 100,
  industryPlaceholderBecomesNull: full.industryBottom[0].changePct === null,
  industryBottomIsReversed: full.industryBottom[0].name === "地产",
  updatedIsKept: full.updated === "2026-09-11 15:30",

  /* ---- 接口失败 vs 数据源没覆盖 ---- */
  availabilityIsAllFalseForEmptyView: Object.values(sectionAvailability(buildMarketView({}))).every((value) => value === false),
  availabilityIsAllTrueForFullView: Object.values(sectionAvailability(full)).every((value) => value === true),

  /* ---- 顶部环境条摘要 ---- */
  stripIsNullWithoutAnyData: marketStripSummary(buildMarketView({})) === null,
  stripIsNullForNullView: marketStripSummary(null) === null,
  stripCallsRisingMarketOk: marketStripSummary(full)?.tone === "ok",
  stripCallsFallingMarketWarn: marketStripSummary(
    buildMarketView({
      overview: {
        ...overviewFixture,
        sentiment: { ...overviewFixture.sentiment, up: 400, down: 3800, breadth: "冰点" },
      },
    }),
  )?.tone === "warn",
  stripCallsMixedMarketNeutral: marketStripSummary(
    buildMarketView({
      overview: {
        ...overviewFixture,
        sentiment: { ...overviewFixture.sentiment, up: 2000, down: 2100, breadth: "中性" },
      },
    }),
  )?.tone === "neutral",
  // 没有涨跌家数时退化为按指数涨跌个数判断，而不是直接说"未知"
  stripFallsBackToIndices:
    marketStripSummary(buildMarketView({ indices: indicesFixture }))?.headline.includes("2 个指数中 1 个上涨") === true,
  stripHeadlineCountsBreadth: marketStripSummary(full)?.headline.includes("上涨 3200") === true,
};
