import { normalizeAlerts } from "./alert-view";
import { normalizePortfolio } from "./portfolio-view";
import type { CandidateWorkspace, FollowupObservation } from "./api";
import { buildTodayStrategy, readTodayStrategySources, type TodayStrategySources } from "./today-strategy";

const stamp = "2026-10-27T10:00:00+08:00";
const portfolio = normalizePortfolio({ holdings: [{ position_id: "root-a", code: "600000", name: "合成持仓", shares: 1000, cost: 12 }, { position_id: "root-b", code: "600001", name: "另一持仓", shares: 100, cost: 10 }] });
const followup: FollowupObservation = { position_id: "root-a", symbol: "600000", name: "合成持仓", state: "price_watch", title: "原价位已到，核对其他前提", detail: "只证明原报价条件，不证明可以卖出", observed_at: stamp, quote_as_of: stamp, revision: "r1", plan_key: "p1", position_snapshot: { position_id: "root-a", cost: 12, shares: 1000, sellable_shares: 1000 } };
const alerts = normalizeAlerts({ events: [{ id: "a", symbol: "600000", position_id: "root-a", level: "L1", status: "OPEN", updated_at: stamp, basis_json: { rule: "成本风险线", actual: "检查时已跌破" } }, { id: "b", symbol: "600000", position_id: "root-a", level: "L4", status: "OPEN", updated_at: stamp }] });
const candidate: CandidateWorkspace = { symbol: "600002", name: "合成候选", version: 2, evidence_version: "ev", research_date: "2026-10-27", ownership: { status: "unowned", position_id: null }, available: true, entry_blocked: false, issues: [], quote: { price: 10 }, quote_issue: "", evidence: {}, price_refs: {}, discussion: null, analysis: { status: "idle", stage: "", run_id: null }, watch: { choice: "pullback", condition: "到价后核对承接", invalidation: "行业转弱重新分析", price: 9.5, price_ref: "low", source_ref: null, comparator: "at_or_below", adopted_at: stamp, discussion_id: "d1", status: "active" }, observation: { state: "price_watch", title: "已到原价位", detail: "上次检查报价达到条件", observed_at: stamp, quote_as_of: stamp, reason: "price" } };
const registeredCandidate: CandidateWorkspace = { ...candidate, watch: { ...candidate.watch!, condition_contract: { schema_version: 1, digest: "contract", prerequisite_ids: ["sector:not_falling:v1"], invalidation_ids: [], definitions: [], price_predicate: null, manual_checks: [] } }, observation: { ...candidate.observation!, condition_checks: {
  schema_version: 1, mode: "registered", summary: "waiting", digest: "checks", items: [
    { id: "price", label: "报价到达观察价", role: "price", status: "met", detail: "仅报价到达", value: 9.5, source: "合成报价源", as_of: stamp },
    { id: "sector:not_falling:v1", label: "板块未走弱", role: "prerequisite", status: "unmet", detail: "板块当前走弱", value: "falling", source: "合成板块源", as_of: stamp },
  ],
} } };
const sources: TodayStrategySources = { portfolio, alerts, followups: [followup], candidates: [candidate], issues: [] };
const view = buildTodayStrategy(sources);
const make = (patch: Partial<TodayStrategySources>) => buildTodayStrategy({ ...sources, ...patch });
export const todayStrategyContracts = {
  samePositionRiskAndFollowupBecomeOne: view.items.filter((row) => row.symbol === "600000").length === 1,
  hardRiskPrecedesCandidateAndQuietHolding: view.items[0].key === "position:root-a" && view.items[0].priority === 0 && view.items[1].kind === "candidate",
  neverTurnsRiskIntoSellInstruction: !JSON.stringify(view.items).includes("立即卖出") && view.items[0].title === "风险记录待复核",
  usesOwnedStockNameAndExactPlanIdentity: view.items[0].name === "合成持仓" && view.items[0].href.includes("root-a/plan"),
  oldRecordKeepsOwnTimestamp: view.items[0].observedAt === stamp && view.items[1].observedAt === stamp,
  legacyCandidateDoesNotClaimRegisteredConditions: view.items[1].next.includes("旧版文字条件未逐项登记") && !view.items[1].next.includes(candidate.watch!.invalidation),
  reachedPriceExplainsFactWithoutRepeatingCondition: !view.items[1].reason.includes(candidate.watch!.condition) && view.items[1].reason.includes("其他前提仍需人工核验"),
  homeUsesConcreteRegisteredNextStep: make({ candidates: [registeredCandidate] }).items.find((row) => row.kind === "candidate")?.next === "继续等待：板块未走弱尚未满足。",
  homeDoesNotRepeatOriginalConditionForRegisteredWatch: (() => { const row = make({ candidates: [registeredCandidate] }).items.find((row) => row.kind === "candidate")!; return !row.next.includes(registeredCandidate.watch!.condition) && !row.reason.includes(registeredCandidate.watch!.invalidation); })(),
  homeChecksNeverOverrideUnknownOwnership: make({ candidates: [{ ...registeredCandidate, ownership: { status: "unavailable", position_id: null } }] }).items.find((row) => row.kind === "candidate")?.next === "核对身份后再决定是否沿用入场条件。",
  homeNewConditionsDoNotReviveTransferredHolding: make({ candidates: [{ ...registeredCandidate, ownership: { status: "owned", position_id: "new-position" } }] }).items.every((row) => row.kind !== "candidate"),
  homeReviewStateStillPrecedesWaitingCheckSummary: make({ candidates: [{ ...registeredCandidate, observation: { ...registeredCandidate.observation!, state: "needs_review", detail: "大盘依据已变化" } }] }).items.find((row) => row.kind === "candidate")?.next.includes("原依据发生变化") === true,
  homeUnavailableEvidenceCannotBeOverriddenByMetChecks: make({ candidates: [{ ...registeredCandidate, observation: { ...registeredCandidate.observation!, state: "evidence_unavailable", detail: "当前来源不可用" } }] }).items.find((row) => row.kind === "candidate")?.next.includes("当前依据不可用") === true,
  homeRetainsImportantReviewReason: make({ candidates: [{ ...registeredCandidate, observation: { ...registeredCandidate.observation!, state: "needs_review", detail: "大盘依据已变化" } }] }).items.find((row) => row.kind === "candidate")?.reason === "大盘依据已变化",
  priceReadDoesNotInventNewTrigger: make({ candidates: [{ ...candidate, quote: { price: 1 }, observation: null }] }).items.find((row) => row.kind === "candidate")?.title === "继续等待候选条件",
  oldRiskRootDoesNotAttachToReopenedStock: make({ alerts: normalizeAlerts({ events: [{ ...alerts.events[0], position_id: "old-root" }] }), followups: [] }).items.find((row) => row.symbol === "600000")?.priority === 5,
  missingRootNeverGuessedFromCode: make({ alerts: normalizeAlerts({ events: [{ ...alerts.events[0], position_id: null }] }), followups: [] }).issues.some((line) => line.includes("缺少仓位身份")),
  foreignFollowupSnapshotDoesNotAttach: make({ alerts: null, followups: [{ ...followup, position_snapshot: { ...followup.position_snapshot, position_id: "old-root" } }] }).items.find((row) => row.symbol === "600000")?.priority === 5,
  changedSharesRequireRecheck: make({ alerts: null, followups: [{ ...followup, position_snapshot: { ...followup.position_snapshot, shares: 500 } }] }).items.find((row) => row.symbol === "600000")?.next.includes("重新核对") === true,
  latestWaitingRecordSupersedesOldPriceTrigger: make({ alerts: null, followups: [followup, { ...followup, state: "waiting", detail: "这次未达到原条件", title: "继续等原条件", observed_at: "2026-10-27T11:00:00+08:00" }] }).items.find((row) => row.symbol === "600000")?.reason === "这次未达到原条件",
  latestUnplannedRecordDoesNotReviveOldTrigger: make({ alerts: null, followups: [followup, { ...followup, state: "unplanned", observed_at: "2026-10-27T11:00:00+08:00" }] }).items.find((row) => row.symbol === "600000")?.priority === 5,
  ownedCodeCannotDuplicateCandidate: make({ candidates: [{ ...candidate, symbol: "600000" }] }).items.filter((row) => row.symbol === "600000").length === 1,
  backendOwnedCandidateCannotReappear: make({ candidates: [{ ...candidate, ownership: { status: "owned", position_id: "root-c" } }] }).items.every((row) => row.kind !== "candidate"),
  repeatedCandidateRowsDeduplicate: make({ candidates: [candidate, candidate] }).items.filter((row) => row.kind === "candidate").length === 1,
  stoppedCandidateDoesNotBecomeAttention: make({ candidates: [{ ...candidate, watch: { ...candidate.watch!, status: "stopped" } }] }).items.every((row) => row.kind !== "candidate"),
  missingOwnershipIsNotBuyable: make({ candidates: [{ ...candidate, ownership: { status: "unavailable", position_id: null } }] }).items.find((row) => row.kind === "candidate")?.title === "持仓身份待核清",
  limitsInitialAttentionToThree: make({ candidates: [candidate, { ...candidate, symbol: "600003" }, { ...candidate, symbol: "600004" }] }).items.length === 3,
  reportsAdditionalRecords: make({ candidates: [candidate, { ...candidate, symbol: "600003" }, { ...candidate, symbol: "600004" }] }).total === 5,
  zeroHoldingsDoesNotNeedTrade: make({ portfolio: normalizePortfolio({ holdings: [] }), alerts: normalizeAlerts({ events: [] }), followups: [] }).noHoldings,
  failedPortfolioIsNotZeroHoldings: !make({ portfolio: null }).noHoldings,
  noAlertsDoesNotClaimNoRisk: make({ alerts: normalizeAlerts({ events: [] }), followups: [], candidates: [] }).items[0].reason.includes("不代表没有风险"),
  gapsAreNotSilentlyDropped: make({ issues: ["风险提醒未读取"] }).issues.includes("风险提醒未读取"),
  derivesWithoutChangingInputs: sources.alerts?.events.length === 2 && sources.candidates?.[0].watch?.price === 9.5,
};

