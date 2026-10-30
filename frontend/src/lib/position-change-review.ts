import type { ActionWorksheet } from "./api";
import { reviewEvidenceText } from "./strategy-review-context";

interface PositionChangeView {
  title: string;
  detail: string;
  next: string;
  changes: Array<{ label: string; before: string; after: string }>;
  remaining: number;
}

/** Present server comparisons only; do not infer direction or strategy validity. */
export function positionChangeEvidenceText(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return reviewEvidenceText(value);
  const row = value as Record<string, unknown>;
  if ("available" in row) return `${row.available === true ? "来源可用" : row.available === false ? "来源不可用" : "来源状态未知"} · ${typeof row.source === "string" && row.source ? row.source : "来源未知"}`;
  return reviewEvidenceText(value);
}

export function positionChangeView(worksheet: ActionWorksheet): PositionChangeView | null {
  const context = worksheet.strategy_review_context;
  if (worksheet.archived || worksheet.review_plan.watch_stopped || !worksheet.review_plan.watch || !context || context.kind !== "position" || context.status === "not_adopted") return null;
  if (context.status === "identity_mismatch") return { title: "原方案身份无法对应", detail: "原记录不能作为这笔持仓的对比依据，变化保持未知。", next: "核对当前持仓身份后，重新分析当前条件。", changes: [], remaining: 0 };
  if (!context.original) return { title: "采用时的依据待补", detail: "未取得原采用记录，不能从当前行情倒推当时判断。", next: "核对当前大盘、板块与个股依据后，再分析是否继续原路线。", changes: [], remaining: 0 };
  const changed = context.material_changed;
  const incomplete = context.missing_evidence.length > 0 || !context.original.snapshot_complete;
  const categories = new Set(context.changes.map((row) => row.category));
  const expectedProgress = worksheet.review_plan.watch.condition_contract?.schema_version === 3 && worksheet.strategy_direction_progress === true
    && context.changes.every((row) => row.category === "market" || row.category === "sector");
  const focus = [
    categories.has("market") ? "大盘" : "", categories.has("sector") ? "板块" : "", categories.has("stock") ? "个股" : "",
    categories.has("quote_source") ? "行情来源" : "", categories.has("risk") ? "风险触线事实" : "",
    categories.has("position_snapshot") || categories.has("trade_revision") ? "当前持仓" : "", categories.has("constraints") ? "个人条件" : "",
    categories.has("choice") ? "采用路线" : "",
  ].filter(Boolean);
  return {
    title: changed ? expectedProgress ? "已登记的方向条件有变化" : "采用后，判断依据有变化" : incomplete ? "原判断还不能完整核验" : "未发现实质依据变化",
    detail: changed ? expectedProgress ? "下面是采用时与现在的方向差异，按已登记条件继续逐项核验；不因已登记方向变化自动要求重选方案。" : `下面是采用时与现在的差异。${incomplete ? "原证据仍有缺口，未保存部分保持未知。" : "变化不等于直接买卖，原条件需要复核。"}` : incomplete ? "采用时的部分依据未保存或不可用，不能确认原判断仍然成立。" : context.quote_updated ? "报价或时点已更新；这本身不代表原方案失效。" : "采用时与当前未记录实质依据变化；这不代表买卖前提已满足或风险已经解除。",
    next: changed ? expectedProgress ? "查看下方方向前提与失效检查；满足后人工复核，不产生买卖任务。" : `先核对${focus.join("、") || "变化依据"}，再判断原买卖前提是否仍成立；需要新条件时重新分析并明确采用。` : incomplete ? "先补齐并核对缺失依据，再判断原条件是否适用。" : "等下方已采用条件的检查结果；条件匹配仍需复核完整前提。",
    changes: changed ? context.changes.slice(0, 3).map((row) => ({ label: row.label, before: positionChangeEvidenceText(row.before), after: positionChangeEvidenceText(row.after) })) : [],
    remaining: changed ? Math.max(0, context.changes.length - 3) : 0,
  };
}
