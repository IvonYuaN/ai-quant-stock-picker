import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { PositionDiscussionPanel } from "../components/aqsp/PositionDiscussionPanel";
import type { ActionWorksheet, FollowupObservation, PositionDiscussion } from "./api";
import { calculationDraftRequiresReview } from "../components/aqsp/PositionActionPlan";
import { draftNeedsReview } from "./position-workspace";
import { discussionSourceKey, discussionSynthesisNotice, discussionStrategyCurrent, strategyWatchInput, strategyWatchIssue, strategyWatchState, strategyDirectionKey } from "./strategy-watch";

const stamp = "2026-10-27T10:00:00+08:00";
const now = Date.parse(stamp);
const evidence = { market: { status: "strong", summary: "合成大盘", as_of: stamp, source: "合成源" }, sector: { status: "rising", summary: "合成行业", as_of: stamp, source: "合成源" }, stock: { status: "rising", summary: "合成股票", as_of: stamp, source: "合成源" } };
const reference = { label: "合成观察高点", price: 10, as_of: stamp, source: "合成源" };
const worksheet: ActionWorksheet = { symbol: "600000", position_id: "p1", revision: "r1", trade_revision: "trade-1", constraints: { attention: "available", extra_cash: 1000, loss_limit: null, risk_baseline: null }, analysis_basis_id: "current-numeric-quote-basis", basis: { checked_at: stamp, quote_as_of: stamp, quote_source: "合成源", quote_issue: "", price: 9.8, cost: 12, shares: 1000, sellable_shares: 1000, position_id: "p1", joint_weakness: false, references: [reference] }, position_snapshot: { position_id: "p1", cost: 12, shares: 1000, sellable_shares: 1000 }, review_plan: {}, scenario: null, scenario_issue: "", route_review: { title: "合成", summary: "合成", unrealized_pnl_pct: null, recovery_to_cost_pct: null, routes: [], evidence, missing_evidence: [] } };
const discussion: PositionDiscussion = { status: "complete", generated_at: stamp, model: "synthetic", mode: "合成", choice: null, position_snapshot: worksheet.position_snapshot, quote_as_of: stamp, review_revision: "r1", analysis_basis_id: "original-model-basis", evidence_snapshot: { ...evidence, quote: { price: 9.7, as_of: stamp, source: "合成源" } }, price_refs: { p0: reference }, rounds: [], summary: { conclusion: "合成", agreements: [], disagreements: [], missing_evidence: [], actions: [{ action: "sell_review", price_ref: "p0", condition: "核对转弱证据", invalidation: "板块转弱则重新分析" }] }, notice: "合成" };
const watch = { direction_schema: "direction-v1", action: "sell_review" as const, comparator: "at_or_above" as const, price: 10, condition: "核对转弱证据", invalidation: "板块转弱则重新分析", price_ref: reference, source_generated_at: stamp, saved_at: stamp, position_snapshot: worksheet.position_snapshot, constraints: worksheet.constraints, trade_revision: worksheet.trade_revision, evidence_snapshot: evidence };
const tracked = { ...worksheet, review_plan: { choice: "reduce" as const, watch } };
const pausedObservation: FollowupObservation = { position_id: "p1", symbol: "600000", name: "合成", state: "paused", title: "原做 T 计划先暂停", detail: "合成", observed_at: stamp, quote_as_of: stamp, revision: "r1", plan_key: "intent-1", position_snapshot: worksheet.position_snapshot, reason: "joint_weakness", joint_pause_pending: true };
const unknownEvidence = { ...evidence, sector: { ...evidence.sector, status: "unknown" } };
const pending: ActionWorksheet = { ...tracked, review_plan: { choice: "t_watch", watch: { ...watch, evidence_snapshot: unknownEvidence } }, basis: { ...worksheet.basis, price: 11 }, route_review: { ...worksheet.route_review!, evidence: unknownEvidence }, followup: { mode: "changes", current_plan_key: "intent-1", last_check: pausedObservation, requires_refresh: false, issue: "" } };
const throws = (fn: () => unknown) => { try { fn(); return false; } catch { return true; } };
const partialBasisFixture = { mode: "partial_initial" as const, participating_roles: ["trend", "risk"], missing_roles: ["t_review"], unreviewed_roles: ["risk"] };
const partialHtmlFixture = renderToStaticMarkup(createElement(PositionDiscussionPanel, { choice: null, worksheet: { ...worksheet, comparison: { ...discussion, status: "partial", synthesis_basis: partialBasisFixture } }, disabled: false }));
const challengeHtmlFixture = renderToStaticMarkup(createElement(PositionDiscussionPanel, { choice: null, worksheet: { ...worksheet, comparison: { ...discussion, status: "partial", synthesis_basis: { ...partialBasisFixture, mode: "challenge" } } }, disabled: false }));
const failedHtmlFixture = renderToStaticMarkup(createElement(PositionDiscussionPanel, { choice: null, worksheet: { ...worksheet, comparison: { ...discussion, status: "failed", summary: null, summary_issue: "有效意见不足两个不同角色", synthesis_basis: partialBasisFixture } }, disabled: false }));
export const strategyWatchContract = {
  failedDiscussionDoesNotInventPartialSummary: !failedHtmlFixture.includes("部分汇总仅供阅读") && failedHtmlFixture.includes("有效意见不足两个不同角色"),
  initialFallbackShowsConclusionAndConditions: partialHtmlFixture.includes("核对转弱证据") && partialHtmlFixture.includes("部分汇总仅供阅读"),
  initialFallbackHidesAdoptionEntry: !partialHtmlFixture.includes("采用这项条件并跟踪") && !partialHtmlFixture.includes("确认采用并跟踪"),
  challengePartialKeepsAdoptionEntry: challengeHtmlFixture.includes("采用这项条件并跟踪"),
  initialFallbackCannotAdopt: strategyWatchIssue({ ...discussion, status: "partial", synthesis_basis: partialBasisFixture }, worksheet, 0, "reduce", "at_or_above", now).includes("仅供阅读"),
  initialFallbackInputCannotEscapeGuard: throws(() => strategyWatchInput({ ...discussion, status: "partial", synthesis_basis: partialBasisFixture }, worksheet, 0, "reduce", "at_or_above", now)),
  existingChallengePartialCanAdopt: strategyWatchIssue({ ...discussion, status: "partial", synthesis_basis: { ...partialBasisFixture, mode: "challenge" } }, worksheet, 0, "reduce", "at_or_above", now) === "",
  legacyPartialCanAdopt: strategyWatchIssue({ ...discussion, status: "partial" }, worksheet, 0, "reduce", "at_or_above", now) === "",
  changedReviewBasisNeedsConfirmation: discussionSourceKey(discussion) !== discussionSourceKey({ ...discussion, synthesis_basis: partialBasisFixture }),
  missingAndUnreviewedRolesAreDistinct: discussionSynthesisNotice(partialBasisFixture, "position").includes("缺席：做 T 评估；未完成回应：风险复核"),
  candidateRolesHaveReadableNames: discussionSynthesisNotice({ ...partialBasisFixture, missing_roles: ["entry"], unreviewed_roles: ["risk"] }, "candidate").includes("缺席：回落与代价；未完成回应：风险与暂缓"),
  completedReviewDoesNotAddWarning: discussionSynthesisNotice({ mode: "challenge", participating_roles: ["trend", "risk", "t_review"], missing_roles: [], unreviewed_roles: [] }, "position") === "",
  legacyReviewDoesNotAddWarning: discussionSynthesisNotice(undefined, "position") === "",

  sameStateIndustryChangeRejectsAdoption: !discussionStrategyCurrent(discussion, { ...worksheet, route_review: { ...worksheet.route_review!, evidence: { ...evidence, sector: { ...evidence.sector, industry: "银行" } } } }, now),
  sameStateSourceChangeRequiresReview: strategyWatchState({ ...tracked, route_review: { ...worksheet.route_review!, evidence: { ...evidence, market: { ...evidence.market, source: "新来源" } } } }, now)?.title === "原策略依据发生变化，重新分析",
  legacyDirectionNeedsExplicitReview: strategyWatchState({ ...tracked, review_plan: { choice: "reduce", watch: { ...watch, direction_schema: undefined } } }, now)?.title === "原方向依据需要重新核对",
  indexTurnChangesDirection: strategyDirectionKey({ ...evidence, market: { ...evidence.market, indices: [{ name: "上证指数", change_pct: 1 }] } }) !== strategyDirectionKey({ ...evidence, market: { ...evidence.market, indices: [{ name: "上证指数", change_pct: -1 }] } }),
  indexTickDoesNotChangeDirection: strategyDirectionKey({ ...evidence, market: { ...evidence.market, indices: [{ name: "上证指数", change_pct: 1 }] } }) === strategyDirectionKey({ ...evidence, market: { ...evidence.market, indices: [{ name: "上证指数", change_pct: 2 }] } }),
  regeneratedDiscussionNeedsNewConfirmation: discussionSourceKey(discussion) !== discussionSourceKey({ ...discussion, generated_at: "2026-10-27T10:00:01+08:00" }),
  changedModelBasisNeedsNewConfirmation: discussionSourceKey(discussion) !== discussionSourceKey({ ...discussion, analysis_basis_id: "new-basis" }),
  replacedSummaryNeedsNewConfirmation: discussionSourceKey(discussion) !== discussionSourceKey({ ...discussion, summary: { ...discussion.summary!, conclusion: "新的不同结论" } }),
  normalQuoteChangeDoesNotPreventAdoption: discussionStrategyCurrent(discussion, worksheet, now + 30_000),
  originalModelBasisIsSubmitted: strategyWatchInput(discussion, worksheet, 0, "reduce", "at_or_above", now).expected_analysis_basis_id === "original-model-basis",
  sourceIndexIsSubmitted: strategyWatchInput(discussion, worksheet, 0, "reduce", "at_or_above", now).action_index === 0,
  directionRequiresHumanSelection: strategyWatchIssue(discussion, worksheet, 0, "reduce", "", now).includes("确认报价"),
  routeRequiresHumanSelection: strategyWatchIssue(discussion, worksheet, 0, "", "at_or_above", now).includes("选择"),
  changedRevisionRejected: !discussionStrategyCurrent(discussion, { ...worksheet, revision: "r2" }, now),
  changedPositionRejected: !discussionStrategyCurrent(discussion, { ...worksheet, position_snapshot: { ...worksheet.position_snapshot, shares: 900 } }, now),
  changedMarketDirectionRejected: !discussionStrategyCurrent(discussion, { ...worksheet, route_review: { ...worksheet.route_review!, evidence: { ...evidence, market: { ...evidence.market, status: "weak" } } } }, now),
  sourceLostRejected: !discussionStrategyCurrent(discussion, { ...worksheet, route_review: { ...worksheet.route_review!, evidence: { ...evidence, sector: { ...evidence.sector, status: "unknown" } } } }, now),
  staleDiscussionRejected: !discussionStrategyCurrent(discussion, worksheet, now + 900_001),
  futureDiscussionRejected: !discussionStrategyCurrent(discussion, worksheet, now - 1),
  missingPriceRejected: strategyWatchIssue({ ...discussion, price_refs: {} }, worksheet, 0, "reduce", "at_or_above", now).includes("来源价位"),
  pauseCannotBecomePriceWatch: strategyWatchIssue({ ...discussion, summary: { ...discussion.summary!, actions: [{ ...discussion.summary!.actions[0], action: "pause" }] } }, worksheet, 0, "reduce", "at_or_above", now).includes("来源价位"),
  buyCannotBeAdoptedAsReduction: strategyWatchIssue({ ...discussion, summary: { ...discussion.summary!, actions: [{ ...discussion.summary!.actions[0], action: "buy_review" }] } }, worksheet, 0, "reduce", "at_or_below", now).includes("做 T"),
  pauseTDoesNotGenerateAdoption: throws(() => strategyWatchInput(discussion, { ...worksheet, basis: { ...worksheet.basis, personal_pause_t: true } }, 0, "t_watch", "at_or_above", now)),
  invalidSourceDoesNotTrack: strategyWatchIssue(discussion, { ...worksheet, basis: { ...worksheet.basis, quote_issue: "源失败" } }, 0, "reduce", "at_or_above", now).includes("行情不可用"),
  watchWaitsBeforePrice: strategyWatchState(tracked, now)?.title === "正在等待策略条件",
  priceEqualityOnlyMeansReview: strategyWatchState({ ...tracked, basis: { ...tracked.basis, price: 10 } }, now)?.detail.includes("尚不能确认") === true,
  belowComparatorWorks: strategyWatchState({ ...tracked, review_plan: { choice: "t_watch", watch: { ...watch, comparator: "at_or_below", action: "buy_review" } } }, now)?.title.includes("已到") === true,
  staleQuoteNeverShowsPriceReached: strategyWatchState({ ...tracked, basis: { ...tracked.basis, price: 11 } }, now + 61_000)?.title === "等待有效行情",
  changedHoldingRequiresReview: strategyWatchState({ ...tracked, position_snapshot: { ...worksheet.position_snapshot, shares: 800 } }, now)?.title.includes("持仓已变化") === true,
  changedConstraintsInvalidateReachedPrice: strategyWatchState({ ...tracked, constraints: { ...worksheet.constraints!, extra_cash: 500 }, basis: { ...worksheet.basis, price: 11 } }, now)?.title === "原策略对应的持仓或条件已变化",
  changedTradeRevisionInvalidatesEvenSameHolding: strategyWatchState({ ...tracked, trade_revision: "trade-2", basis: { ...worksheet.basis, price: 11 } }, now)?.title === "原策略对应的持仓或条件已变化",
  changedSectorInvalidatesReachedPrice: strategyWatchState({ ...tracked, route_review: { ...worksheet.route_review!, evidence: { ...evidence, sector: { ...evidence.sector, status: "falling" } } }, basis: { ...worksheet.basis, price: 11 } }, now)?.title === "原策略依据发生变化，重新分析",
  unavailableStockCannotContinueOldStrength: strategyWatchState({ ...tracked, route_review: { ...worksheet.route_review!, evidence: { ...evidence, stock: { ...evidence.stock, status: "unknown" } } } }, now)?.title === "原策略依据发生变化，重新分析",
  unchangedConstraintsOrderDoesNotInvalidate: strategyWatchState({ ...tracked, constraints: { loss_limit: null, risk_baseline: null, extra_cash: 1000, attention: "available" } }, now)?.title === "正在等待策略条件",
  missingWatchSnapshotRequiresReview: strategyWatchState({ ...tracked, review_plan: { choice: "reduce", watch: { ...watch, position_snapshot: undefined } } }, now)?.title.includes("持仓已变化") === true,
  invalidStoredPriceRequiresReview: strategyWatchState({ ...tracked, review_plan: { choice: "reduce", watch: { ...watch, price: 0 } } }, now)?.title === "已保存观察条件不可核验",
  stoppedWatchDoesNotResumeOldCalculator: strategyWatchState({ ...tracked, review_plan: { choice: "reduce", watch_stopped: true } }, now)?.title === "这项策略跟踪已停止",
  quoteUnavailableBeforeWatchChanges: strategyWatchState({ ...tracked, trade_revision: "trade-2", basis: { ...worksheet.basis, quote_issue: "源失败" } }, now)?.title === "等待有效行情",
  l1RiskOverridesHoldPriceReached: strategyWatchState({ ...tracked, review_plan: { choice: "hold", watch }, current_risk: [{ level: "L1", status: "triggered" }], basis: { ...worksheet.basis, price: 11 } }, now)?.title === "当前风险线仍触发，重新核对持有依据",
  l5RiskOverridesHoldPriceReached: strategyWatchState({ ...tracked, review_plan: { choice: "hold", watch }, current_risk: [{ level: "L5", status: "triggered" }], basis: { ...worksheet.basis, price: 11 } }, now)?.title === "当前风险线仍触发，重新核对持有依据",
  riskReminderDoesNotInventSellAnalysis: strategyWatchState({ ...tracked, review_plan: { choice: "hold", watch }, current_risk: [{ level: "L1", status: "triggered" }] }, now)?.detail.includes("不代替卖出分析") === true,
  inactiveHoldRiskDoesNotPauseWatch: strategyWatchState({ ...tracked, review_plan: { choice: "hold", watch }, current_risk: [{ level: "L1", status: "waiting" }] }, now)?.title === "正在等待策略条件",
  unresolvedJointPauseOverridesReachedPrice: strategyWatchState(pending, now)?.title === "三维暂停依据尚未核清",
  samePlanKeyPreservesPauseDespiteChangedRiskRevision: strategyWatchState({ ...pending, revision: "r2" }, now)?.title === "三维暂停依据尚未核清",
  renewedIntentDoesNotInheritOldPause: strategyWatchState({ ...pending, followup: { ...pending.followup!, current_plan_key: "intent-2" } }, now)?.title === "已到观察价，核对下一步条件",
  legacyObservationWithoutReasonStillSupported: strategyWatchState({ ...pending, followup: { ...pending.followup!, last_check: { ...pausedObservation, reason: undefined, joint_pause_pending: undefined } } }, now)?.title === "已到观察价，核对下一步条件",
  legacyJointReasonPreservesPause: strategyWatchState({ ...pending, followup: { ...pending.followup!, last_check: { ...pausedObservation, joint_pause_pending: undefined } } }, now)?.title === "三维暂停依据尚未核清",
  stoppedWatchIsNotReactivatedByRisk: strategyWatchState({ ...tracked, review_plan: { choice: "hold", watch_stopped: true }, current_risk: [{ level: "L1", status: "triggered" }] }, now)?.title === "这项策略跟踪已停止",
  stopRevisionWithoutCalculatorDraftDoesNotWarn: !calculationDraftRequiresReview(false, "r1", { ...tracked, revision: "r2", review_plan: { choice: "reduce", watch_stopped: true } }),
  savedCalculatorStillRequiresReviewAfterStop: calculationDraftRequiresReview(true, "r1", { ...tracked, revision: "r2", review_plan: { choice: "reduce", watch_stopped: true } }),
  editingCalculatorRetainsOldRevisionGuard: calculationDraftRequiresReview(true, "r1", { ...tracked, revision: "r2" }) && draftNeedsReview("r1", { ...tracked, revision: "r2" }),
  acknowledgedDraftCanResume: !calculationDraftRequiresReview(true, "r2", { ...tracked, revision: "r2" }),
  tPauseOverridesReachedPrice: strategyWatchState({ ...tracked, review_plan: { choice: "t_watch", watch }, basis: { ...worksheet.basis, price: 11, joint_weakness: true } }, now)?.title.includes("暂停") === true,
};

