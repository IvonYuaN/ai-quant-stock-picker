import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { ResearchValidationContext } from "@/components/aqsp/ResearchValidationContext";
import type { AqspSnapshot } from "@/types/aqsp";
import { researchValidationContext } from "./research-validation-context";

const now = Date.parse("2026-10-27T10:00:00+08:00");
const snapshot: AqspSnapshot = {
  schema_version: "v1", selected_date: "2026-10-27", available_dates: ["2026-10-27", "2026-10-26"],
  generated_at: "2026-10-27T09:55:00+08:00", stale_after: "2026-10-27T10:25:00+08:00",
  source: { effective: "tencent", latest_trade_date: "2026-10-27", lag_days: 0, status: "fresh" },
  coldstart: { status: "ready", detail: "" }, summaries: [], messages: [], message_status: "partial", market_context: null,
  meta: { historical: false, stale: false },
  candidates: [{ symbol: "000001", display_name: "示例公司", score: 75, research_status: "watch", evidence_status: "pending",
    next_step: "等反证", context: "", strategies: ["momentum"], deterministic_reasons: ["有源依据"],
    freshness: "fresh", data_fetched_at: "2026-10-27T09:53:00+08:00" }],
  debates: [],
  recommendation_gate: { recommendation_allowed: false, status: "research_validation_not_ready", reasons: ["当天候选尚未验证"] },
  variant_suite: { schema_version: "v1", generated_at: "2026-10-26T22:34:00+08:00", end_date: "2026-10-26", data_mode: "historical_raw_unadjusted",
    variant_count: 6, selected_symbols: 500, supported_symbols: 5029, batch_active: true, batch_id: "b", batch_size: 500, cycle_id: 1, coverage_pct: 0.1, filters: "", last_error: "" },
  research_chain: { status: "waiting_validation", candidate_symbols: ["000001"], debated_symbols: [], pending_review_symbols: ["000001"],
    variant_candidate_symbols: [], variant_review_symbols: [], variant_holding_candidate_symbols: [], variant_holding_review_symbols: [],
    blocker: "变体结果截至 2026-10-26，不作为当天结论的验证证据。" },
};
const build = (changes: Partial<AqspSnapshot> = {}, symbol = "000001") => researchValidationContext({ ...snapshot, ...changes }, symbol, now);
const currentSuite = { ...snapshot.variant_suite!, end_date: "2026-10-27" };
const recordedDebate = { symbol: "000001", display_name: "示例公司", conclusion: "待反证", primary_risk_gate: "", next_trigger: "", active_roles: ["支持", "反证"], round_count: 2, bull_count: 0, bear_count: 0, neutral_count: 0, process_summary: "已记录" };
const recordedChain = { ...snapshot.research_chain!, debated_symbols: ["000001"], pending_review_symbols: [] };
const current = build();
const html = renderToStaticMarkup(createElement(ResearchValidationContext, { snapshot, symbol: "000001", now }));
const serializedBefore = JSON.stringify(snapshot);
build();
const readyGate = { recommendation_allowed: true, status: "ready", reasons: [] };
const acceptedStates = ["fresh", "current", "realtime", "实时", "新鲜"];
const rejectedStates = ["", "unknown", "failed", "stale", "失败", "过期", "ready", "partial", "toString"];

