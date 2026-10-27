import { api, type CandidateDiscussion, type CandidateWorkspace } from "./api";
import { CANDIDATE_CHOICES, candidateAdoptionInput, candidateAdoptionIssue, candidateDiscussionIssue, candidateDiscussionKey, candidateEvidenceRows, candidateObservationView, candidateResearchHref, candidateWorkspaceIssue, currentCandidateObservations } from "./candidate-observation";

const stamp = "2026-10-27T10:00:00+08:00";
const ref = { label: "合成前收盘价", price: 10, as_of: stamp, source: "合成行情源" };
const discussion: CandidateDiscussion = { id: "discussion-1", status: "complete", generated_at: stamp, model: "synthetic-model", mode: "合成三角色", resource_version: 1, evidence_version: "evidence-1", research_date: "2026-10-27", quote_as_of: stamp, price_refs: { previous: ref }, evidence_snapshot: {}, rounds: [],
  summary: { conclusion: "合成比较，不作交易指令", agreements: [], disagreements: ["需要核对承接"], missing_evidence: [], focus_choice: "pullback", focus_reason: "先核对来源位置", routes: [
    { choice: "pullback", judgment: "investigate", reason: "等待回落以核对承接", evidence_ids: ["quote"], needs: ["核对止跌"] },
    { choice: "confirmation", judgment: "insufficient", reason: "等待走势确认", evidence_ids: ["quote"], needs: ["核对量价"] },
    { choice: "defer", judgment: "investigate", reason: "也可以先暂缓", evidence_ids: ["quote"], needs: [] }],
    actions: [{ choice: "pullback", price_ref: "previous", condition: "价格回落后核对承接", invalidation: "来源失效或环境走弱重新分析" }, { choice: "confirmation", price_ref: "previous", condition: "报价回升后核对有效性", invalidation: "未确认就不参与" }] }, summary_issue: "", elapsed_seconds: 1, notice: "合成" };
