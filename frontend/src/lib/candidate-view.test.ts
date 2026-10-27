// 候选筛选与排序的契约断言（由 npm test 真正执行）。
import { groupCandidatesByOwnership, selectCandidates, strategyCounts, strategyOptions } from "./candidate-view";
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
const personalRows = Array.from({ length: 20 }, (_, index) => row({
  symbol: String(600001 + index), score: 100 - index,
}));
const ownership = {
  holdings: new Set(["600018", "600020"]),
  watchlist: new Set(["600014", "600016", "600019", "600020"]),
};
const personal = { ...base, sort: "personal" as const, ownership };
export const unknownOwnershipDoesNotFilterToFalseEmpty = selectCandidates(rows, { ...base, onlyOwned: true, ownership: { ...ownership, holdingsKnown: false } }).rows.length === rows.length;
export const unknownOwnershipDoesNotKeepStaleHoldingMarks = selectCandidates(personalRows, { ...personal, ownership: { ...ownership, holdingsKnown: false } }).rows.every((row) => !row.hit_position);
const personalGroups = groupCandidatesByOwnership(selectCandidates(personalRows, personal).rows);

export const candidateViewContract = {
  personalDisplayHasExplicitOwnershipGroups:
    personalGroups.map((group) => group.kind).join("|") === "holding|watchlist|other",
  personalDisplaySeparatesTwoHoldingsAndThreeWatchlistMatches:
    personalGroups[0].rows.length === 2 && personalGroups[1].rows.length === 3,
  personalDisplayGroupsKeepFrozenInputOrder:
    personalGroups.flatMap((group) => group.rows.map((item) => item.symbol)).join("|") ===
    "600018|600020|600014|600016|600019|" +
    personalRows.filter((item) => !ownership.holdings.has(item.symbol) && !ownership.watchlist.has(item.symbol)).map((item) => item.symbol).join("|"),
  personalSortPutsTwoHoldingsAndThreeWatchlistInFirstFive:
    selectCandidates(personalRows, personal).rows.slice(0, 5).map((item) => item.symbol).join("|") === "600018|600020|600014|600016|600019",
  holdingOutranksWatchlistWhenAlsoWatched:
    selectCandidates(personalRows, personal).rows[1].symbol === "600020",
  personalRowsCarryExplicitOwnershipFlags:
    selectCandidates(personalRows, personal).rows[0].hit_position === true &&
    selectCandidates(personalRows, personal).rows[2].hit_watchlist === true,
  dualOwnedRowsKeepBothFactsButRankAsHolding:
    selectCandidates(personalRows, personal).rows[1].hit_position === true &&
    selectCandidates(personalRows, personal).rows[1].hit_watchlist === true,
  personalSortPreservesFrozenOrderWithinAllOtherCandidates:
    selectCandidates(personalRows, personal).rows.slice(5).map((item) => item.symbol).join("|") === personalRows.filter((item) => !ownership.holdings.has(item.symbol) && !ownership.watchlist.has(item.symbol)).map((item) => item.symbol).join("|"),
  personalSortWithNoOwnershipPreservesOriginalOrder:
    selectCandidates(rows, { ...base, sort: "personal" }).rows.map((item) => item.symbol).join("|") === "000001|000002|000003",
  personalSortWithNoMatchesPreservesOriginalOrder:
    selectCandidates(rows, personal).rows.map((item) => item.symbol).join("|") === "000001|000002|000003",
  onlyOwnedKeepsFiveMatchingCandidates:
    selectCandidates(personalRows, { ...personal, onlyOwned: true }).rows.length === 5,
  onlyOwnedReportsHiddenCount:
    selectCandidates(personalRows, { ...personal, onlyOwned: true }).hidden === 15,
  missingOwnershipSafelyProducesNoMatches:
    selectCandidates(rows, { ...base, onlyOwned: true }).rows.length === 0,
  personalSortAndExclusionCompose:
    selectCandidates(personalRows, { ...personal, onlyOwned: true, exclude: (item) => ownership.holdings.has(item.symbol) }).rows.length === 3,
  /* ---- 策略选项 ---- */
  // 顺序以实际 zh-CN 排序为准（不是"拉丁字母排前面"那种想当然）
  strategiesSplitFromString: strategyOptions(rows).join("|") === "放量突破|均线缩量回踩|RPS 相对强度",
  strategiesDeduplicated: new Set(strategyOptions(rows)).size === strategyOptions(rows).length,
  strategiesEmptyWhenNoRows: strategyOptions([]).length === 0,
  strategiesEmptyWhenBlankField: strategyOptions([row({ strategies: "  " })]).length === 0,

  /* ---- 策略 × 计数（分策略选股 chip 的数据源，issue #317）---- */
  // 与 strategyOptions 同一套拆分规则 ⇒ chip 集合与过滤命中必然一致
  countsMatchOptions: strategyCounts(rows).map((c) => c.name).join("|") === strategyOptions(rows).join("|"),
  // 000001 挂两个策略 ⇒「放量突破」计 2、「均线缩量回踩」计 1、「RPS 相对强度」计 1
  countsCountCandidates:
    JSON.stringify(strategyCounts(rows).map((c) => [c.name, c.count])) ===
    JSON.stringify([["放量突破", 2], ["均线缩量回踩", 1], ["RPS 相对强度", 1]]),
  // 候选数多的策略排前面（点选率最高的一等入口），同数按 zh-CN 字典序
  countsSortedByCountDesc:
    strategyCounts(rows)[0].name === "放量突破" && strategyCounts(rows)[0].count === 2,
  countsEmptyWhenNoRows: strategyCounts([]).length === 0,
  countsEmptyWhenBlankField: strategyCounts([row({ strategies: "  " })]).length === 0,

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