export const researchValidationContextChecks = {
  yesterdayResultRetainsActualDate: current.history.date === "2026-10-26" && current.history.status === "历史结果已生成",
  todayCandidateRetainsOwnDate: current.candidate.date === "2026-10-27" && current.candidate.validation === "当日候选验证待完成",
  dateDifferenceInPrimaryExplanation: current.summary.includes("2026-10-26") && current.summary.includes("2026-10-27"),
  historyGenerationDoesNotClaimPassed: current.history.detail.includes("结果生成不等于验证通过"),
  historyParameterIdentityNotInvented: current.parameterDetail.includes("未提供可比对的参数指纹"),
  missingDiscussionSeparateFromValidation: current.candidate.discussion === "同日多空讨论待补" && current.gaps.some((gap) => gap.includes("多空讨论")),
  observedCandidateMayContinueExplainingDefer: current.nextStep.includes("AI") && current.nextStep.includes("暂缓观察") && current.nextStep.includes("仍须等待正式推荐门禁"),
  priorDayResultsCannotClaimTodayCoverage: build({ research_chain: { ...recordedChain, variant_candidate_symbols: ["000001"] }, debates: [recordedDebate] }).candidate.validation === "当日候选验证待完成",
  sameDayResultsDoNotCoverMissingCandidate: build({ variant_suite: currentSuite, research_chain: recordedChain, debates: [recordedDebate] }).candidate.validation === "当日候选验证待完成",
  sameDayCoverageDescribesExperimentOnly: build({ variant_suite: currentSuite, research_chain: { ...recordedChain, variant_candidate_symbols: ["000001"] } }).candidate.validation === "已进入当日实验池 · 不等于验证通过",
  completedDiscussionDoesNotChangeGate: build({ debates: [recordedDebate], research_chain: recordedChain }).recommendation.status === "正式推荐未放行",
  debateWithoutChainDoesNotClaimComplete: build({ debates: [recordedDebate] }).candidate.discussion === "同日多空讨论待补",
  chainWithoutDebateDoesNotClaimComplete: build({ research_chain: recordedChain }).candidate.discussion === "同日多空讨论待补",
  pendingOverridesDebatePresence: build({ debates: [recordedDebate], research_chain: { ...recordedChain, pending_review_symbols: ["000001"] } }).candidate.discussion === "同日多空讨论待补",
  actualRecordedDiscussionIsVisible: build({ debates: [recordedDebate], research_chain: recordedChain }).candidate.discussion === "同日多空讨论已记录",
  futureResultCannotBeHistoricalEvidence: build({ variant_suite: { ...currentSuite, end_date: "2026-10-28" } }).history.status === "日期不匹配",
  invalidCalendarDateDoesNotThrowOrValidate: build({ variant_suite: { ...currentSuite, end_date: "2026-99-99" } }).history.status === "结果待补",
  normalizedInvalidCalendarDateRejected: build({ variant_suite: { ...currentSuite, end_date: "2026-02-30" } }).history.date === "未记录",
  missingSuiteCannotUseCandidateScoreAsValidation: build({ variant_suite: undefined }).history.status === "结果待补",
  emptyResultsNotCompleted: build({ variant_suite: { ...currentSuite, variant_count: 0 } }).history.status === "结果待补",
  failedSuitePreservesFailure: build({ variant_suite: { ...currentSuite, last_error: "验证产物校验失败" } }).gaps.includes("验证产物校验失败"),
  oldResultsNeverBecomeTodayResults: build({ variant_suite: { ...currentSuite, end_date: "2025-01-01" } }).candidate.validation === "当日候选验证待完成",
  researchDisplayOverrideDoesNotClaimRecommendation: build({ recommendation_gate: { recommendation_allowed: true, status: "research_display", reasons: ["research_display_override"] } }).recommendation.status === "正式推荐未放行",
  missingGateIsExplicit: build({ recommendation_gate: undefined }).recommendation.status === "推荐状态未取得",
  freshReadyGateRetainsItsMeaning: build({ recommendation_gate: { recommendation_allowed: true, status: "ready", reasons: [] } }).recommendation.status === "原门禁已放行 · 仍需人工复核",
  readyGateDoesNotTellUserToWaitForAlreadyCompletedGate: !build({ recommendation_gate: { recommendation_allowed: true, status: "ready", reasons: [] } }).nextStep.includes("等待正式推荐门禁"),
  expiredSnapshotDoesNotDisplayReady: build({ stale_after: "2026-10-27T09:59:00+08:00", recommendation_gate: { recommendation_allowed: true, status: "ready", reasons: [] } }).recommendation.status === "依据失效 · 仅供观察",
  failedSourcesDoNotSuggestAdoptingEntry: build({ source: { ...snapshot.source, status: "failed" } }).nextStep.includes("不据此采用入场条件"),
  staleCandidateDoesNotDisplayReady: build({ candidates: [{ ...snapshot.candidates[0], freshness: "stale" }], recommendation_gate: { recommendation_allowed: true, status: "ready", reasons: [] } }).recommendation.status === "依据待核实 · 仅供观察",
  candidateFreshnessUsesBackendVocabulary: acceptedStates.map((freshness) => build({ candidates: [{ ...snapshot.candidates[0], freshness }], recommendation_gate: readyGate }).recommendation.status === "原门禁已放行 · 仍需人工复核"),
  overallSourceUsesBackendVocabulary: acceptedStates.map((status) => build({ source: { ...snapshot.source, status }, recommendation_gate: readyGate }).recommendation.status === "原门禁已放行 · 仍需人工复核"),
  unknownAndChineseFailureSourcesNeverClaimValid: rejectedStates.map((status) => build({ source: { ...snapshot.source, status }, recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察"),
  unknownCandidateFreshnessNeverClaimsValid: rejectedStates.filter(Boolean).map((freshness) => build({ candidates: [{ ...snapshot.candidates[0], freshness }], recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察"),
  absentCandidateFreshnessFallsBackToAuthoritativeSource: build({ candidates: [{ ...snapshot.candidates[0], freshness: "" }], source: { ...snapshot.source, status: "realtime" }, recommendation_gate: readyGate }).recommendation.status === "原门禁已放行 · 仍需人工复核",
  uppercaseCurrentSourceIsAccepted: build({ source: { ...snapshot.source, status: "CURRENT" }, candidates: [{ ...snapshot.candidates[0], freshness: "REALTIME" }], recommendation_gate: readyGate }).recommendation.status === "原门禁已放行 · 仍需人工复核",
  missingCandidateTimeCannotClaimValid: build({ candidates: [{ ...snapshot.candidates[0], data_fetched_at: undefined }], recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  futureCandidateTimeCannotClaimValid: build({ candidates: [{ ...snapshot.candidates[0], data_fetched_at: "2026-10-27T10:00:01+08:00" }], recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  naiveCandidateTimeCannotClaimValid: build({ candidates: [{ ...snapshot.candidates[0], data_fetched_at: "2026-10-27T09:53:00" }], recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  invalidCandidateTimeCannotClaimValid: build({ candidates: [{ ...snapshot.candidates[0], data_fetched_at: "2026-02-30T09:53:00+08:00" }], recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  missingGenerationTimeCannotClaimValid: build({ generated_at: "", recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  futureGenerationTimeCannotClaimValid: build({ generated_at: "2026-10-27T10:00:01+08:00", recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  naiveGenerationTimeCannotClaimValid: build({ generated_at: "2026-10-27T09:55:00", recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  naiveDeadlineCannotClaimValid: build({ stale_after: "2026-10-27T10:25:00", recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  sourceIssueExplainsMissingTime: build({ candidates: [{ ...snapshot.candidates[0], data_fetched_at: "" }] }).gaps.some((gap) => gap.includes("候选取数时点缺失")),
  unknownSourceOnlyAffectsPresentation: (() => {
    const input = { ...snapshot, source: { ...snapshot.source, status: "unknown" }, recommendation_gate: readyGate };
    researchValidationContext(input, "000001", now);
    return input.recommendation_gate.recommendation_allowed && input.recommendation_gate.status === "ready";
  })(),
  circuitBreakerReasonRemainsExplicit: build({ recommendation_gate: { recommendation_allowed: false, status: "blocked", reasons: ["circuit_breaker_daily"] } }).recommendation.reasons.some((reason) => reason.includes("熔断冷静期")),
  allGateReasonsRemainVisible: build({ recommendation_gate: { recommendation_allowed: false, status: "blocked", reasons: ["候选待验证", "circuit_breaker_daily"] } }).recommendation.reasons.length === 2,
  parameterIdentityNeverAssertsHistoryMatch: build({ parameter_snapshot: { version: "v3", effective_from: "2026-10-01", fingerprint: "a".repeat(64) } }).parameterDetail.includes("不能证明参数相同"),
  historicalPageNeverClaimsCurrentPermission: build({ meta: { historical: true, stale: true }, recommendation_gate: { recommendation_allowed: true, status: "ready", reasons: [] } }).recommendation.status === "历史门禁记录 · 仅供回看",
  historicalPagePointsBackToCurrentResearch: build({ meta: { historical: true, stale: true } }).nextStep.includes("返回今日研究"),
  nonCandidateHasNoFalseDiscussion: build({}, "600000").candidate.discussion === "无同日候选记录",
  builderNeverMutatesGateOrSnapshot: JSON.stringify(snapshot) === serializedBefore && snapshot.recommendation_gate?.recommendation_allowed === false,
  renderShowsBothDatesAndSeparateStates: html.includes("历史策略验证 · 截至 2026-10-26") && html.includes("候选研究日 · 2026-10-27") && html.includes("当日候选验证待完成") && html.includes("正式推荐未放行"),
  renderGapsExpandableRatherThanRepeatedWarnings: html.includes("<details") && html.includes("查看验证依据与缺口") && html.includes("还缺什么"),
  renderKeepsFetchTimeDistinctFromHistoryDate: html.includes("候选数据取回") && html.includes("取回时间不代表历史验证截至日"),
  explanationHasNoMutatingControls: !html.includes("<button") && !html.includes("<form"),
};
