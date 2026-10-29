import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { ActionWorksheet, CandidateConditionChecks, DiscussionAction, FollowupObservation, PositionConditionContract, PositionDiscussion } from "./api";
import { positionActionChecks, positionChecksView, positionConditionNextStep, positionObservationNextStep } from "./position-conditions";
import { discussionSourceKey, strategyWatchState } from "./strategy-watch";
import { PositionActionChecks, PositionConditionChecks } from "../components/aqsp/PositionConditionChecks";
import { PositionActionPlan } from "../components/aqsp/PositionActionPlan";
import { buildTodayStrategy } from "./today-strategy";
import { normalizeAlerts } from "./alert-view";
import { normalizePortfolio } from "./portfolio-view";

const stamp = "2026-10-29T11:55:00+08:00", now = Date.parse(stamp);
const price = { label: "原有源价格", price: 10, source: "合成报价源", as_of: stamp };
const snapshot = { position_id: "p1", cost: 12, shares: 1000, sellable_shares: 1000 };
const evidence = { market: { status: "strong", summary: "合成大盘", as_of: stamp, source: "合成源" }, sector: { status: "rising", summary: "合成板块", as_of: stamp, source: "合成源" }, stock: { status: "rising", summary: "合成股票", as_of: stamp, source: "合成源" } };
const catalog = { schema_version: 1 as const, items: [
  { id: "sector:not_falling:v1", label: "板块没有转弱", kind: "sector_status" as const, evidence_key: "sector" as const, status: "met" as const, value: "rising", detail: "原板块依据", source: "合成板块源", as_of: stamp },
  { id: "market:weak:v1", label: "大盘转弱", kind: "market_status" as const, evidence_key: "market" as const, status: "unmet" as const, value: "strong", detail: "原大盘依据", source: "合成宽度源", as_of: stamp },
] };
const action: DiscussionAction = { action: "sell_review", price_ref: "p0", condition: "到价后核对承接变化", invalidation: "大盘转弱重新分析", prerequisite_ids: [catalog.items[0].id], invalidation_ids: [catalog.items[1].id] };
const contract: PositionConditionContract = { schema_version: 2, digest: "position-contract", origin: { kind: "position", position_id: "p1", analysis_basis_id: "basis1", source_discussion: "comparison", generated_at: stamp, action_index: 0, choice: "reduce", action: "sell_review", price_ref: "p0" }, prerequisite_ids: action.prerequisite_ids!, invalidation_ids: action.invalidation_ids!, definitions: catalog.items.map((row) => ({ id: row.id, label: row.label, kind: row.kind, evidence_key: row.evidence_key, accepted_statuses: ["strong"] })), price_predicate: { ...price, price_ref: "p0", comparator: "at_or_above" }, manual_checks: [] };
const checks: CandidateConditionChecks = { schema_version: 1, mode: "registered", summary: "waiting", digest: "checks1", items: [
  { id: "price", label: "报价达到观察价", role: "price", status: "met", detail: "报价已到，其余前提待核对", value: 10.1, source: "合成报价源", as_of: stamp },
  { id: catalog.items[0].id, label: "板块没有转弱", role: "prerequisite", status: "unmet", detail: "板块当前走弱", value: "falling", source: "合成板块源", as_of: stamp },
  { id: "manual", label: "原文中的承接变化", role: "manual", status: "manual_required", detail: "没有自动核验依据", value: null, source: "", as_of: null },
] };
const observation: FollowupObservation = { position_id: "p1", symbol: "600000", name: "合成持仓", state: "price_watch", title: "报价已到，核对其余前提", detail: "后台检查记录", observed_at: stamp, quote_as_of: stamp, revision: "r1", plan_key: "plan1", position_snapshot: snapshot, condition_checks: checks };
const worksheet: ActionWorksheet = { symbol: "600000", position_id: "p1", revision: "r1", trade_revision: "trade1", constraints: { attention: "available", extra_cash: 1000, loss_limit: null, risk_baseline: null }, analysis_basis_id: "basis1", position_snapshot: snapshot,
  basis: { checked_at: stamp, quote_as_of: stamp, quote_source: price.source, quote_issue: "", price: 10.1, cost: 12, shares: 1000, sellable_shares: 1000, position_id: "p1", joint_weakness: false, references: [price] },
  review_plan: { choice: "reduce", watch: { direction_schema: "direction-v1", action: "sell_review", price: 10, comparator: "at_or_above", condition: action.condition, invalidation: action.invalidation, price_ref: price, source_generated_at: stamp, saved_at: stamp, position_snapshot: snapshot, constraints: { attention: "available", extra_cash: 1000, loss_limit: null, risk_baseline: null }, trade_revision: "trade1", evidence_snapshot: evidence, condition_contract: contract } },
  followup: { mode: "changes", current_plan_key: "plan1", requires_refresh: false, issue: "", last_check: observation },
  route_review: { title: "合成", summary: "合成", unrealized_pnl_pct: null, recovery_to_cost_pct: null, routes: [], evidence, missing_evidence: [] }, strategy_condition_checks: checks, scenario: null, scenario_issue: "" };