const previousFetch = globalThis.fetch;
const requests: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
let conflict = false;
globalThis.fetch = async (input, options) => {
  requests.push({ url: String(input), method: options?.method ?? "GET", body: JSON.parse(String(options?.body ?? "{}")) });
  return new Response(JSON.stringify(conflict ? { detail: "策略版本变化" } : { data: tracked }), { status: conflict ? 409 : 200 });
};
let rejectedStop = false;
try {
  const { api } = await import("./api");
  await api.adoptStrategyWatch("p1", strategyWatchInput(discussion, worksheet, 0, "reduce", "at_or_above", now));
  await api.stopStrategyWatch("p1", worksheet.position_snapshot, "r1");
  conflict = true;
  try { await api.stopStrategyWatch("p1", worksheet.position_snapshot, "r1"); } catch { rejectedStop = true; }
} finally { globalThis.fetch = previousFetch; }
export const strategyWatchTransportContract = {
  adoptsExactModelCondition: requests[0].url === "/api/positions/p1/strategy-watch" && requests[0].body.expected_analysis_basis_id === "original-model-basis" && requests[0].body.action_index === 0,
  stopUsesGuardedPost: requests[1].url === "/api/positions/p1/strategy-watch/stop" && requests[1].method === "POST" && requests[1].body.expected_revision === "r1" && (requests[1].body.expected_position as { position_id: string }).position_id === "p1",
  stopConflictDoesNotAutoRetry: rejectedStop && requests.length === 3,
};
