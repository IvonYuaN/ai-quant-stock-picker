import type { AqspVariantSuite } from "@/types/aqsp";
import type { FactorTrialResult, HealthPayload } from "@/lib/api";

export function productionGateLabel(gate: HealthPayload["gate"] | undefined): string {
  if (!gate?.present) return "未发现生产 gate 产物";
  if (gate.freshness === "stale") return "生产 gate 已过期";
  if (gate.freshness === "unverified") return "生产 gate 日期未核实";
  if (gate.validation_status === "blocked") return "生产 gate 校验阻断";
  if (gate.verdict === "pass") return "产物记录为双门通过";
  if (gate.verdict === "fail") return "最后读取到的产物记录为双门未通过";
  return "产物存在，判据字段不完整";
}

export function icEvidenceStatusLabel(value: "fresh" | "stale" | "unverified" | "missing" | "empty" | "unknown"): string {
  return {
    fresh: "新鲜",
    stale: "过期",
    unverified: "未验证",
    missing: "缺失",
    empty: "无历史点",
    unknown: "未知",
  }[value];
}

export function variantSuiteLabel(suite: AqspVariantSuite | undefined): string {
  if (!suite) return "没有研究变体快照";
  if (suite.batch_active) return "快照记录批次执行中";
  if (suite.last_error) return "快照记录最近错误";
  if (suite.variant_count > 0) return "已有研究变体结果";
  return "当前没有变体结果";
}

export function variantRefreshLabel(suite: AqspVariantSuite): string {
  if (suite.refresh_freshness === "stale") return "刷新等待已过期";
  switch (suite.refresh_status ?? "unknown") {
    case "waiting": return "等待错峰刷新";
    case "staged": return "分段构建中";
    case "completed": return "最近刷新完成";
    case "timed_out": return "刷新超出运行预算，未发布";
    case "skipped_lock": return "已有任务占用资源，本次跳过";
    case "rejected": return "输入或结果校验未通过，未发布";
    case "failed": return "刷新异常，未发布";
    default: return "最近刷新状态未知";
  }
}

export function factorTrialSampleLabel(trial: Pick<FactorTrialResult, "data_rows" | "data_sample_sha256">): string {
  if (!trial.data_sample_sha256) return "历史试算未记录实际数据样本指纹";
  return `数据样本 ${trial.data_rows ?? "未知"} 行 / ${trial.data_sample_sha256.slice(0, 12)}`;
}