const discussion: PositionDiscussion = { status: "complete", generated_at: stamp, model: "synthetic", mode: "synthetic", choice: null, position_snapshot: snapshot, quote_as_of: stamp, price_refs: { p0: price }, rounds: [], summary: null, notice: "合成", condition_catalog: catalog };
const render = (row: ActionWorksheet, time = now) => renderToStaticMarkup(createElement(PositionConditionChecks, { worksheet: row, now: time }));
const html = render(worksheet);
const backgroundOnly: ActionWorksheet = { ...worksheet, strategy_condition_checks: undefined };
const fulfilled: CandidateConditionChecks = { ...checks, items: [checks.items[0]] };
const home = (row: FollowupObservation, hardRisk = false, time = now) => buildTodayStrategy({ portfolio: normalizePortfolio({ holdings: [{ position_id: "p1", code: "600000", name: "合成持仓", shares: 1000, cost: 12 }] }), alerts: normalizeAlerts({ events: hardRisk ? [{ id: "risk1", symbol: "600000", position_id: "p1", level: "L1", status: "OPEN", updated_at: stamp }] : [] }), followups: [row], candidates: [], issues: [] }, time).items[0];
const previousNow = Date.now;
let actionPlanHtml = "", pausedPlanHtml = "", reviewedPlanHtml = "";
try {
  Date.now = () => now;
  actionPlanHtml = renderToStaticMarkup(createElement(PositionActionPlan, { target: { kind: "position", id: "p1" }, choice: "reduce", worksheet }));
  pausedPlanHtml = renderToStaticMarkup(createElement(PositionActionPlan, { target: { kind: "position", id: "p1" }, choice: "t_watch", worksheet: { ...worksheet, review_plan: { ...worksheet.review_plan, choice: "t_watch" }, basis: { ...worksheet.basis, joint_weakness: true } } }));
  reviewedPlanHtml = renderToStaticMarkup(createElement(PositionActionPlan, { target: { kind: "position", id: "p1" }, choice: "reduce", worksheet: { ...worksheet, route_review: { ...worksheet.route_review!, evidence: { ...evidence, sector: { ...evidence.sector, status: "falling" } } } } }));
} finally { Date.now = previousNow; }
export const positionConditionContracts = {
  damagedContractCanStillRenderOriginalWatch: render({ ...backgroundOnly, strategy_condition_issue: "原合同内容损坏，需重核", review_plan: { ...worksheet.review_plan, watch: { ...worksheet.review_plan.watch!, condition_contract: { schema_version: 2, origin: { kind: "position" } } as PositionConditionContract } } }).includes("原合同内容损坏，需重核"),
  actualPlanShowsConcreteNextAndItems: actionPlanHtml.includes("继续等待：板块没有转弱尚未满足。") && actionPlanHtml.includes('aria-label="持仓条件逐项检查"'),
  originalTextRemainsDuringEvidenceReview: reviewedPlanHtml.includes(action.condition) && reviewedPlanHtml.includes(action.invalidation) && reviewedPlanHtml.includes("板块没有转弱"),
  pausedActualPlanKeepsConstraintAndSpecificRows: pausedPlanHtml.includes("当前做 T 观察先暂停") && pausedPlanHtml.includes("板块没有转弱"),
  planDoesNotRepeatConcreteNextStep: actionPlanHtml.split("继续等待：板块没有转弱尚未满足。").length === 2,

  changedCatalogNeedsNewConfirmation: discussionSourceKey(discussion) !== discussionSourceKey({ ...discussion, condition_catalog: { ...catalog, items: [] } }),
  backgroundPauseIsNotClearedByMetChecks: positionChecksView({ ...backgroundOnly, followup: { ...worksheet.followup!, last_check: { ...observation, state: "paused", condition_checks: fulfilled } } }, now)?.nextStep.includes("暂停依据") === true,
  backgroundReviewIsNotClearedByMetChecks: positionChecksView({ ...backgroundOnly, followup: { ...worksheet.followup!, last_check: { ...observation, state: "needs_review", condition_checks: fulfilled } } }, now)?.nextStep.includes("原方案需要重核") === true,
  buyBackCannotSkipSaleSequence: strategyWatchState({ ...worksheet, review_plan: { ...worksheet.review_plan, choice: "t_watch", watch: { ...worksheet.review_plan.watch!, action: "buy_review" } }, strategy_condition_checks: fulfilled }, now)?.detail.includes("先卖后买") === true,

  existingRoutesKeepSameChecks: (["hold", "reduce", "t_watch"] as const).every((choice) => positionChecksView({ ...worksheet, review_plan: { ...worksheet.review_plan, choice } }, now)?.items.length === 3),
  reachedPriceDoesNotCertifyPrerequisites: strategyWatchState(worksheet, now)?.detail === "继续等待：板块没有转弱尚未满足。",
  manualPrerequisiteIsNotAutomatic: positionConditionNextStep({ ...checks, items: [checks.items[2]] }).includes("人工核验"),
  noUnqualifiedBuyOrSellClaimWhenMet: positionConditionNextStep(fulfilled).includes("不是买卖或成交信号"),
  registeredLabelsUseOriginalDiscussion: positionActionChecks(action, discussion).prerequisites[0] === "板块没有转弱",
  originalCancellationIsDistinct: positionActionChecks(action, discussion).invalidations[0] === "大盘转弱",
  missingCatalogIsNotGuessedFromText: positionActionChecks(action, { ...discussion, condition_catalog: undefined }).prerequisites[0].includes("待核验"),
  freeTextStillRequiresHumanReview: positionActionChecks({ ...action, prerequisite_ids: [], invalidation_ids: [] }, discussion).manual.includes("尚未逐项登记"),
  noDuplicateSelectedIds: positionActionChecks({ ...action, prerequisite_ids: [catalog.items[0].id, catalog.items[0].id] }, discussion).prerequisites.length === 1,
  actionPreviewShowsMonitoringScope: renderToStaticMarkup(createElement(PositionActionChecks, { action, result: discussion })).includes("后台核对的前提"),
  threeStatusesAreReadable: html.includes("已满足") && html.includes("未满足") && html.includes("需人工核验"),
  originalSourceRemainsExpandable: html.includes("<summary>依据与时点</summary>") && html.includes("合成板块源") && html.includes("10-29 11:55"),
  currentUnknownBeatsOlderSuccess: positionChecksView({ ...worksheet, strategy_condition_checks: { ...checks, items: [{ ...checks.items[1], status: "unknown", source: "", as_of: null }] }, followup: { ...worksheet.followup!, last_check: { ...observation, condition_checks: fulfilled } } }, now)?.nextStep.includes("先补依据") === true,
  missingSourceIsNotUnmet: render({ ...worksheet, strategy_condition_checks: { ...checks, items: [{ ...checks.items[1], status: "unknown", source: "", as_of: null }] } }).includes("缺依据"),
  triggeredCancellationCannotLookHealthy: render({ ...worksheet, strategy_condition_checks: { ...checks, summary: "review", items: [{ ...checks.items[1], role: "invalidation", status: "met" }] } }).includes("已触发 · 需复核"),
  invalidationNamesNextReview: positionConditionNextStep({ ...checks, items: [{ ...checks.items[1], label: "大盘转弱", role: "invalidation", status: "met" }] }).includes("大盘转弱已触发"),
  changedPremiseStillShowsSpecificChecks: positionChecksView({ ...worksheet, strategy_condition_checks: { ...checks, summary: "review" } }, now)?.nextStep.includes("板块没有转弱未满足") === true,
  ordinaryQuoteTickDoesNotReevaluateServerChecks: positionChecksView({ ...worksheet, basis: { ...worksheet.basis, price: 11 } }, now)?.nextStep === positionChecksView(worksheet, now)?.nextStep,
  matchingFreshBackgroundIsFallback: positionChecksView(backgroundOnly, now)?.nextStep === positionChecksView(worksheet, now)?.nextStep,
  mismatchedPlanCannotReuseSuccess: positionChecksView({ ...backgroundOnly, followup: { ...worksheet.followup!, current_plan_key: "plan2" } }, now)?.mode === "pending",
  staleBackgroundCannotReuseSuccess: positionChecksView({ ...backgroundOnly, followup: { ...worksheet.followup!, last_check: { ...observation, observed_at: "2026-10-29T11:39:00+08:00" } } }, now)?.mode === "pending",
  futureBackgroundIsNotCurrent: positionChecksView(backgroundOnly, now - 1)?.mode === "pending",
  requiresRefreshBlocksBackground: positionChecksView({ ...backgroundOnly, followup: { ...worksheet.followup!, requires_refresh: true } }, now)?.mode === "pending",
  changedSnapshotBlocksBackground: positionChecksView({ ...backgroundOnly, position_snapshot: { ...snapshot, shares: 900 } }, now)?.mode === "pending",
  issueCannotFallbackToOldSuccess: positionChecksView({ ...backgroundOnly, strategy_condition_issue: "原合同身份待重核" }, now)?.nextStep === "原合同身份待重核",
  pendingRegisteredStillShowsOriginalLabels: render({ ...backgroundOnly, followup: undefined }).includes("板块没有转弱"),
  oldHoldingNeverAutoMigrates: positionChecksView({ ...backgroundOnly, followup: undefined, review_plan: { ...worksheet.review_plan, watch: { ...worksheet.review_plan.watch!, condition_contract: undefined } } }, now)?.nextStep.includes("旧版文字条件未逐项登记") === true,
  expiredPageDoesNotPresentCurrentSuccess: render(worksheet, now + 60_001).includes("上次 · 已满足") && positionChecksView(worksheet, now + 60_001)?.mode === "pending",
  pausedTOverridesMetChecks: strategyWatchState({ ...worksheet, review_plan: { ...worksheet.review_plan, choice: "t_watch" }, basis: { ...worksheet.basis, joint_weakness: true }, strategy_condition_checks: fulfilled }, now)?.title === "当前做 T 观察先暂停",
  hardRiskOverridesMetChecks: strategyWatchState({ ...worksheet, review_plan: { ...worksheet.review_plan, choice: "hold" }, current_risk: [{ level: "L1", status: "triggered" }], strategy_condition_checks: fulfilled }, now)?.title.includes("风险线仍触发") === true,
  tPlusOneOverridesMetChecks: strategyWatchState({ ...worksheet, basis: { ...worksheet.basis, sellable_shares: 0 }, strategy_condition_checks: fulfilled }, now)?.detail.includes("T+1") === true,
  changedDirectionCannotBeClearedByChecks: strategyWatchState({ ...worksheet, route_review: { ...worksheet.route_review!, evidence: { ...evidence, sector: { ...evidence.sector, status: "falling" } } }, strategy_condition_checks: fulfilled }, now)?.title === "原策略依据发生变化，重新分析",
  originalChecksRemainVisibleDuringPause: render({ ...worksheet, basis: { ...worksheet.basis, joint_weakness: true } }).includes("板块没有转弱"),
  stoppedDoesNotShowCurrentMonitoring: positionChecksView({ ...worksheet, review_plan: { ...worksheet.review_plan, watch_stopped: true } }, now) === null,
  homeGivesSpecificUnmetNextStep: home(observation).next === "继续等待：板块没有转弱尚未满足。",
  homeDoesNotRepeatLongOriginalConditions: !home(observation).next.includes(action.condition),
  homeHardRiskPrecedesSatisfiedConditions: home({ ...observation, condition_checks: fulfilled }, true).next.includes("当前风险"),
  homePausePrecedesSatisfiedConditions: home({ ...observation, state: "paused", condition_checks: fulfilled }).next.includes("暂停依据"),
  homeEvidenceChangePrecedesSatisfiedConditions: home({ ...observation, state: "needs_review", condition_checks: fulfilled }).next.includes("原方案需要重核"),
  homeOldSuccessRequestsFreshCheck: home({ ...observation, condition_checks: fulfilled }, false, now + 900_001).next.includes("先更新后台检查"),
  unavailableSourceIsNotReady: positionObservationNextStep({ ...observation, state: "evidence_unavailable", condition_checks: fulfilled }, now)?.includes("先补齐") === true,
};
