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
    coverage_complete: true,
    total: 4910,
    source: "东方财富全A逐只行情宽度",
    as_of: "2026-09-11T15:00:00+08:00",
    latest_source_at: "2026-09-11T15:00:03+08:00",
    zt: 88,
    zt_real: 72,
    dt: 6,
    dt_real: 4,
    active: "偏高",
    breadth: "偏强",
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

/**
 * 源失败时后端返回的**真实形状**：`sentiment: {}`（不是 null）。
 * 前端 `?? 0` 会把每个字段兜成 0，必须当「没数据」而不是「全市场平盘」。
 */
const emptySentiment = {
  up: 0,
  down: 0,
  flat: 0,
  zt: 0,
  zt_real: 0,
  dt: 0,
  dt_real: 0,
  active: "",
  breadth: "",
  speculation: "",
  date: "",
} satisfies MarketOverview["sentiment"];

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
  top: [{ rank: 1, name: "电池", change_pct: 3.2, code: "BK1033", up_count: 55, down_count: 7, as_of: "2026-09-11T15:00:00+08:00" }],
  // 后端可能给出 "-" 这类占位串，必须归一成 null
  bottom: [{ rank: 100, name: "地产", change_pct: "-", code: "BK0451", up_count: 8, down_count: 80, as_of: "2026-09-11T15:00:00+08:00" }],
  total: 100,
} satisfies IndustryData;

const fullInput: MarketInput = {
  indices: indicesFixture,
  global: globalFixture,
  overview: overviewFixture,
  emotion: emotionFixture,
  turnover: turnoverFixture,
  turnoverUpdated: "2026-09-11 15:32",
  industry: industryFixture,
};

const full = buildMarketView(fullInput);