const workspace: CandidateWorkspace = { symbol: "600000", name: "合成候选", version: 1, evidence_version: "evidence-1", research_date: "2026-10-27", ownership: { status: "unowned", position_id: null }, available: true, entry_blocked: false, issues: [], quote: { price: 10.5, as_of: stamp, source: "合成行情源" }, quote_issue: "", evidence: {}, price_refs: { previous: ref }, discussion, analysis: { status: "complete", stage: "", run_id: null }, watch: null, observation: null };
const tracked: CandidateWorkspace = { ...workspace, watch: { choice: "pullback", condition: "核对承接", invalidation: "来源失效重核", price: 10, price_ref: "previous", source_ref: ref, comparator: "at_or_below", adopted_at: stamp, discussion_id: discussion.id, status: "active" } };
const blocked: CandidateWorkspace = { ...workspace, entry_blocked: true };
const throws = (fn: () => unknown) => { try { fn(); return false; } catch { return true; } };
const input = candidateAdoptionInput(discussion, workspace, "pullback", 0, "at_or_below", "operation-1");
const deferred = candidateAdoptionInput(discussion, blocked, "defer", 0, "at_or_above", "operation-2");
export const candidateObservationContracts = {
  unknownEnvironmentIsNotInvented: candidateEvidenceRows(workspace).every((row) => row.summary === "尚缺可核验依据" && row.source === "来源待补"),
  environmentSourceRemainsVerifiable: candidateEvidenceRows({ ...workspace, evidence: { market: { summary: "合成大盘方向", source: "合成指数源", as_of: stamp } } })[0].asOf === stamp,
  threeRoutesReflectCandidateChoices: Object.keys(CANDIDATE_CHOICES).join(",") === "pullback,confirmation,defer",
  modelResultCarriesNoPositionIdentity: !JSON.stringify(input).includes("position_id") && !JSON.stringify(input).includes("shares"),
  sourceConditionPinnedExactly: input.discussion_id === "discussion-1" && input.generated_at === stamp && input.action_index === 0 && input.expected_evidence_version === "evidence-1" && input.expected_version === 1,
  noQuantityFeesOrTransactionsRequired: candidateAdoptionIssue(discussion, workspace, "pullback", 0, "at_or_below") === "",
  ownedStockGoesToExistingPosition: candidateWorkspaceIssue({ ...workspace, ownership: { status: "owned", position_id: "real-position" } }).includes("持仓策略"),
  ownershipFailureNeverMeansUnowned: candidateWorkspaceIssue({ ...workspace, ownership: { status: "unavailable", position_id: null } }).includes("身份尚未核清"),
  historicalResearchCannotAdoptCurrentCondition: candidateWorkspaceIssue(workspace, "2026-10-26").includes("历史研究") && !!candidateWorkspaceIssue(workspace, workspace.research_date, true),
  currentResearchIsAllowed: candidateWorkspaceIssue(workspace, workspace.research_date) === "",
  missingEvidenceRemainsExplicit: candidateWorkspaceIssue({ ...workspace, available: false, issues: ["来源缺失"] }) === "来源缺失",
  missingQuoteCannotCreatePriceWatch: candidateAdoptionIssue(discussion, { ...workspace, quote_issue: "报价失效" }, "pullback", 0, "at_or_below").includes("行情不可用"),
  quotationTickDoesNotInvalidateSource: candidateDiscussionIssue(discussion, { ...workspace, quote: { ...workspace.quote, price: 10.7 } }) === "",
  changedResearchCannotReuseModel: candidateDiscussionIssue(discussion, { ...workspace, evidence_version: "evidence-2" }).includes("重新分析"),
  concurrentVersionCannotReuseModel: candidateDiscussionIssue(discussion, { ...workspace, version: 2 }).includes("重新分析"),
  changedResearchDateCannotReuseModel: candidateDiscussionIssue(discussion, { ...workspace, research_date: "2026-10-28" }).includes("重新分析"),
  partialRolesCannotBecomeAdoptedStrategy: !!candidateDiscussionIssue({ ...discussion, status: "partial" }, workspace),
  failedModelDoesNotGetTemplateCondition: !!candidateDiscussionIssue({ ...discussion, status: "failed", summary: null }, workspace),
  missingModelIdentityRejected: !!candidateDiscussionIssue({ ...discussion, id: "" }, workspace),
  invalidModelTimestampRejected: !!candidateDiscussionIssue({ ...discussion, generated_at: "bad" }, workspace),
  directionRequiresHumanConfirmation: candidateAdoptionIssue(discussion, workspace, "pullback", 0, "").includes("选择报价"),
  routeAndActionMustMatch: candidateAdoptionIssue(discussion, workspace, "confirmation", 0, "at_or_above").includes("来源价位"),
  insufficientRouteCannotAdoptEvenWithPrice: candidateAdoptionIssue(discussion, workspace, "confirmation", 1, "at_or_above").includes("依据待补"),
  missingActionCannotInventWatch: candidateAdoptionIssue(discussion, workspace, "pullback", 99, "at_or_below").includes("来源价位"),
  unknownReferenceCannotInventPrice: candidateAdoptionIssue({ ...discussion, price_refs: {} }, workspace, "pullback", 0, "at_or_below").includes("来源价位"),
  missingPriceSourceCannotAdopt: candidateAdoptionIssue({ ...discussion, price_refs: { previous: { ...ref, source: "" } } }, workspace, "pullback", 0, "at_or_below").includes("来源价位"),
  invalidPriceCannotAdopt: candidateAdoptionIssue({ ...discussion, price_refs: { previous: { ...ref, price: Number.NaN } } }, workspace, "pullback", 0, "at_or_below").includes("来源价位"),
  gateBlocksEntryButAllowsExplanation: candidateWorkspaceIssue(blocked) === "" && candidateAdoptionIssue(discussion, blocked, "confirmation", 1, "at_or_above").includes("门禁阻塞"),
  deferralDoesNotCreatePriceDirection: deferred.choice === "defer" && deferred.action_index === null && deferred.comparator === null,
  operationIdentityRequired: throws(() => candidateAdoptionInput(discussion, workspace, "pullback", 0, "at_or_below", "")),
  regeneratedResultClearsOldConfirmation: candidateDiscussionKey(discussion) !== candidateDiscussionKey({ ...discussion, id: "discussion-2" }),
  changedResultSourceClearsOldConfirmation: candidateDiscussionKey(discussion) !== candidateDiscussionKey({ ...discussion, price_refs: { previous: { ...ref, price: 11 } } }),
  newSummaryClearsOldConfirmation: candidateDiscussionKey(discussion) !== candidateDiscussionKey({ ...discussion, summary: { ...discussion.summary!, conclusion: "改为暂缓" } }),
  priceTickKeepsSameConfirmationSource: candidateDiscussionKey(discussion) === candidateDiscussionKey({ ...discussion }),
  savedWatchDoesNotInventPriceReached: candidateObservationView({ ...tracked, quote: { price: 9 } }).title === "观察已保存，等待后台检查",
  serverFactsDisplayedWithoutReinterpretation: candidateObservationView({ ...tracked, observation: { state: "price_watch", title: "报价已到观察条件", detail: "止跌尚未核实", observed_at: stamp, quote_as_of: stamp, reason: "price" } }).detail === "止跌尚未核实",
  stoppedWatchNeverResumesOnPriceCrossing: candidateObservationView({ ...tracked, watch: { ...tracked.watch!, status: "stopped" }, quote: { price: 9 } }).title === "候选观察已停止",
  acquiredHoldingRetiresCandidateView: candidateObservationView({ ...tracked, ownership: { status: "owned", position_id: "real" } }).title === "已转入持仓策略",
  stateUnavailablePreservesUnknown: candidateObservationView({ ...tracked, ownership: { status: "unavailable", position_id: null } }).tone === "warn",
  homeHidesUnadoptedAndStoppedDrafts: currentCandidateObservations([workspace, tracked, { ...tracked, symbol: "600001", watch: { ...tracked.watch!, status: "stopped" } }]).length === 1,
  homeDoesNotKeepOwnedCandidatePlans: currentCandidateObservations([{ ...tracked, ownership: { status: "owned", position_id: "real" } }]).length === 0,
  homePrioritizesChangedEvidence: currentCandidateObservations([tracked, { ...tracked, symbol: "600001", observation: { state: "needs_review", title: "依据变化", detail: "先重核", observed_at: stamp, quote_as_of: stamp, reason: "changed" } }])[0].symbol === "600001",
  observationEntryKeepsCandidateIdentity: candidateResearchHref("600000") === "/stock/600000#candidate-observation" && throws(() => candidateResearchHref("fake-position")),
};

