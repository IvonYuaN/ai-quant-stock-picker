import {
  factorReviewLabel,
  factorStatusIsEnabled,
  factorStatusLabel,
  factorTransitionOptions,
  factorTrialSampleLabel,
  icEvidenceStatusLabel,
  normalizeFactorStatus,
  planFactorTransition,
  productionGateLabel,
  variantRefreshLabel,
  variantSuiteLabel,
} from "./research-workbench-view";
import type { AqspVariantSuite } from "@/types/aqsp";
import type { HealthPayload } from "@/lib/api";

const suite: AqspVariantSuite = {
  schema_version: "1",
  generated_at: "2026-10-30T16:00:00+08:00",
  data_mode: "historical_raw_unadjusted",
  end_date: "2026-10-29",
  variant_count: 2,
  selected_symbols: 100,
  supported_symbols: 80,
  batch_active: false,
  batch_id: "batch-1",
  batch_size: 100,
  cycle_id: 1,
  coverage_pct: 0.8,
  filters: "",
  last_error: "",
};

const passingGate: NonNullable<HealthPayload["gate"]> = {
  present: true,
  verdict: "pass",
  generated_at: "2026-10-29",
  updated: "2026-10-29T17:00:00+08:00",
};

export const researchWorkbenchViewContracts = {
  gateMissingDoesNotPass: productionGateLabel(undefined) === "未发现生产 gate 产物",
  gatePassUsesBothCriteria: productionGateLabel(passingGate) === "产物记录为双门通过",
  gateStaleIsNotCurrent: productionGateLabel({ ...passingGate, freshness: "stale" }) === "生产 gate 已过期",
  gateUnverifiedIsNotCurrent: productionGateLabel({ ...passingGate, freshness: "unverified" }) === "生产 gate 日期未核实",
  gateValidationBlockerVisible: productionGateLabel({ ...passingGate, validation_status: "blocked" }) === "生产 gate 校验阻断",
  gateFailureIsHistoricalArtifact:
    productionGateLabel({ ...passingGate, verdict: "fail" }) === "最后读取到的产物记录为双门未通过",
  gateUnknownVerdictStaysIncomplete:
    productionGateLabel({ ...passingGate, verdict: null }) === "产物存在，判据字段不完整",
  absentVariantSuiteIsNotReady: variantSuiteLabel(undefined) === "没有研究变体快照",
  activeBatchIsDistinctFromCompletedResult:
    variantSuiteLabel({ ...suite, batch_active: true }) === "快照记录批次执行中",
  variantErrorIsVisible: variantSuiteLabel({ ...suite, last_error: "runner failed" }) === "快照记录最近错误",
  completedVariantsAreDistinct: variantSuiteLabel(suite) === "已有研究变体结果",
  oldQualifiedArtifactDoesNotHideLatestFailure: variantRefreshLabel({ ...suite, refresh_status: "failed" }) === "刷新异常，未发布",
  stagedProgressIsSeparateFromArtifactCount: variantRefreshLabel({ ...suite, refresh_status: "staged", profiles_staged: 32, profiles_total: 128 }) === "分段构建中",
  expiredWaitingDoesNotLookCurrent: variantRefreshLabel({ ...suite, refresh_status: "waiting", refresh_freshness: "stale" }) === "刷新等待已过期",
  missingOrUnknownStatusStaysUnknown: variantRefreshLabel({ ...suite, refresh_status: "unknown" }) === "最近刷新状态未知",
  oldFactorTrialWithoutSampleDigestRemainsReadable:
    factorTrialSampleLabel({}) === "历史试算未记录实际数据样本指纹",
  newFactorTrialShowsRowsAndDigest:
    factorTrialSampleLabel({ data_rows: 3200, data_sample_sha256: "0123456789abcdef" }) === "数据样本 3200 行 / 0123456789ab",
  icFreshnessLabelsAreLocalized: icEvidenceStatusLabel("fresh") === "新鲜" && icEvidenceStatusLabel("stale") === "过期" && icEvidenceStatusLabel("unverified") === "未验证",

  // 因子生命周期：未验证绝不渲染成已启用
  knownStatusesArePreserved: normalizeFactorStatus("draft") === "draft" && normalizeFactorStatus("watch") === "watch" && normalizeFactorStatus("active") === "active" && normalizeFactorStatus("retired") === "retired",
  statusCanBeReadFromWholeRecord: normalizeFactorStatus({ status: "active" }) === "active",
  missingStatusIsUnknown: normalizeFactorStatus(undefined) === "unknown" && normalizeFactorStatus(null) === "unknown",
  unverifiedValueNeverBecomesActive: normalizeFactorStatus("verified") === "unknown" && normalizeFactorStatus("enabled") === "unknown",
  onlyActiveCountsAsEnabled: factorStatusIsEnabled("active") === true && factorStatusIsEnabled("watch") === false && factorStatusIsEnabled(normalizeFactorStatus("enabled")) === false,
  unknownStatusIsLabeledUnverified: factorStatusLabel("unknown") === "状态未知（未验证）",

  // 可用的人工状态操作
  draftOnlyOffersWatch: factorTransitionOptions("draft").map((option) => option.to).join(",") === "watch",
  watchOffersPromotionDemotionRetirement: factorTransitionOptions("watch").map((option) => option.to).join(",") === "active,draft,retired",
  activeOffersOnlyDemotionAndRetirement: factorTransitionOptions("active").map((option) => option.to).join(",") === "watch,retired",
  retiredAndUnknownHaveNoActions: factorTransitionOptions("retired").length === 0 && factorTransitionOptions("unknown").length === 0,

  // 人工晋级闸门
  promotionWithReportAndHumanConfirmationIsAllowed: planFactorTransition({
    from: "watch", to: "active",
    evidence: { human_confirmed: true, actor: "reviewer", validation_family_id: "a".repeat(64), validation_status: "diagnostic_only" },
  }).allowed === true,
  promotionWithConcreteManualNoteIsAllowed: planFactorTransition({
    from: "watch", to: "active",
    evidence: { human_confirmed: true, actor: "reviewer", manual_review_note: "已人工核对样本外与 IC 结论" },
  }).allowed === true,
  automaticPromotionFromReportIsBlocked: planFactorTransition({
    from: "watch", to: "active",
    evidence: { validation_family_id: "a".repeat(64), validation_status: "diagnostic_only", actor: "system" },
  }).allowed === false,
  promotionWithoutAnyEvidenceIsBlocked: planFactorTransition({
    from: "watch", to: "active", evidence: { human_confirmed: true, actor: "reviewer" },
  }).allowed === false,
  blockedReportCannotBeSolePromotionEvidence: planFactorTransition({
    from: "watch", to: "active",
    evidence: { human_confirmed: true, actor: "reviewer", validation_family_id: "a".repeat(64), validation_status: "blocked" },
  }).allowed === false,
  tooShortManualNoteIsNotEvidence: planFactorTransition({
    from: "watch", to: "active", evidence: { human_confirmed: true, actor: "reviewer", manual_review_note: "好" },
  }).allowed === false,
  illegalDraftToActiveIsBlocked: planFactorTransition({
    from: "draft", to: "active", evidence: { human_confirmed: true, actor: "reviewer", manual_review_note: "具体人工确认记录内容" },
  }).allowed === false,
  retiredCannotBeReactivated: planFactorTransition({
    from: "retired", to: "active", evidence: { human_confirmed: true, actor: "reviewer", manual_review_note: "具体人工确认记录内容" },
  }).allowed === false,
  everyTransitionNeedsActor: planFactorTransition({
    from: "active", to: "watch", evidence: { human_confirmed: true },
  }).allowed === false,
  everyTransitionNeedsHumanConfirmation: planFactorTransition({
    from: "active", to: "watch", evidence: { actor: "reviewer" },
  }).allowed === false,

  // 漂移观察：待复核 / 已过期
  missingValidationStaysPending: factorReviewLabel(undefined, Date.parse("2026-11-06T12:00:00+08:00")).state === "missing",
  blockedValidationStaysPending: factorReviewLabel({ status: "blocked", generated_at: "2026-11-05T10:00:00+08:00" }, Date.parse("2026-11-06T12:00:00+08:00")).state === "blocked",
  oldDiagnosticIsStale: factorReviewLabel({ status: "diagnostic_only", generated_at: "2026-09-01T10:00:00+08:00" }, Date.parse("2026-11-06T12:00:00+08:00")).state === "stale",
  futureTimestampIsNotFresh: factorReviewLabel({ status: "diagnostic_only", generated_at: "2026-12-01T10:00:00+08:00" }, Date.parse("2026-11-06T12:00:00+08:00")).stale === true,
  recentDiagnosticIsFreshButNotPromotion: (() => {
    const verdict = factorReviewLabel({ status: "diagnostic_only", generated_at: "2026-11-05T10:00:00+08:00" }, Date.parse("2026-11-06T12:00:00+08:00"));
    return verdict.state === "fresh" && verdict.label.includes("未授权晋级");
  })(),
  unverifiedConclusionStaysPending: factorReviewLabel({ status: "something_else", generated_at: "2026-11-05T10:00:00+08:00" }, Date.parse("2026-11-06T12:00:00+08:00")).stale === true,
};