const calls: string[] = [];
const fake = {
  portfolio: async () => { calls.push("portfolio"); return portfolio; },
  alerts: async () => { calls.push("alerts"); throw new Error("offline"); },
  positionFollowups: async () => { calls.push("followups"); return { items: [followup] }; },
  candidateObservations: async () => { calls.push("candidates"); return { items: [candidate] }; },
};
const loaded = await readTodayStrategySources(fake as Parameters<typeof readTodayStrategySources>[0]);
const incomplete = await readTodayStrategySources({ ...fake, portfolio: async () => ({}), alerts: async () => ({}) } as Parameters<typeof readTodayStrategySources>[0]);
export const todayStrategyReadContracts = {
  fixedBatchNotPerStockReads: calls.slice(0, 4).join(",") === "portfolio,alerts,followups,candidates",
  failingAlertsRemainUnknown: loaded.alerts === null && loaded.issues.some((line) => line.includes("风险提醒")),
  partialSuccessStillDisplaysOtherSources: loaded.portfolio?.holdings.length === 2 && loaded.candidates?.length === 1,
  incompletePortfolioCannotBecomeEmptySuccess: incomplete.portfolio === null && !buildTodayStrategy(incomplete).noHoldings,
  incompleteAlertsCannotBecomeAllClear: incomplete.alerts === null,
};

const malformed = await readTodayStrategySources({ ...fake, portfolio: async () => ({ holdings: [{}] }) } as unknown as Parameters<typeof readTodayStrategySources>[0]);
export const malformedPortfolioIsUnknown = malformed.portfolio === null && !buildTodayStrategy(malformed).noHoldings;