const originalFetch = globalThis.fetch;
const calls: Array<{ url: string; method: string; body: Record<string, unknown> }> = [];
let reject = false;
globalThis.fetch = async (url, options) => {
  calls.push({ url: String(url), method: options?.method ?? "GET", body: JSON.parse(String(options?.body ?? "{}")) });
  if (reject) return new Response(JSON.stringify({ detail: "候选版本已变化" }), { status: 409 });
  if (String(url).endsWith("/discussion/stream")) return new Response(`${JSON.stringify({ event: "progress", stage: "independent" })}\n${JSON.stringify({ event: "result", data: discussion })}\n`);
  return new Response(JSON.stringify({ data: String(url).endsWith("candidate-observations") ? { items: [tracked] } : tracked }));
};
let streamResult: CandidateDiscussion | null = null, rejected = false;
const progress: string[] = [];
try {
  await api.candidateObservations();
  await api.candidateObservation("600000");
  streamResult = await api.candidateDiscussionStream("600000", { expected_version: 1, expected_evidence_version: "evidence-1", server_model: "next-model" }, (stage) => progress.push(stage), new AbortController().signal);
  await api.adoptCandidateObservation("600000", input);
  await api.stopCandidateObservation("600000", 2, "stop-1");
  reject = true;
  try { await api.adoptCandidateObservation("600000", input); } catch { rejected = true; }
} finally { globalThis.fetch = originalFetch; }
export const candidateObservationTransportContracts = {
  serverListIsIndependentOfResearchDate: calls[0].url === "/api/candidate-observations" && calls[0].method === "GET",
  detailUsesRealSymbol: calls[1].url === "/api/candidate-observations/600000" && calls[1].method === "GET",
  discussionUsesSourceCASAndRuntimeModel: calls[2].body.expected_version === 1 && calls[2].body.expected_evidence_version === "evidence-1" && calls[2].body.server_model === "next-model",
  progressOnlyDoesNotCompleteAnalysis: progress[0] === "independent" && streamResult?.id === "discussion-1",
  adoptsExactSourceWithoutPositionOrTrade: calls[3].url.endsWith("/600000/adopt") && calls[3].body.discussion_id === "discussion-1" && !JSON.stringify(calls[3].body).includes("shares"),
  stopsWithCASAndIdempotencyKey: calls[4].url.endsWith("/600000/stop") && calls[4].body.expected_version === 2 && calls[4].body.operation_id === "stop-1",
  conflictDoesNotRetryOrPretendSuccess: rejected && calls.length === 6,
};