function breadthWith(patch: Record<string, unknown>) {
  return buildMarketView({ overview: { ...overviewFixture,
    sentiment: { ...overviewFixture.sentiment, ...patch } as MarketOverview["sentiment"] } }).breadth;
}

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
  legacyTop100CannotBecomeMarketDirection: (() => {
    const overview = { ...overviewFixture, sentiment: { ...emptySentiment,
      up: 100, down: 0, breadth: "冰点", date: "2026-10-29" } };
    const view = buildMarketView({ overview, indices: indicesFixture });
    const strip = marketStripSummary(view);
    return view.breadth === null && strip?.tone === "neutral" && strip.headline.includes("市场宽度未取得");
  })(),
  incompleteOrUnverifiableBreadthIsUnavailable: [
    { coverage_complete: false }, { coverage_complete: undefined }, { source: undefined },
    { source: "涨停池" }, { total: undefined }, { total: 100 }, { total: -1 },
    { up: true }, { up: "3200" }, { down: -1 }, { flat: 0.5 }, { up: NaN }, { total: Infinity },
    { as_of: undefined }, { as_of: "2026-09-11" }, { as_of: "2026-09-11T15:00:00" },
    { latest_source_at: "2026-09-11T14:59:00+08:00" },
  ].every((patch) => breadthWith(patch) === null),
  breadthKeepsIndependentSourceTimeAndCoverage: full.breadth?.asOf === overviewFixture.sentiment.as_of &&
    full.breadth.total === 4910 && full.breadth.source === overviewFixture.sentiment.source,

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
  industrySourceTimeIsKept: full.industryUpdated === "2026-09-11T15:00:00+08:00" && full.industryTop[0].asOf === full.industryUpdated,
  missingIndustrySourceTimeStaysMissing: buildMarketView({ industry: { total: 1, top: [{ ...industryFixture.top[0], as_of: null }], bottom: [] } }).industryUpdated === "",
  industryPlaceholderBecomesNull: full.industryBottom[0].changePct === null,
  industryBottomIsReversed: full.industryBottom[0].name === "地产",
  updatedIsKept: full.updated === "2026-09-11 15:30",
  // W3 证据链：turnover 接口自带的取数时间必须透传（此前在 useMarketData 被丢弃）
  turnoverUpdatedIsKept: full.turnoverUpdated === "2026-09-11 15:32",
  turnoverUpdatedIsEmptyWithoutSource: buildMarketView({ turnover: turnoverFixture }).turnoverUpdated === "",

  /* ---- 接口失败 vs 数据源没覆盖 ---- */
  availabilityIsAllFalseForEmptyView: Object.values(sectionAvailability(buildMarketView({}))).every((value) => value === false),
  availabilityIsAllTrueForFullView: Object.values(sectionAvailability(full)).every((value) => value === true),

  /* ---- 板块资金流不能因涨跌家数缺失而连坐 ----
     两块来自不同源（涨跌家数＝乐咕/东财聚合，板块资金流＝东财行业资金）。
     prod 曾出现 sentiment 恒空而 sectors 有 90 行，页面把两者放进同一个 section
     ⇒ 整块被「涨跌家数未返回」的空态替换，90 行正常数据一起消失。 */
  breadthIsNullWhenSentimentIsAllZero: (() => {
    // 后端在源失败时返回空对象 `{}`，经 `?? 0` 兜成全 0 —— 必须当「没数据」。
    const v = buildMarketView({ overview: { ...overviewFixture, sentiment: emptySentiment } });
    return v.breadth === null && sectionAvailability(v).breadth === false;
  })(),
  sectorsAvailableWithoutBreadth: (() => {
    const v = buildMarketView({ overview: { ...overviewFixture, sentiment: emptySentiment } });
    const a = sectionAvailability(v);
    return a.breadth === false && a.sectors === true;
  })(),
  breadthAvailableWithoutSectors: (() => {
    const v = buildMarketView({ overview: { ...overviewFixture, sectors: [] } });
    const a = sectionAvailability(v);
    return a.breadth === true && a.sectors === false;
  })(),
  breadthKeptWhenOnlyOneSideIsNonZero: (() => {
    // 单边为 0 是真实情况（涨停潮 / 全跌），不能被「全 0」规则误判成没数据。
    const v = buildMarketView({
      overview: { ...overviewFixture, sentiment: { ...overviewFixture.sentiment, down: 0, flat: 0 } },
    });
    return v.breadth !== null && v.breadth.up > 0;
  })(),

  breadthKeepsNullLimitCounts: (() => {
    // #417：涨停池取数失败时后端 zt/dt 是 null（不是 0）⇒ 涨跌家数照常显示，
    // 涨停/跌停保持 null（页面渲染「—」），绝不把取数失败讲成「0 家涨停」。
    const v = buildMarketView({
      overview: {
        ...overviewFixture,
        sentiment: { ...overviewFixture.sentiment, zt: null, dt: null },
      },
    });
    return v.breadth !== null && v.breadth.zt === null && v.breadth.dt === null;
  })(),

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
  // 指数涨跌不能替代全市场宽度；来源缺失时不生成方向性结论。
  indicesAloneCannotClaimMarketStrength: (() => {
    const summary = marketStripSummary(buildMarketView({ indices: indicesFixture }));
    return summary?.tone === "neutral" && summary.headline.includes("市场宽度未取得");
  })(),
  sectorOnlyDataRemainsVisible: marketStripSummary(buildMarketView({ industry: industryFixture })) !== null,
  stripHeadlineCountsBreadth: marketStripSummary(full)?.headline.includes("上涨 3200") === true,

  /* ---- 证据链（#317 W3）：数据时间取该组最新的一条，不是取数时刻 ---- */
  indicesUpdatedPicksNewest: buildMarketView({
    indices: [
      { ...indicesFixture[0], as_of: "2026-10-19T10:58:06+08:00" },
      { ...indicesFixture[1], as_of: "2026-10-19T10:58:03+08:00" },
    ],
  }).indicesUpdated === "2026-10-19T10:58:06+08:00",
  // 偏移量写法不同时按瞬时比较：02:00Z (=10:00 上海) 比 09:00+08:00 更晚
  indicesUpdatedComparesByInstant: buildMarketView({
    indices: [
      { ...indicesFixture[0], as_of: "2026-10-19T09:00:00+08:00" },
      { ...indicesFixture[1], as_of: "2026-10-19T02:00:00+00:00" },
    ],
  }).indicesUpdated === "2026-10-19T02:00:00+00:00",
  // 源没给就是空串，页面据此显示「未标注」，绝不拿 now() 顶替
  indicesUpdatedEmptyWhenSourceSilent: buildMarketView({ indices: indicesFixture }).indicesUpdated === "",
  indicesUpdatedSkipsUnparseable: buildMarketView({
    indices: [
      { ...indicesFixture[0], as_of: "not-a-time" },
      { ...indicesFixture[1], as_of: "2026-10-19T10:58:03+08:00" },
    ],
  }).indicesUpdated === "2026-10-19T10:58:03+08:00",
  globalUpdatedKeepsPriorSession:
    buildMarketView({
      global: [
        { ...globalFixture[0], as_of: "2026-10-17T04:00:00+08:00" },
        { ...globalFixture[1], as_of: "2026-10-16T16:08:38+08:00" },
      ],
    }).globalUpdated === "2026-10-17T04:00:00+08:00",
  globalUpdatedEmptyWhenSourceSilent: buildMarketView({ global: globalFixture }).globalUpdated === "",
};
