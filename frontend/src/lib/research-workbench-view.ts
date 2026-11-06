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

// ===== 因子生命周期：人工晋级闸门与漂移观察 =====
// 这些是纯函数：只读取后端字段做展示与预检，不改状态、不回灌打分/权重/thresholds。

export type FactorLifecycleStatus = "draft" | "watch" | "active" | "retired" | "unknown";

export interface FactorTransitionEvidence {
  human_confirmed?: boolean;
  actor?: string;
  validation_family_id?: string;
  validation_status?: "diagnostic_only" | "blocked" | null;
  manual_review_note?: string;
}

export interface FactorTransitionOption {
  action: "to_watch" | "to_active" | "to_draft" | "to_retired";
  label: string;
  to: Exclude<FactorLifecycleStatus, "unknown">;
}

const FACTOR_STATUS_LABELS: Record<FactorLifecycleStatus, string> = {
  draft: "草稿",
  watch: "观察中",
  active: "已启用",
  retired: "已退役",
  unknown: "状态未知（未验证）",
};

const ALLOWED_FACTOR_TRANSITIONS: Record<FactorLifecycleStatus, FactorTransitionOption[]> = {
  draft: [{ action: "to_watch", label: "送观察", to: "watch" }],
  watch: [
    { action: "to_active", label: "人工晋级为启用", to: "active" },
    { action: "to_draft", label: "退回草稿", to: "draft" },
    { action: "to_retired", label: "退役", to: "retired" },
  ],
  active: [
    { action: "to_watch", label: "降回观察", to: "watch" },
    { action: "to_retired", label: "退役", to: "retired" },
  ],
  retired: [],
  unknown: [],
};

const MIN_REVIEW_NOTE_CHARS = 8;
const SHA256_PATTERN = /^[0-9a-f]{64}$/;
const DEFAULT_STALE_AFTER_DAYS = 30;

/** 任何缺失、未知或未验证的状态一律归为 unknown，绝不渲染成「已启用」。 */
export function normalizeFactorStatus(value: unknown): FactorLifecycleStatus {
  const raw = value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>).status
    : value;
  if (raw === "draft" || raw === "watch" || raw === "active" || raw === "retired") return raw;
  return "unknown";
}

export function factorStatusLabel(status: FactorLifecycleStatus): string {
  return FACTOR_STATUS_LABELS[status];
}

export function factorStatusIsEnabled(status: FactorLifecycleStatus): boolean {
  return status === "active";
}

export function factorTransitionOptions(status: FactorLifecycleStatus): FactorTransitionOption[] {
  return ALLOWED_FACTOR_TRANSITIONS[status].slice();
}

export type FactorReviewState = "missing" | "blocked" | "stale" | "fresh";

export interface FactorReviewVerdict {
  state: FactorReviewState;
  label: string;
  stale: boolean;
}

/** 最近一次检验结论：缺结论、被阻断、过期都保持「待复核」，检验结果永不授权晋级。 */
export function factorReviewLabel(
  lastValidation: unknown,
  nowEpochMs: number,
  staleAfterDays = DEFAULT_STALE_AFTER_DAYS,
): FactorReviewVerdict {
  const row = lastValidation && typeof lastValidation === "object" && !Array.isArray(lastValidation)
    ? (lastValidation as Record<string, unknown>)
    : null;
  if (!row) return { state: "missing", label: "尚无检验结论 · 待人工复核", stale: true };
  if (row.status === "blocked") return { state: "blocked", label: "最近检验被阻断 · 待复核", stale: true };
  if (row.status !== "diagnostic_only") {
    return { state: "missing", label: "最近检验结论未验证 · 待复核", stale: true };
  }
  const generated = typeof row.generated_at === "string" ? Date.parse(row.generated_at) : Number.NaN;
  const fresh = Number.isFinite(generated)
    && generated <= nowEpochMs
    && nowEpochMs - generated <= Math.max(staleAfterDays, 0) * 86_400_000;
  if (!fresh) return { state: "stale", label: "最近检验已过期 · 待复核", stale: true };
  return { state: "fresh", label: "最近检验可供人工复核 · 未授权晋级", stale: false };
}

export interface FactorTransitionPlan {
  allowed: boolean;
  blockers: string[];
  requiresEvidence: boolean;
}

/** 前端预检：与后端人工闸门同规则。真正裁决仍在服务端。 */
export function planFactorTransition(input: {
  from: FactorLifecycleStatus;
  to: FactorLifecycleStatus;
  evidence: FactorTransitionEvidence;
}): FactorTransitionPlan {
  const { from, to, evidence } = input;
  const blockers: string[] = [];
  if (!factorTransitionOptions(from).some((option) => option.to === to)) {
    blockers.push(`不允许从「${factorStatusLabel(from)}」迁移到「${factorStatusLabel(to)}」`);
  }
  const requiresEvidence = to === "active";
  if (evidence.human_confirmed !== true) {
    blockers.push("必须由人工显式确认；检验结果不会自动晋级或变更状态");
  }
  if (typeof evidence.actor !== "string" || !evidence.actor.trim()) {
    blockers.push("缺少操作人，无法留痕");
  }
  if (requiresEvidence) {
    const hasReport = typeof evidence.validation_family_id === "string"
      && SHA256_PATTERN.test(evidence.validation_family_id)
      && evidence.validation_status === "diagnostic_only";
    const hasNote = typeof evidence.manual_review_note === "string"
      && evidence.manual_review_note.trim().length >= MIN_REVIEW_NOTE_CHARS;
    if (!hasReport && !hasNote) {
      blockers.push("晋级为启用缺少可核对证据：需有效的检验报告或具体的人工确认记录");
    }
    if (evidence.validation_status === "blocked" && !hasNote) {
      blockers.push("引用的检验报告被阻断，不能单独作为晋级证据");
    }
  }
  return { allowed: blockers.length === 0, blockers, requiresEvidence };
}
