import type { ActionWorksheet, CandidateConditionCheck, CandidateConditionChecks, DiscussionAction, FollowupObservation, PositionDiscussion } from "./api";
import { conditionCheckStatus, type CandidateActionChecksView, type CandidateChecksView } from "./candidate-conditions";
import { worksheetExpired } from "./position-plan";

const roleLabels = { prerequisite: "前提", invalidation: "失效条件", price: "报价", manual: "人工核验" };
// Existing background observation validity budget; this does not authenticate quote sources.
const FOLLOWUP_OBSERVATION_MAX_AGE_MS = 900_000;

/** Use the model result's original catalog, never today's replacement definitions. */
export function positionActionChecks(action: DiscussionAction, result: PositionDiscussion): CandidateActionChecksView {
  const rows = result.condition_catalog?.schema_version === 1 ? result.condition_catalog.items : [];
  const label = (id: string): string => rows.find((row) => row.id === id)?.label || "已登记条件（定义待核验）";
  const prerequisites = [...new Set(action.prerequisite_ids ?? [])].map(label);
  const invalidations = [...new Set(action.invalidation_ids ?? [])].map(label);
  return { prerequisites, invalidations, manual: prerequisites.length || invalidations.length
    ? "其余文字买卖前提仍需人工核验；到价不代表完整条件成立。"
    : "文字条件尚未逐项登记，其余买卖前提仍需人工核验。" };
}

/** Fallback is a matching background record, never a local reevaluation of text or sources. */
function backgroundChecks(worksheet: ActionWorksheet, now: number): CandidateConditionChecks | null {
  const followup = worksheet.followup, row = followup?.last_check;
  if (!row || !followup?.current_plan_key || row.plan_key !== followup.current_plan_key || followup.requires_refresh || followup.issue || row.requires_refresh || row.issue) return null;
  if (row.position_id !== worksheet.position_id || ["position_id", "cost", "shares", "sellable_shares"].some((key) => row.position_snapshot[key as keyof typeof row.position_snapshot] !== worksheet.position_snapshot[key as keyof typeof worksheet.position_snapshot])) return null;
  const age = now - Date.parse(row.observed_at);
  return Number.isFinite(age) && age >= 0 && age <= FOLLOWUP_OBSERVATION_MAX_AGE_MS ? row.condition_checks ?? null : null;
}

function pendingItems(worksheet: ActionWorksheet): CandidateConditionCheck[] {
  if (worksheet.strategy_condition_issue) return [];
  const contract = worksheet.review_plan.watch?.condition_contract;
  if (contract?.schema_version !== 2 || contract.origin?.kind !== "position") return [];
  return contract.definitions.flatMap((row) => {
    const roles: Array<"prerequisite" | "invalidation"> = [];
    if (contract.prerequisite_ids.includes(row.id)) roles.push("prerequisite");
    if (contract.invalidation_ids.includes(row.id)) roles.push("invalidation");
    return roles.map((role) => ({ id: row.id, label: row.label, role, status: "unknown" as const,
      detail: "已采用时登记的原条件；尚无当前检查结果。", value: null, source: "", as_of: null }));
  });
}

function checkRows(checks: CandidateConditionCheck[], historical = false): CandidateChecksView["items"] {
  return checks.map((row) => ({ ...row, ...conditionCheckStatus(row), roleLabel: roleLabels[row.role] || "条件",
    ...(historical ? { statusLabel: `上次 · ${conditionCheckStatus(row).statusLabel}`, tone: "warn" as const } : {}) }));
}

/** One concrete next step from server-evaluated items; a price match is never a trade signal. */
export function positionConditionNextStep(checks: CandidateConditionChecks, state?: FollowupObservation["state"]): string {
  if (state === "paused") return "先复核暂停依据，暂不沿用原买卖条件。";
  const rows = checks.items;
  const invalidated = rows.find((row) => row.role === "invalidation" && row.status === "met");
  const unknown = rows.find((row) => row.status === "unknown");
  const unmet = rows.find((row) => row.role !== "invalidation" && row.status === "unmet");
  const manual = rows.find((row) => row.status === "manual_required");
  if (invalidated) return `先复核：${invalidated.label}已触发。`;
  if (state === "evidence_unavailable") return `先补齐${unknown ? `：${unknown.label}` : "当前来源"}，再核对原条件。`;
  if (state === "needs_review" || checks.summary === "review") return `原方案需要重核${unknown ? `：${unknown.label}缺依据` : unmet ? `：${unmet.label}未满足` : "，先核对变化依据"}。`;
  if (checks.mode === "legacy") return "旧版文字条件未逐项登记；到价后仍需人工核验原买卖前提。";
  if (!rows.length) return "等待逐项检查，不能仅凭到价判断买卖条件成立。";
  if (unknown) return `先补依据：${unknown.label}，暂不能判断条件是否成立。`;
  if (unmet) return `继续等待：${unmet.label}尚未满足。`;
  if (manual) return `下一步人工核验：${manual.label}；到价不等于买卖条件成立。`;
  if (checks.summary === "unknown") return "本次仍缺完整依据，不能只根据已满足项目判断可以买卖。";
  if (checks.summary === "manual_required") return "仍有人工前提待核验，到价不代表完整条件成立。";
  return "已列项目可复核，仍需人工确认完整前提；这不是买卖或成交信号。";
}

export function positionChecksView(worksheet: ActionWorksheet, now = Date.now()): CandidateChecksView | null {
  const watch = worksheet.review_plan.watch;
  if (!watch || worksheet.archived || worksheet.review_plan.watch_stopped) return null;
  const issue = worksheet.strategy_condition_issue;
  const checks = issue ? null : worksheet.strategy_condition_checks ?? backgroundChecks(worksheet, now);
  const registered = watch.condition_contract?.schema_version === 2;
  if (!checks || checks.schema_version !== 1) return { title: issue ? "原条件需要重核" : registered ? "逐项条件待检查" : "旧版文字条件", mode: registered ? "pending" : "legacy",
    nextStep: issue || (registered ? "等待当前逐项检查；报价到价不代表其余前提成立。" : "旧版文字条件未逐项登记；仅有报价检查，其余买卖前提需人工核验。"), items: checkRows(pendingItems(worksheet)) };
  const expired = worksheetExpired(worksheet.basis, now);
  return { title: expired ? "上次逐项检查（待更新）" : checks.summary === "review" ? "哪些条件需要复核" : "条件逐项检查", mode: expired ? "pending" : checks.mode,
    nextStep: expired ? "检查已旧，先更新当前依据，再逐项核对原条件。" : positionConditionNextStep(checks, worksheet.strategy_condition_checks ? undefined : worksheet.followup?.last_check?.state), items: checkRows(checks.items, expired) };
}

/** Home uses the recorded backend state, retaining pause and evidence-change precedence. */
export function positionObservationNextStep(row: FollowupObservation, now = Date.now()): string | null {
  if (row.condition_checks?.schema_version !== 1) return null;
  if (row.state === "paused") return positionConditionNextStep(row.condition_checks, row.state);
  const age = now - Date.parse(row.observed_at);
  if (!Number.isFinite(age) || age < 0 || age > FOLLOWUP_OBSERVATION_MAX_AGE_MS) return "先更新后台检查，再核对原买卖条件。";
  return positionConditionNextStep(row.condition_checks, row.state);
}
