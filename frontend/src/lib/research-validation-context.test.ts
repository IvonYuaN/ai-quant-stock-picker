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
const refreshSuite = { ...snapshot.variant_suite!, refresh_status: "staged", refresh_generated_at: "2026-10-27T09:58:00+08:00",
  refresh_end_date: "2026-10-27", refresh_detail: "当前分段已保存，等待后续分段", profiles_staged: 2, profiles_total: 6 };
const refreshed = (patch: Partial<typeof refreshSuite> = {}) => build({ variant_suite: { ...refreshSuite, ...patch } });
const sourceRun = { ...snapshot.source, task_id: "intraday", run_status: "failed", run_updated_at: "2026-10-27T09:58:00+08:00", run_detail: "本轮研究未完成" };
const refreshedHtml = renderToStaticMarkup(createElement(ResearchValidationContext, { snapshot: { ...snapshot, variant_suite: refreshSuite, source: sourceRun }, symbol: "000001", now }));
const archiveHtml = renderToStaticMarkup(createElement(ResearchValidationContext, { snapshot: { ...snapshot, variant_suite: refreshSuite, source: sourceRun, meta: { historical: true, stale: false } }, symbol: "000001", now }));

export const researchValidationContextChecks = {
  stagedRefreshDoesNotHideHistoricalResult: refreshed().history.date === "2026-10-26" && refreshed().history.status === "历史结果已生成" && refreshed().refresh?.status === "当日验证分段进行中",
  refreshProgressIsStrategyProfilesOnly: refreshed().refresh?.progress.includes("2/6 组策略配置已暂存") && refreshed().refresh?.progress.includes("不是候选覆盖数"),
  refreshPrimarySummaryExposesStaging: refreshed().summary.includes("分段进行中") && refreshed().summary.includes("2026-10-26"),
  allWriterRefreshStatesHaveHonestLabels: [
    ["waiting", "当日验证等待中"], ["staged", "当日验证分段进行中"], ["completed", "当日验证运行已结束"],
    ["timed_out", "当日验证本轮超时"], ["skipped_lock", "当日验证等待运行资源"], ["rejected", "当日验证本轮未被接纳"], ["failed", "当日验证本轮失败"],
  ].map(([refresh_status, label]) => refreshed({ refresh_status }).refresh?.status === label),
  failedRefreshDoesNotCorruptOldResults: refreshed({ refresh_status: "failed", refresh_detail: "本轮超时" }).history.status === "历史结果已生成" && !refreshed({ refresh_status: "failed" }).gaps.some((gap) => gap.includes("损坏")),
  refreshFailureRemainsVisibleAlongsideOldSuccess: refreshed({ refresh_status: "failed" }).summary.includes("本轮失败") && refreshed({ refresh_status: "failed" }).history.detail.includes("6 个纸面变体"),
  completedRefreshDoesNotPromoteOldCandidateCoverage: refreshed({ refresh_status: "completed" }).candidate.validation === "当日候选验证待完成" && refreshed({ refresh_status: "completed" }).recommendation.status === "正式推荐未放行",
  completedRefreshDoesNotInventMissingCoverage: build({ variant_suite: { ...refreshSuite, refresh_status: "completed", end_date: "2026-10-27" } }).candidate.validation === "当日候选验证待完成",
  refreshCompletionExplicitlyNotValidationSuccess: refreshed({ refresh_status: "completed" }).refresh?.detail.includes("运行结束不等于这只候选已覆盖或正式推荐已放行"),
  absentRefreshRecordCannotBeCalledWaiting: current.refresh?.status === "当日验证进度待核实",
  unknownRefreshStatesRemainUnknown: ["unknown", "", "toString", "future_status"].map((refresh_status) => refreshed({ refresh_status }).refresh?.status === "当日验证运行状态未知"),
  invalidProfileCountersNeverClaimProgress: [{ profiles_staged: -1 }, { profiles_staged: 7 }, { profiles_staged: 1.5 }, { profiles_total: 0 }, { profiles_total: Number.NaN }].map((patch) => refreshed(patch).refresh?.progress === ""),
  missingRefreshClockDoesNotClaimCurrent: refreshed({ refresh_generated_at: "" }).refresh?.status === "当日验证进度待核实",
  naiveRefreshClockDoesNotClaimCurrent: refreshed({ refresh_generated_at: "2026-10-27T09:58:00" }).refresh?.status === "当日验证进度待核实",
  futureRefreshClockDoesNotClaimCurrent: refreshed({ refresh_generated_at: "2026-10-27T10:00:01+08:00" }).refresh?.status === "当日验证进度待核实",
  impossibleRefreshClockDoesNotClaimCurrent: refreshed({ refresh_generated_at: "2026-02-30T09:58:00+08:00" }).refresh?.status === "当日验证进度待核实",
  previousDayRefreshIsHistorical: refreshed({ refresh_generated_at: "2026-10-26T23:58:00+08:00", refresh_end_date: "2026-10-26" }).refresh?.status === "历史验证刷新记录",
  previousDayRunWithCurrentTargetIsNotCurrent: refreshed({ refresh_generated_at: "2026-10-26T23:58:00+08:00" }).refresh?.status === "验证刷新日期不匹配",
  currentRunWithPreviousOrFutureTargetIsNotCurrent: ["2026-10-26", "2026-10-28"].map((refresh_end_date) => refreshed({ refresh_end_date }).refresh?.status === "验证刷新日期不匹配"),
  invalidTargetDateDoesNotClaimCurrent: refreshed({ refresh_end_date: "2026-02-30" }).refresh?.status === "当日验证进度待核实",
  refreshDayUsesShanghaiRatherThanIsoPrefix: refreshed({ refresh_generated_at: "2026-10-26T17:58:00Z" }).refresh?.status === "当日验证分段进行中",
  historicalPageOmitsCurrentRefreshAndSourceRun: build({ meta: { historical: true, stale: false }, variant_suite: refreshSuite, source: sourceRun }).refresh === null && build({ meta: { historical: true, stale: false }, variant_suite: refreshSuite, source: sourceRun }).sourceRun === null,
  sourceRunFailureIsSeparateFromQuoteFreshness: build({ source: sourceRun }).sourceRun?.status === "研究任务失败" && !build({ source: sourceRun }).gaps.includes("总体行情来源未确认为当前有效状态。") && sourceRun.status === "fresh",
  sourceRunCompletionCannotBypassRecommendationGate: build({ source: { ...sourceRun, run_status: "completed" } }).recommendation.status === "正式推荐未放行",
  failedRunNeverDisplaysOldReadyGate: ["failed", "error", "partial_failed", "unknown", "unrecognized"].map((run_status) => {
    const view = build({ source: { ...sourceRun, run_status }, recommendation_gate: readyGate });
    return view.recommendation.status === "研究任务待核实 · 仅供观察" && view.nextStep.includes("暂不据此采用入场条件") &&
      view.gaps.some((gap) => gap.includes("有效运行记录")) && !view.gaps.includes("总体行情来源未确认为当前有效状态。");
  }),
  unverifiedRunClockNeverDisplaysOldReadyGate: ["", "2026-10-27T09:58:00", "2026-10-27T10:00:01+08:00", "2026-10-26T09:58:00+08:00"].map((run_updated_at) =>
    build({ source: { ...sourceRun, run_status: "completed", run_updated_at }, recommendation_gate: readyGate }).recommendation.status === "研究任务待核实 · 仅供观察"),
  normalRunDoesNotInventFailure: ["running", "completed", "success", "ok", "skipped"].map((run_status) =>
    build({ source: { ...sourceRun, run_status }, recommendation_gate: readyGate }).recommendation.status === "原门禁已放行 · 仍需人工复核"),
  sourceRunHasNoAssumedScheduledTime: !refreshed().refresh?.detail.includes("22:30") && !build({ source: sourceRun }).sourceRun?.detail.includes("22:30"),
  invalidSourceRunClockCannotClaimTodayComplete: ["", "2026-10-27T10:00:01+08:00", "2026-10-27T09:58:00"].map((run_updated_at) => build({ source: { ...sourceRun, run_status: "completed", run_updated_at } }).sourceRun?.status === "研究任务状态待核实"),
  oldSourceRunDoesNotOverrideCurrentFacts: build({ source: { ...sourceRun, run_updated_at: "2026-10-26T09:58:00+08:00" } }).sourceRun?.status === "历史研究任务记录",
  unknownSourceRunStateIsNotCompletion: build({ source: { ...sourceRun, run_status: "toString" } }).sourceRun?.status === "研究任务状态未知",
  sourceRunCompletionCannotRepairStaleQuoteState: build({ source: { ...sourceRun, status: "stale", run_status: "completed" }, recommendation_gate: readyGate }).recommendation.status === "依据待核实 · 仅供观察",
  renderShowsRefreshWithoutClaimingProfileCoverage: refreshedHtml.includes("当日验证分段进行中") && refreshedHtml.includes("2/6 组策略配置已暂存") && refreshedHtml.includes("研究任务失败") && refreshedHtml.includes("研究任务待核实 · 仅供观察"),
  archiveRenderNeverLeaksCurrentExecution: !archiveHtml.includes("本次验证刷新") && !archiveHtml.includes("分段进行中") && !archiveHtml.includes("研究任务失败"),
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
