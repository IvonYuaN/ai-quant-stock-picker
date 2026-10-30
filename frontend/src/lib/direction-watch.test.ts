import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { ActionWorksheet, CandidateConditionChecks, DiscussionAction, PositionConditionContract, PositionDiscussion } from "./api";
import { directionStrategyWatch, directionWatchAction, strategyWatchInput, strategyWatchIssue, strategyWatchState } from "./strategy-watch";
import { positionActionChecks, positionChecksView, positionConditionNextStep } from "./position-conditions";
import { PositionDiscussionPanel } from "../components/aqsp/PositionDiscussionPanel";
import { PositionActionPlan } from "../components/aqsp/PositionActionPlan";

const stamp = "2026-10-30T13:00:00+08:00", now = Date.parse(stamp);
const snapshot = { position_id: "p-direction", cost: 12, shares: 1000, sellable_shares: 1000 };
const constraints = { attention: "available" as const, extra_cash: 1000, loss_limit: null, risk_baseline: null };
const evidence = { market: { status: "weak", summary: "合成大盘偏弱", source: "合成宽度源", as_of: stamp }, sector: { status: "flat", summary: "合成行业持平", source: "合成行业源", industry: "合成行业", as_of: stamp }, stock: { status: "rising", summary: "合成个股", source: "合成行情源", as_of: stamp } };
const action: DiscussionAction = { action: "observe", price_ref: null, condition: "等待市场宽度不再偏弱", invalidation: "所属行业下跌则复核", prerequisite_ids: ["market:not_weak:v1"], invalidation_ids: ["sector:falling:v1"] };
const catalog = { schema_version: 1 as const, items: [
  { id: "market:not_weak:v1", label: "市场宽度不再偏弱", kind: "market_status" as const, evidence_key: "market" as const, status: "unmet" as const, value: "weak", detail: "合成", source: "合成宽度源", as_of: stamp },
  { id: "market:weak:v1", label: "市场宽度偏弱", kind: "market_status" as const, evidence_key: "market" as const, status: "met" as const, value: "weak", detail: "合成", source: "合成宽度源", as_of: stamp },
  { id: "sector:falling:v1", label: "所属行业当前方向下跌", kind: "sector_status" as const, evidence_key: "sector" as const, status: "unmet" as const, value: "flat", detail: "合成", source: "合成行业源", as_of: stamp },
] };
const contract: PositionConditionContract = { schema_version: 3, digest: "direction-contract", prerequisite_ids: action.prerequisite_ids!, invalidation_ids: action.invalidation_ids!, definitions: catalog.items.filter((row) => row.id !== "market:weak:v1").map((row) => ({ id: row.id, label: row.label, kind: row.kind, evidence_key: row.evidence_key, accepted_statuses: row.evidence_key === "market" ? ["neutral", "strong"] : ["falling"] })), price_predicate: null, manual_checks: [], origin: { kind: "position", position_id: snapshot.position_id, analysis_basis_id: "basis-direction", source_discussion: "plan", generated_at: stamp, action_index: 0, choice: "hold", action: "observe", price_ref: null } };
const waiting: CandidateConditionChecks = { schema_version: 1, mode: "registered", summary: "waiting", digest: "waiting", items: [
  { id: "market:not_weak:v1", label: "市场宽度不再偏弱", role: "prerequisite", status: "unmet", detail: "有源方向尚不符合", value: "weak", source: "合成宽度源", as_of: stamp },
  { id: "sector:falling:v1", label: "所属行业当前方向下跌", role: "invalidation", status: "unmet", detail: "当前没有触发", value: "flat", source: "合成行业源", as_of: stamp },
] };
export const directionWorksheetFixture: ActionWorksheet = { symbol: "600000", name: "合成方向等待", position_id: snapshot.position_id, revision: "r-direction", trade_revision: "trade-direction", analysis_basis_id: "basis-direction", constraints,
  basis: { checked_at: stamp, quote_as_of: stamp, quote_source: "合成行情源", quote_issue: "", price: 9.8, cost: 12, shares: 1000, sellable_shares: 1000, position_id: snapshot.position_id, joint_weakness: false, references: [] }, position_snapshot: snapshot,
  review_plan: { choice: "hold", watch: { action: "observe", price: null, comparator: null, price_ref: null, condition: action.condition, invalidation: action.invalidation, direction_schema: "direction-v1", source_generated_at: stamp, saved_at: stamp, position_snapshot: snapshot, constraints, trade_revision: "trade-direction", evidence_snapshot: evidence, condition_contract: contract } },
  strategy_condition_checks: waiting, route_review: { title: "合成等待", summary: "合成", routes: [], evidence, missing_evidence: [], unrealized_pnl_pct: null, recovery_to_cost_pct: null }, scenario: null, scenario_issue: "",
};
const worksheet = directionWorksheetFixture;
const discussion: PositionDiscussion = { status: "complete", generated_at: stamp, model: "synthetic", mode: "合成", choice: "hold", position_snapshot: snapshot, quote_as_of: stamp, review_revision: worksheet.revision, analysis_basis_id: worksheet.analysis_basis_id, evidence_snapshot: { ...evidence, quote: { price: 9.8, source: "合成行情源", as_of: stamp } }, price_refs: {}, rounds: [], condition_catalog: catalog, summary: { conclusion: "先观察方向", agreements: [], disagreements: [], missing_evidence: [], actions: [action] }, notice: "合成验证" };
const resultWith = (row: DiscussionAction) => ({ ...discussion, summary: { ...discussion.summary!, actions: [row] } });
const improved: ActionWorksheet = { ...worksheet, strategy_direction_progress: true, route_review: { ...worksheet.route_review!, evidence: { ...evidence, market: { ...evidence.market, status: "neutral" } } }, strategy_condition_checks: { ...waiting, summary: "manual_required", items: [{ ...waiting.items[0], status: "met", value: "neutral" }, waiting.items[1]] } };
const unknown: ActionWorksheet = { ...improved, strategy_condition_checks: { ...waiting, summary: "unknown", items: [{ ...waiting.items[0], status: "unknown", value: null, source: "", as_of: null }] } };
const invalidated: ActionWorksheet = { ...improved, strategy_condition_checks: { ...waiting, summary: "review", items: [{ ...waiting.items[1], status: "met", value: "falling" }] } };
const planHtml = (row: ActionWorksheet) => renderToStaticMarkup(createElement(PositionActionPlan, { worksheet: row, choice: row.review_plan.choice ?? "hold" }));
const discussionHtml = (row: PositionDiscussion) => renderToStaticMarkup(createElement(PositionDiscussionPanel, { worksheet: { ...worksheet, review_plan: { choice: "hold", discussion: row } }, choice: "hold", disabled: false }));
const previousNow = Date.now;
let plan = "", improvedPlan = "", model = "", failedModel = "", stopped = "", archived = "", damaged = "";
try {
  Date.now = () => now;
  plan = planHtml(worksheet); improvedPlan = planHtml(improved); model = discussionHtml(discussion);
  failedModel = discussionHtml({ ...discussion, status: "failed" });
  stopped = planHtml({ ...worksheet, review_plan: { choice: "hold", watch_stopped: true } });
  archived = planHtml({ ...worksheet, archived: true });
  damaged = planHtml({ ...worksheet, review_plan: { ...worksheet.review_plan, watch: { ...worksheet.review_plan.watch!, condition_contract: undefined } } });
} finally { Date.now = previousNow; }
const rejected = (row: DiscussionAction, comparator: "at_or_above" | null = null) => !!strategyWatchIssue(resultWith(row), worksheet, 0, "hold", comparator, now);
const nullInput = strategyWatchInput(discussion, worksheet, 0, "hold", null, now);

