// daily-view 展示模型的契约断言。
//
// 这些断言由 `npm test`（scripts/run-contracts.mjs）真正求值 —— 之前 `*.test.ts` 只被 tsc
// 做类型检查、布尔表达式从不执行，项目 IA 漂移就是这样漏过去的。
//
// 命名约定：`*Fixture` 是测试数据（内含刻意构造的 false），执行器会跳过；其余导出才算断言。
import type { AqspSnapshot, AqspVariantSuite } from "@/types/aqsp";
import {
  buildDailyView,
  DEFAULT_TODAY_SECTION,
  gateView,
  resolveTodaySection,
  symbolNames,
  TODAY_SECTION_IDS,
} from "./daily-view";

export const dailyViewFixture = {
  schema_version: "v1",
  generated_at: "2026-09-11T15:30:00+08:00",
  selected_date: "2026-09-11",
  available_dates: ["2026-09-11", "2026-09-10"],
  candidates: [
    {
      symbol: "300750",
      display_name: "300750 300750 宁德时代",
      score: 87.4,
      research_status: "通过筛选",
      next_step: "等回踩 MA20",
      context: "放量突破前高后缩量回踩。",
      deterministic_reasons: ["MA5/10/20 多头排列"],
      strategies: ["放量突破", "RPS 相对强度"],
      evidence_status: "已闭环",
      score_breakdown: ["动能 32", "趋势 28"],
      technical_metrics: [
        { key: "ret20_pct", label: "20日涨幅", value: "+18.4%" },
        { key: "rps", label: "RPS", value: "97" },
      ],
      data_source: "eastmoney",
      freshness: "fresh",
    },
    {
      symbol: "600036",
      display_name: "600036 招商银行",
      score: 71.2,
      research_status: "仅观察",
      next_step: "等待个股级消息证据",
      context: "低波趋势延续。",
      deterministic_reasons: ["均线缩量回踩"],
      strategies: ["均线缩量回踩"],
      evidence_status: "证据未闭环",
    },
  ],
  debates: [
    {
      symbol: "300750",
      display_name: "宁德时代",
      conclusion: "证据链闭环，可进入纸面复核。",
      primary_risk_gate: "板块集中度接近上限",
      next_trigger: "跌破 MA20 移出",
      active_roles: ["多头", "空头", "风控"],
      round_count: 3,
      bull_count: 2,
      bear_count: 1,
      neutral_count: 1,
      process_summary: "围绕量价结构讨论。",
      round_summaries: ["第1轮：突破有效。"],
      viewpoint_buckets: { bullish: ["放量突破前高"], bearish: ["板块拥挤"] },
      disagreement_points: ["突破是否被量能确认"],
    },
  ],
  summaries: ["盘中：判断：2 个对象通过筛选。"],
  source: { effective: "eastmoney", latest_trade_date: "2026-09-11", lag_days: 0, status: "fresh" },
  coldstart: { status: "完成", detail: "" },
  stale_after: "2026-09-12T15:30:00+08:00",
  message_status: "已产出",
  messages: [
    {
      title: "海外储能招标落地",
      summary: "利好电池环节。",
      impact: "利好",
      category: "行业",
      source: "公开财经媒体",
      published_at: "2026-09-11T09:12:00+08:00",
      source_url: "https://example.test/news/1",
      affected_sectors: ["电池", "储能"],
      affected_symbols: ["300750"],
      transmission_path: ["招标落地", "排产上修"],
      validation_signals: ["排产环比上行"],
      invalidation_signals: ["招标延期"],
    },
  ],
  market_context: {
    status: "已产出",
    overview: "外盘科技走强。",
    summary_lines: ["来源覆盖: 国内 9/9 路", "消息结果: 1 条高影响事件"],
    cross_market: [
      {
        rule_id: "r1",
        theme: "海外储能需求",
        strength: "中",
        action: "关注电芯",
        source_title: "t",
        source_region: "US",
        source_published_at: "2026-09-11T08:00:00+08:00",
        affected_sectors: ["电池"],
        transmission_path: ["海外招标", "国内排产"],
        validation_signals: ["排产上行"],
        invalidation_signals: ["招标延期"],
        summary: "需求传导。",
      },
    ],
    warnings: [],
  },
  recommendation_gate: {
    recommendation_allowed: false,
    status: "blocked",
    reasons: ["freshness_not_ready"],
  },
  phases: [
    {
      task_id: "pre_market",
      label: "盘前",
      status: "已产出",
      candidate_count: 3,
      unique_symbols: 3,
      overlap_symbols: 0,
    },
    {
      task_id: "intraday",
      label: "盘中",
      status: "已产出",
      candidate_count: 2,
      unique_symbols: 2,
      overlap_symbols: 1,
    },
  ],
  universe: { total: 5815, resolved: 5597, screened: 320, final: 2, max_universe: 0, coverage_pct: 0.9625 },
  variants: [],
  research_chain: {
    status: "linked",
    candidate_symbols: ["300750", "600036"],
    debated_symbols: ["300750"],
    pending_review_symbols: ["600036"],
    variant_candidate_symbols: ["300750"],
    variant_review_symbols: [],
    variant_holding_candidate_symbols: [],
    variant_holding_review_symbols: [],
    blocker: "",
  },
  meta: {
    historical: false,
    stale: false,
    freshness: { candidates: "fresh", messages: "fresh", cross_market: "fresh" },
  },
} satisfies AqspSnapshot;

