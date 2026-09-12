// 候选筛选与排序的契约断言（由 npm test 真正执行）。
import { selectCandidates, strategyOptions } from "./candidate-view";
import type { CandidateRow } from "./daily-view";

const row = (over: Partial<CandidateRow>): CandidateRow =>
  ({
    symbol: "000000",
    name: "X",
    score: 50,
    scoreText: "50",
    status: "通过筛选",
    evidence: "已闭环",
    ready: false,
    strategies: "",
    keyMetric: "",
    messageCount: 0,
    firstMessageTitle: "",
    debateRounds: null,
    debateConclusion: "",
    variantCount: 0,
    nextStep: "",
    context: "",
    reasons: [],
    metrics: [],
    breakdown: [],
    provenance: "",
    ...over,
  }) as CandidateRow;

const rows: CandidateRow[] = [
  row({ symbol: "000001", score: 60, strategies: "放量突破 · RPS 相对强度", messageCount: 3 }),
  row({ symbol: "000002", score: 90, ready: true, strategies: "均线缩量回踩", messageCount: 1 }),
  row({ symbol: "000003", score: 75, strategies: "放量突破", messageCount: 5 }),
];

const base = { sort: "score" as const, onlyReady: false, strategy: null };

export const candidateViewContract = {
  /* ---- 策略选项 ---- */
  // 顺序以实际 zh-CN 排序为准（不是"拉丁字母排前面"那种想当然）
  strategiesSplitFromString: strategyOptions(rows).join("|") === "放量突破|均线缩量回踩|RPS 相对强度",
  strategiesDeduplicated: new Set(strategyOptions(rows)).size === strategyOptions(rows).length,
  strategiesEmptyWhenNoRows: strategyOptions([]).length === 0,
  strategiesEmptyWhenBlankField: strategyOptions([row({ strategies: "  " })]).length === 0,

  /* ---- 默认按评分降序 ---- */
  scoreSortDescending: selectCandidates(rows, base).rows.map((r) => r.symbol).join("|") === "000002|000003|000001",
  noFilterKeepsAll: selectCandidates(rows, base).hidden === 0,
  totalIsOriginalCount: selectCandidates(rows, base).total === 3,

  /* ---- 只看可复核 ---- */
  onlyReadyFilters: selectCandidates(rows, { ...base, onlyReady: true }).rows.length === 1,
  onlyReadyKeepsTheReadyOne:
    selectCandidates(rows, { ...base, onlyReady: true }).rows[0].symbol === "000002",
  // 被筛掉的数量必须如实报告，否则用户会以为只有这些候选
  onlyReadyReportsHidden: selectCandidates(rows, { ...base, onlyReady: true }).hidden === 2,

  /* ---- 按策略过滤 ---- */
  strategyFilterMatches:
    selectCandidates(rows, { ...base, strategy: "放量突破" }).rows.map((r) => r.symbol).join("|") === "000003|000001",
  unknownStrategyYieldsEmpty: selectCandidates(rows, { ...base, strategy: "不存在的策略" }).rows.length === 0,
  unknownStrategyReportsAllHidden:
    selectCandidates(rows, { ...base, strategy: "不存在的策略" }).hidden === 3,

  /* ---- 可复核优先排序 ---- */
  readySortPutsReadyFirst:
    selectCandidates(rows, { ...base, sort: "ready" }).rows[0].symbol === "000002",
  // 同为可复核（或同为仅观察）时，仍按评分降序
  readySortThenScore:
    selectCandidates(rows, { ...base, sort: "ready" }).rows.map((r) => r.symbol).join("|") === "000002|000003|000001",

  /* ---- 按证据数排序 ---- */
  evidenceSortDescending:
    selectCandidates(rows, { ...base, sort: "evidence" }).rows.map((r) => r.symbol).join("|") === "000003|000001|000002",
  evidenceSortTiesBreakByScore:
    selectCandidates(rows, { ...base, sort: "evidence" }).rows[0].messageCount === 5,

  /* ---- 额外排除（隐藏已持有等）---- */
  excludeRemovesMatching:
    selectCandidates(rows, { ...base, exclude: (r) => r.symbol === "000002" }).rows.length === 2,
  excludeReportsHidden: selectCandidates(rows, { ...base, exclude: () => true }).hidden === 3,
  excludeComposesWithOtherFilters:
    selectCandidates(rows, {
      ...base,
      onlyReady: true,
      exclude: (r) => r.symbol === "000002",
    }).rows.length === 0,
  noExcludeKeepsAll: selectCandidates(rows, base).hidden === 0,

  /* ---- 边界 ---- */
  emptyRowsSafe: selectCandidates([], base).rows.length === 0,
  emptyRowsTotalZero: selectCandidates([], base).total === 0,
  combinationFilterWorks:
    selectCandidates(rows, { sort: "score", onlyReady: true, strategy: "均线缩量回踩" }).rows.length === 1,
};