export const directionWatchContracts = {
  explicitNullDirectionActionAccepted: directionWatchAction(action, discussion) && strategyWatchIssue(discussion, worksheet, 0, "hold", null, now) === "",
  nullableInputPinsOriginalIdentity: nullInput.comparator === null && nullInput.generated_at === stamp && nullInput.expected_position === snapshot && nullInput.expected_analysis_basis_id === worksheet.analysis_basis_id,
  routeStillRequired: strategyWatchIssue(discussion, worksheet, 0, "", null, now).includes("路线"),
  quoteDirectionIsNotAccepted: rejected(action, "at_or_above") && strategyWatchIssue(discussion, worksheet, 0, "hold", "", now).includes("不设置"),
  emptyDirectionIdsRejected: rejected({ ...action, prerequisite_ids: [], invalidation_ids: [] }),
  unknownIdsRejected: rejected({ ...action, prerequisite_ids: ["market:fake:v1"] }),
  contradictoryIdsRejected: rejected({ ...action, prerequisite_ids: ["market:not_weak:v1", "market:weak:v1"] }),
  repeatedAndOverlappingIdsRejected: rejected({ ...action, prerequisite_ids: ["market:not_weak:v1", "market:not_weak:v1"] }) && rejected({ ...action, invalidation_ids: action.prerequisite_ids }),
  missingCatalogRejected: !directionWatchAction(action, { ...discussion, condition_catalog: undefined }),
  pauseBuyAndSellWithoutPriceRejected: (["pause", "buy_review", "sell_review"] as const).every((kind) => rejected({ ...action, action: kind })),
  invalidReferenceDoesNotBecomeDirectionWait: rejected({ ...action, price_ref: "missing" }),
  failedModelCannotAdopt: !!strategyWatchIssue({ ...discussion, status: "failed" }, worksheet, 0, "hold", null, now) && !failedModel.includes("采用这项条件并跟踪"),
  partialInitialCannotAdopt: !!strategyWatchIssue({ ...discussion, synthesis_basis: { mode: "partial_initial", participating_roles: ["trend"], missing_roles: ["risk"], unreviewed_roles: [] } }, worksheet, 0, "hold", null, now),
  observeActionHasAdoptionEntryWithoutPrice: model.includes("采用这项条件并跟踪") && model.includes("等待已登记方向前提") && !model.includes("缺少可核验价位"),
  directionTextExplainsManualReview: positionActionChecks(action, discussion).manual.includes("不自动产生买卖任务"),
  nullWatchRendersWithoutPriceFallback: plan.includes("等待方向前提") && plan.includes("等待验证：") && !plan.includes("原价格引用待核对") && !plan.includes("到价后看"),
  waitingUsesConcreteRegisteredCondition: strategyWatchState(worksheet, now)?.detail.includes("市场宽度不再偏弱尚未满足") === true,
  serverConfirmedProgressReachesManualChecks: strategyWatchState(improved, now)?.title === "等待方向前提，逐项核对" && strategyWatchState(improved, now)?.detail.includes("人工前提待核验") === true,
  improvedUiDoesNotClaimPriceReached: improvedPlan.includes("已满足") && !improvedPlan.includes("已到观察价") && !improvedPlan.includes("到价后看"),
  unconfirmedProgressRequiresReview: strategyWatchState({ ...improved, strategy_direction_progress: undefined }, now)?.title === "原策略依据发生变化，重新分析",
  sourceOrUnregisteredChangeRequiresReview: strategyWatchState({ ...improved, strategy_direction_progress: false, route_review: { ...improved.route_review!, evidence: { ...improved.route_review!.evidence, sector: { ...evidence.sector, source: "替换源" } } } }, now)?.title === "原策略依据发生变化，重新分析",
  unknownDoesNotCountAsImprovement: strategyWatchState(unknown, now)?.detail.includes("先补依据") === true,
  invalidationPrecedesMetPrerequisites: strategyWatchState(invalidated, now)?.detail.includes("已触发") === true,
  l1RiskKeepsPriority: strategyWatchState({ ...improved, current_risk: [{ level: "L1", status: "triggered" }] }, now)?.title.includes("风险线仍触发") === true,
  personalPauseKeepsPriority: strategyWatchState({ ...improved, review_plan: { ...improved.review_plan, choice: "t_watch" }, basis: { ...improved.basis, personal_pause_t: true } }, now)?.title === "当前做 T 观察先暂停",
  changedHoldingKeepsPriority: strategyWatchState({ ...improved, position_snapshot: { ...snapshot, shares: 900 } }, now)?.title.includes("持仓已变化") === true,
  recordedSaleStillRequiresReview: strategyWatchState({ ...improved, sale_requires_review: true }, now)?.title.includes("持仓已变化") === true,
  changedPersonalConditionsKeepPriority: strategyWatchState({ ...improved, constraints: { ...constraints, extra_cash: 0 } }, now)?.title === "原策略对应的持仓或条件已变化",
  staleQuoteKeepsPriority: strategyWatchState(improved, now + 60_001)?.title === "等待有效行情",
  stoppedDoesNotShowDirectionAdvice: stopped.includes("这项策略跟踪已停止") && !stopped.includes("等待验证：") && positionChecksView({ ...worksheet, review_plan: { ...worksheet.review_plan, watch_stopped: true } }, now) === null,
  archivedDoesNotShowDirectionAdvice: archived.includes("这笔持仓已清仓") && !archived.includes("等待验证："),
  invalidNullWatchRemainsReadable: damaged.includes("原观察条件不完整") && !damaged.includes("等待验证："),
  v2NullPriceCannotUseProgressFlag: !directionStrategyWatch({ ...worksheet.review_plan.watch!, condition_contract: { ...contract, schema_version: 2 } }),
  pendingV3ShowsOriginalDefinitions: positionChecksView({ ...worksheet, strategy_condition_checks: undefined }, now)?.items.length === 2,
  fulfilledDirectionOnlyRemainsHumanReview: positionConditionNextStep({ ...waiting, summary: "manual_required", items: [{ ...waiting.items[0], status: "met" }] }, undefined, true).includes("方向条件满足不代表完整条件成立"),
};
