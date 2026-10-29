// 候选并排对比的契约断言（由 npm test 真正执行）。
import {
  CANDIDATE_METRICS,
  MAX_COMPARE_SYMBOLS,
  buildCandidateDecisionComparison,
  buildCompareMatrix,
  parseCompareSymbols,
  toggleCompareSymbol,
} from "./candidate-compare";
import type { CandidateRow } from "./daily-view";
import type { CandidateWorkspace } from "./api";

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
  row({ symbol: "000001", name: "A", score: 60, scoreText: "60", ready: false, messageCount: 3, debateRounds: 2, variantCount: 1, keyMetric: "20日涨幅 +6.0%", nextStep: "等回踩" }),
  row({ symbol: "000002", name: "B", score: 90, scoreText: "90", ready: true, messageCount: 1, debateRounds: null, variantCount: 4, keyMetric: "20日涨幅 +18.4%" }),
];

const available = ["000001", "000002", "000003"];
const matrix = buildCompareMatrix(rows, ["000002", "000001"]);
const researchDate = "2026-10-29";
const market = { status: "strong", summary: "市场宽度偏强", source: "全A来源", as_of: "2026-10-29T10:00:00+08:00" };
const workspace = (symbol: string, patch: Partial<CandidateWorkspace> = {}): CandidateWorkspace => ({
  symbol, name: symbol, version: 1, research_date: researchDate, evidence_version: "basis", available: true,
  entry_blocked: false, new_entry_blocked: false, issues: [], quote_issue: "",
  evidence: { market, sector: { status: "rising", industry: "电池", source: "行业源", as_of: market.as_of } },
  watch: null, observation: null, discussion: null, ...patch,
} as CandidateWorkspace);
const decisionRows = [row({ symbol: "000001", name: "甲", ready: true, reasons: ["量价研究支持"] }), row({ symbol: "000002", name: "乙", ready: true })];
const workspaces = { "000001": workspace("000001"), "000002": workspace("000002", { new_entry_blocked: true, new_entry_block_reason: "实验覆盖待补" }) };
const decision = buildCandidateDecisionComparison(decisionRows, ["000001", "000002"], workspaces, researchDate, false);
const completeDiscussion = { status: "complete", id: "discussion-1", generated_at: "2026-10-29T10:01:00+08:00",
  evidence_version: "basis", resource_version: 1, research_date: researchDate, summary: { disagreements: ["个股量能仍有分歧"] } } as CandidateWorkspace["discussion"];

