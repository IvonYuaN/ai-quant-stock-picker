import { factorTrialSampleLabel, icEvidenceStatusLabel, productionGateLabel, variantRefreshLabel, variantSuiteLabel } from "./research-workbench-view";
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
};
