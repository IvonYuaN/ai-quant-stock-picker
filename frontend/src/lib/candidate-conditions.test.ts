import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import type { CandidateConditionCatalog, CandidateConditionChecks as Checks, CandidateDiscussion, CandidateWorkspace } from "./api";
import { candidateActionChecks, candidateChecksView } from "./candidate-conditions";
import { CandidateConditionChecks, CandidateActionChecks } from "@/components/aqsp/CandidateConditionChecks";

const stamp = "2026-10-29T10:30:00+08:00";
const catalogFixture: CandidateConditionCatalog = { schema_version: 1, items: [
  { id: "sector:not_falling:v1", label: "板块没有转弱", kind: "sector_status", evidence_key: "sector", status: "met", value: "rising", detail: "合成", source: "合成", as_of: stamp },
  { id: "market:weak:v1", label: "大盘转弱", kind: "market_status", evidence_key: "market", status: "unmet", value: "strong", detail: "合成", source: "合成", as_of: stamp },
] };
const actionFixture = { choice: "pullback" as const, price_ref: "previous", condition: "回落后看承接", invalidation: "大盘转弱重核", prerequisite_ids: ["sector:not_falling:v1"], invalidation_ids: ["market:weak:v1"] };
const discussionFixture: CandidateDiscussion = { id: "d1", status: "complete", generated_at: stamp, model: "synthetic", mode: "synthetic", resource_version: 1, evidence_version: "v1", research_date: "2026-10-29", quote_as_of: stamp, price_refs: {}, evidence_snapshot: {}, rounds: [], summary: null, summary_issue: "", elapsed_seconds: 1, notice: "synthetic", condition_catalog: catalogFixture };
const checksFixture: Checks = { schema_version: 1, mode: "registered", summary: "waiting", digest: "check1", items: [
  { id: "price", label: "报价达到观察价", role: "price", status: "met", detail: "报价已到，尚需核对其余前提", value: 10, source: "合成报价源", as_of: stamp },
  { id: "sector:not_falling:v1", label: "板块没有转弱", role: "prerequisite", status: "unmet", detail: "板块当前走弱", value: "weak", source: "合成板块源", as_of: stamp },
  { id: "manual", label: "承接与止跌", role: "manual", status: "manual_required", detail: "尚无可自动确认依据", value: null, source: "", as_of: null },
] };
const workspaceFixture: CandidateWorkspace = { symbol: "600000", name: "合成候选", version: 1, evidence_version: "v1", research_date: "2026-10-29", ownership: { status: "unowned", position_id: null }, available: true, entry_blocked: false, issues: [], quote: {}, quote_issue: "", evidence: {}, price_refs: {}, discussion: discussionFixture, analysis: { status: "complete", stage: "", run_id: null }, condition_catalog: catalogFixture,
  watch: { choice: "pullback", condition: "观察承接", invalidation: "方向转弱", price: 10, price_ref: "previous", source_ref: null, comparator: "at_or_below", adopted_at: stamp, discussion_id: "d1", status: "active", condition_contract: { schema_version: 1, digest: "contract1", prerequisite_ids: ["sector:not_falling:v1"], invalidation_ids: ["market:weak:v1"], definitions: [], price_predicate: null, manual_checks: [] } },
  observation: { state: "price_watch", title: "报价已到观察价", detail: "复核前提", observed_at: stamp, quote_as_of: stamp, reason: "price", condition_checks: checksFixture },
};
const render = (workspace: CandidateWorkspace) => renderToStaticMarkup(createElement(CandidateConditionChecks, { workspace }));
const checkWorkspace = (checks: Checks): CandidateWorkspace => ({ ...workspaceFixture, observation: { ...workspaceFixture.observation!, condition_checks: checks } });
const reachedFixture = render(workspaceFixture);
export const conditionMonitoringContracts = {
  deferNeverPromisesIgnoredMachineChecks: candidateActionChecks({ ...actionFixture, choice: "defer" }, discussionFixture, workspaceFixture).prerequisites.length === 0 && candidateActionChecks({ ...actionFixture, choice: "defer" }, discussionFixture, workspaceFixture).manual.includes("不建立解除暂缓的自动条件"),
  deferNextStepDoesNotSuggestEntry: candidateChecksView({ ...workspaceFixture, watch: { ...workspaceFixture.watch!, choice: "defer" }, observation: { ...workspaceFixture.observation!, state: "deferred" } })?.nextStep === "保持暂缓，依据变化后复核原前提与失效说明。",
  legacyDeferDoesNotSuggestWaitingForPrice: candidateChecksView({ ...workspaceFixture, watch: { ...workspaceFixture.watch!, choice: "defer", condition_contract: undefined }, observation: null })?.nextStep === "保持暂缓，依据变化后复核原前提与失效说明。",
  aiChoiceLabelsComeFromFrozenCatalog: candidateActionChecks(actionFixture, discussionFixture, workspaceFixture).prerequisites[0] === "板块没有转弱",
  invalidationChoiceHasDifferentRole: candidateActionChecks(actionFixture, discussionFixture, workspaceFixture).invalidations[0] === "大盘转弱",
  currentCatalogCannotRewriteOldDiscussion: candidateActionChecks(actionFixture, { ...discussionFixture, condition_catalog: undefined, evidence_version: "old" }, workspaceFixture).prerequisites[0].includes("待核验"),
  legacyActionsRemainManual: candidateActionChecks({ ...actionFixture, prerequisite_ids: undefined, invalidation_ids: undefined }, discussionFixture, workspaceFixture).manual.includes("尚未逐项登记"),
  duplicateIdsDoNotRepeatLabels: candidateActionChecks({ ...actionFixture, prerequisite_ids: ["sector:not_falling:v1", "sector:not_falling:v1"] }, discussionFixture, workspaceFixture).prerequisites.length === 1,
  reachedPriceDoesNotSatisfyOtherPrerequisites: candidateChecksView(workspaceFixture)?.nextStep === "继续等待：板块没有转弱尚未满足。",
  renderedStatusesRemainDistinct: reachedFixture.includes("已满足") && reachedFixture.includes("未满足") && reachedFixture.includes("需人工核验"),
  concreteUnmetConditionIsVisible: reachedFixture.includes("板块没有转弱") && reachedFixture.includes("继续等待"),
  sourcesAreExpandable: reachedFixture.includes("<summary>依据与时点</summary>") && reachedFixture.includes("合成报价源") && reachedFixture.includes("10-29 10:30"),
  unknownIsNotAutomaticallyUnmet: candidateChecksView(checkWorkspace({ ...checksFixture, items: [{ ...checksFixture.items[1], status: "unknown" }] }))?.nextStep.includes("先补依据") === true,
  invalidationMetRequestsReview: candidateChecksView(checkWorkspace({ ...checksFixture, summary: "review", items: [{ ...checksFixture.items[1], role: "invalidation", status: "met", label: "大盘转弱" }] }))?.nextStep.includes("大盘转弱已触发") === true,
  invalidationStatusCannotLookLikeHealthyMet: render(checkWorkspace({ ...checksFixture, summary: "review", items: [{ ...checksFixture.items[1], role: "invalidation", status: "met" }] })).includes("已触发 · 需复核"),
  allAutomaticChecksStillRequireManualConfirmation: candidateChecksView(checkWorkspace({ ...checksFixture, items: [{ ...checksFixture.items[0] }] }))?.nextStep.includes("不是买入或成交信号") === true,
  summaryUnknownIsNotOverriddenBySomeMetRows: candidateChecksView(checkWorkspace({ ...checksFixture, summary: "unknown", items: [checksFixture.items[0]] }))?.nextStep.includes("仍缺完整依据") === true,
  summaryManualCannotBecomeReady: candidateChecksView(checkWorkspace({ ...checksFixture, summary: "manual_required", items: [checksFixture.items[0]] }))?.nextStep.includes("人工前提待核验") === true,
  missingResultsDoNotInventSatisfiedChecks: candidateChecksView({ ...workspaceFixture, observation: null })?.mode === "pending",
  emptyResultsDoNotClaimReady: candidateChecksView(checkWorkspace({ ...checksFixture, items: [] }))?.mode === "pending",
  legacyWatchDoesNotInventRegistration: candidateChecksView({ ...workspaceFixture, watch: { ...workspaceFixture.watch!, condition_contract: undefined }, observation: null })?.nextStep.includes("旧版文字条件未逐项登记") === true,
  legacyServerCheckIsClearlyMarked: candidateChecksView(checkWorkspace({ ...checksFixture, mode: "legacy" }))?.mode === "legacy",
  stoppedWatchCannotSuggestContinuedMonitoring: candidateChecksView({ ...workspaceFixture, watch: { ...workspaceFixture.watch!, status: "stopped" } })?.nextStep.includes("不再后台监控") === true,
  ownershipUnknownDoesNotSuggestWaitingForEntry: candidateChecksView({ ...workspaceFixture, ownership: { status: "unavailable", position_id: null } }) === null,
  noWatchDoesNotCreateChecks: candidateChecksView({ ...workspaceFixture, watch: null }) === null,
  actionPreviewShowsBothMachineCheckGroups: renderToStaticMarkup(createElement(CandidateActionChecks, { action: actionFixture, result: discussionFixture, workspace: workspaceFixture })).includes("后台核对的前提"),
};