export const candidateCompareContract = {
  /* ---- 解析与开关 ---- */
  parsesKnownSymbols: parseCompareSymbols("000001,000002", available).join("|") === "000001|000002",
  dropsUnknownSymbols: parseCompareSymbols("000001,999999", available).join("|") === "000001",
  dedupesRepeated: parseCompareSymbols("000001,000001", available).length === 1,
  nullYieldsEmpty: parseCompareSymbols(null, available).length === 0,
  respectsCap: parseCompareSymbols(available.join(","), available).length <= MAX_COMPARE_SYMBOLS,
  toggleAdds: toggleCompareSymbol(["000001"], "000002").join("|") === "000001|000002",
  toggleRemoves: toggleCompareSymbol(["000001", "000002"], "000002").join("|") === "000001",
  // 满了不动，不静默丢弃
  toggleRespectsMax: toggleCompareSymbol(["000001", "000002"], "000003", 2).length === 2,
  toggleAllowsBelowMax: toggleCompareSymbol(["000001"], "000002", 2).length === 2,

  /* ---- 矩阵 ---- */
  // 列顺序跟随传入顺序（用户点选顺序），而不是候选列表顺序
  columnOrderFollowsInput: matrix.columns.map((c) => c.symbol).join("|") === "000002|000001",
  columnsCarryNames: matrix.columns[0].name === "B",
  metricCountMatchesDefs: matrix.metrics.length === CANDIDATE_METRICS.length,
  valuesIndexedByMetricThenSymbol: matrix.values.score["000002"] === "90",
  readyIsHumanReadable: matrix.values.ready["000002"].includes("可进入纸面复核"),
  notReadyIsHonest: matrix.values.ready["000001"].includes("仅观察"),
  missingDebateSaysSo: matrix.values.debate["000002"] === "未讨论",
  messageCountFormatted: matrix.values.messages["000001"] === "3 条",
  emptyFieldFallsBack: matrix.values.nextStep["000002"] === "—",

  /* ---- 边界 ---- */
  unknownSymbolDropped: buildCompareMatrix(rows, ["000001", "999999"]).columns.length === 1,
  emptySelectionNoColumns: buildCompareMatrix(rows, []).columns.length === 0,
  emptyRowsNoColumns: buildCompareMatrix([], ["000001"]).columns.length === 0,
  missingValueIsDash: buildCompareMatrix([], []).values.score !== undefined,
  sharedMarketAllowsLimitedContrast: decision.comparable && decision.contextSource.includes("全A") && decision.conclusion.includes("甲可先核对"),
  counterExplainsWhySecondWaits: decision.cards[1].counter === "实验覆盖待补" && !decision.cards[1].researchable,
  originalReasonIsLabeledFrozen: decision.cards[0].support === "原研究：量价研究支持",
  sectorKeepsItsOwnSource: decision.cards[0].sector.includes("行业源"),
  scoreDoesNotForceTieBreak: buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000002": workspace("000002") }, researchDate, false).conclusion.includes("不能只凭评分"),
  differentMarketTimeRejectsRanking: !buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000002": workspace("000002", { evidence: { market: { ...market, as_of: "2026-10-29T10:01:00+08:00" } } }) }, researchDate, false).comparable,
  equivalentMarketInstantAllowsContrast: buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000002": workspace("000002", { evidence: { market: { ...market, as_of: "2026-10-29T02:00:00Z" } } }) }, researchDate, false).comparable,
  missingSourceRejectsRanking: !buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000002": workspace("000002", { evidence: { market: { ...market, source: "" } } }) }, researchDate, false).comparable,
  historicalNeverBorrowsCurrentEvidence: (() => { const result = buildCandidateDecisionComparison(decisionRows, ["000001", "000002"], workspaces, researchDate, true); return !result.comparable && result.cards[0].counter.includes("历史反证") && !result.cards[0].sector.includes("行业源"); })(),
  unknownMarketStatusRejectsRanking: !buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000002": workspace("000002", { evidence: { market: { ...market, status: "made_up" } } }) }, researchDate, false).comparable,
  wrongResearchDateNeverBorrowsCurrentEvidence: !buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000002": workspace("000002", { research_date: "2026-10-28" }) }, researchDate, false).comparable,
  incompleteDiscussionNotPresentedAsDecision: buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000001": workspace("000001", { discussion: { status: "partial" } as CandidateWorkspace["discussion"] }) }, researchDate, false).cards[0].debate.includes("不能采用"),
  currentCompleteDiscussionShowsActualDisagreement: buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000001": workspace("000001", { discussion: completeDiscussion }) }, researchDate, false).cards[0].debate === "个股量能仍有分歧",
  oldResourceVersionDoesNotBecomeCurrentDebate: buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000001": workspace("000001", { version: 2, discussion: completeDiscussion }) }, researchDate, false).cards[0].debate === "当前 AI 讨论尚未完整可用",
  partialLoadingShowsKnownStockAndWaitsForOther: (() => { const result = buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { "000001": workspace("000001") }, researchDate, false); return !result.comparable && result.cards[0].sector.includes("行业源") && result.cards[1].counter.includes("读取中"); })(),
  adoptedWatchShowsBackgroundFollowup: buildCandidateDecisionComparison(decisionRows, ["000001", "000002"],
    { ...workspaces, "000001": workspace("000001", { watch: { status: "active" } as CandidateWorkspace["watch"], observation: { title: "条件变化需复核" } as CandidateWorkspace["observation"] }) }, researchDate, false).cards[0].next.includes("后台跟进：条件变化需复核"),
};