const variantSuiteFixture = {
  schema_version: "v1",
  generated_at: "2026-09-11T15:00:00+08:00",
  data_mode: "historical_raw_unadjusted",
  end_date: "2026-08-12",
  variant_count: 1,
  selected_symbols: 4412,
  supported_symbols: 5597,
  batch_active: false,
  batch_id: "b1",
  batch_size: 500,
  cycle_id: 3,
  coverage_pct: 0.7883,
  filters: "",
  last_error: "",
} satisfies AqspVariantSuite;

const view = buildDailyView(dailyViewFixture);

export const dailyViewContract = {
  /* ---- 门禁口径：全站唯一来源，信息缺失不得被当成"放行" ---- */
  gateMissingIsNeutralNotReady: gateView(undefined).tone === "neutral",
  gateMissingSaysUnrecorded: gateView(undefined).label === "门禁状态未记录",
  gateFreshnessReasonIsMapped:
    gateView({ recommendation_allowed: false, status: "blocked", reasons: ["freshness_not_ready"] }).label ===
    "实时数据新鲜度未达标",
  gateCircuitBreakerReasonIsMapped:
    gateView({ recommendation_allowed: false, status: "blocked", reasons: ["circuit_breaker_daily"] }).label ===
    "组合保护处于冷却状态",
  gateResearchDisplayIsWarnNotReady:
    gateView({ recommendation_allowed: true, status: "research_display", reasons: ["research_display_override"] })
      .tone === "warn",
  gateExplicitAllowIsOk: gateView({ recommendation_allowed: true, status: "ready", reasons: [] }).tone === "ok",
  gateUnknownReasonFallsBackToObserve:
    gateView({ recommendation_allowed: false, status: "blocked", reasons: ["something_new"] }).label ===
    "当前结果仅供观察",

  /* ---- 页签必须覆盖全部正文分段（原 bug：候选段无入口） ---- */
  sectionsCoverEveryTab: view.sections.map((section) => section.id).join("|") === TODAY_SECTION_IDS.join("|"),
  sectionsAreThree: view.sections.length === 3,
  unknownHashFallsBackToFirstSection: resolveTodaySection("#nope") === DEFAULT_TODAY_SECTION,
  knownHashResolves: resolveTodaySection("#discussion") === "discussion",

  /* ---- 候选名与就绪判定 ---- */
  repeatedSymbolPrefixIsRemoved: view.candidates[0].name === "宁德时代",
  singleSymbolPrefixIsRemoved: view.candidates[1].name === "招商银行",
  readyCandidateIsMarkedReady: view.candidates[0].ready === true,
  candidateWithoutEvidenceIsNotReady: view.candidates[1].ready === false,
  chainIsWarnWhenPartiallyReady: view.chain.tone === "warn",
  chainDetailReportsRatio: view.chainDetail.includes("1/2"),
  keyMetricPrefersRet20: view.candidates[0].keyMetric === "20日涨幅 +18.4%",

  /* ---- 诚实回退：数据缺失不伪装成 0 ---- */
  missingVariantSuiteSaysUnrecorded:
    buildDailyView({ ...dailyViewFixture, variant_suite: undefined }).variantCoverageText === "覆盖率未记录",
  presentVariantSuiteShowsRealCoverage:
    buildDailyView({ ...dailyViewFixture, variant_suite: variantSuiteFixture }).variantCoverageText ===
    "4412/5597 · 78.8%",

  /* ---- 空态与边界 ---- */
  emptyCandidatesKeepSectionEmptyReason:
    buildDailyView({ ...dailyViewFixture, candidates: [], debates: [], messages: [] }).sections[0].empty !== null,
  emptyCandidatesMakeChainNeutral:
    buildDailyView({ ...dailyViewFixture, candidates: [], debates: [], messages: [] }).chain.tone === "neutral",
  emptyCandidatesCountIsZero: buildDailyView({ ...dailyViewFixture, candidates: [] }).sections[0].count === "0 个",
  emptyCandidatesHaveNoRows: buildDailyView({ ...dailyViewFixture, candidates: [] }).candidates.length === 0,

  /* ---- 其他派生 ---- */
  gateIsWarnForBlockedSnapshot: view.gate.tone === "warn",
  crossMarketThemeIsParsed: view.crossMarket !== null && view.crossMarket.theme.includes("海外储能需求"),
  phaseLaneAlwaysHasThreeSlots: view.phaseLanes.length === 3,
  unproducedPhaseIsFlagged: view.phaseLanes[2].produced === false,
  sourceCoverageOnlyKeepsCoverageLines: view.sourceCoverage.length === 2,
  messageKeepsSourceUrl: view.messages[0].sourceUrl === "https://example.test/news/1",
  bullishMessageIsToneOk: view.messages[0].impactTone === "ok",
  historicalSnapshotIsFlagged:
    buildDailyView({ ...dailyViewFixture, meta: { historical: true, stale: true } }).isHistorical === true,
  symbolNamesJoinsCodeAndName:
    symbolNames(["300750"], [{ symbol: "300750", display_name: "300750 300750 宁德时代" }]) ===
    "300750 宁德时代",
};
