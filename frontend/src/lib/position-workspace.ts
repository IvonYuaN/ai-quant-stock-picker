import { api, type ActionWorksheet, type Holding, type PlanParameters, type PlanTarget, type PositionConstraints, type ReviewChoice } from "./api";

/** A plan belongs to a position, never to a stock code guessed from a quote. */
export function positionPlanHref(positionId: string | null | undefined, from = "/my/holdings"): string | null {
  if (!positionId?.trim()) return null;
  return `/my/holdings/${encodeURIComponent(positionId)}/plan?from=${encodeURIComponent(planReturnHref(from))}`;
}

/** Keep return navigation inside the three owned entry surfaces. */
export function planReturnHref(value: string | null | undefined): string {
  if (!value || !/^\/(?:today|my\/holdings|stock\/\d{6})(?:[?#].*)?$/.test(value)) return "/my/holdings";
  return value;
}

export function ownedPositionForStock(holdings: readonly Holding[], code: string): Holding | null {
  const matches = holdings.filter((row) => row.code === code && row.shares > 0 && !!row.position_id?.trim());
  return matches.length === 1 ? matches[0] : null;
}

export function readPlanWorksheet(target: PlanTarget): Promise<ActionWorksheet> {
  return target.kind === "position" ? api.positionActionPlan(target.id) : api.alertActionPlan(target.id);
}

export function worksheetGuard(worksheet: ActionWorksheet): { expected_position: ActionWorksheet["position_snapshot"]; expected_revision: string } {
  if (worksheet.archived) throw new Error("这笔持仓已清仓，只能查看历史记录。");
  if (!worksheet.revision || !worksheet.position_snapshot.position_id) throw new Error("当前计划版本或持仓身份待确认，请重新读取后再继续。");
  return { expected_position: worksheet.position_snapshot, expected_revision: worksheet.revision };
}

export async function savePlanChoice(target: PlanTarget, worksheet: ActionWorksheet, choice: ReviewChoice, parameters?: PlanParameters): Promise<ActionWorksheet> {
  const guard = worksheetGuard(worksheet);
  if (target.kind === "position") await api.savePositionPlan(target.id, choice, guard.expected_position, guard.expected_revision, parameters);
  else await api.alertReviewPlan(target.id, choice, parameters, guard.expected_position, guard.expected_revision);
  return readPlanWorksheet(target);
}

export interface ConstraintsDraft { attention: PositionConstraints["attention"]; extra_cash: string; loss_limit: string }
export const EMPTY_CONSTRAINTS: ConstraintsDraft = { attention: "unknown", extra_cash: "", loss_limit: "" };
export function constraintsDraft(worksheet: ActionWorksheet): ConstraintsDraft {
  const value = worksheet.constraints;
  return value ? { attention: value.attention, extra_cash: value.extra_cash == null ? "" : String(value.extra_cash), loss_limit: value.loss_limit == null ? "" : String(value.loss_limit) } : { ...EMPTY_CONSTRAINTS };
}
export function parseConstraints(draft: ConstraintsDraft): Pick<PositionConstraints, "attention" | "extra_cash" | "loss_limit"> {
  const number = (value: string, label: string) => {
    if (!value.trim()) return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) throw new Error(`${label}请填写不小于 0 的金额，或留空表示不确定。`);
    return parsed;
  };
  return { attention: draft.attention, extra_cash: number(draft.extra_cash, "备用资金"), loss_limit: number(draft.loss_limit, "最多还能承受的损失") };
}

/** A changed fact cannot silently adopt the revision attached to old edits. */
export function draftNeedsReview(draftRevision: string | undefined, worksheet: ActionWorksheet): boolean {
  return !worksheet.revision || draftRevision !== worksheet.revision;
}
export function discussionIsCurrent(result: ActionWorksheet["comparison"], worksheet: ActionWorksheet): boolean {
  return !!result && !!worksheet.revision && result.review_revision === worksheet.revision && result.analysis_basis_id === worksheet.analysis_basis_id;
}
