import type { CandidateCondition, CandidateConditionCatalog, CandidateConditionCheck, CandidateDiscussion, CandidateWorkspace } from "./api";

export interface CandidateActionChecksView {
  prerequisites: string[];
  invalidations: string[];
  manual: string;
}

/** Labels belong to the same discussion evidence; current catalog cannot rewrite old AI choices. */
export function candidateActionChecks(action: CandidateCondition, result: CandidateDiscussion, workspace: CandidateWorkspace): CandidateActionChecksView {
  if (action.choice === "defer") return { prerequisites: [], invalidations: [], manual: "暂缓仅保存原因为人工复核依据，不建立解除暂缓的自动条件。" };
  const catalog: CandidateConditionCatalog | undefined = result.condition_catalog ??
    (result.evidence_version === workspace.evidence_version ? workspace.condition_catalog : undefined);
  const rows = catalog?.schema_version === 1 ? catalog.items : [];
  const label = (id: string): string => rows.find((row) => row.id === id)?.label || "已登记条件（定义待核验）";
  const prerequisites = [...new Set(action.prerequisite_ids ?? [])].map(label);
  const invalidations = [...new Set(action.invalidation_ids ?? [])].map(label);
  return {
    prerequisites, invalidations,
    manual: !prerequisites.length && !invalidations.length ? "文字条件尚未逐项登记，止跌、承接等仍需人工核验。" : "文字中的止跌、承接等其余前提仍需人工核验。",
  };
}

export interface CandidateChecksView {
  title: string;
  nextStep: string;
  mode: "registered" | "legacy" | "pending";
  items: Array<CandidateConditionCheck & { statusLabel: string; roleLabel: string; tone: "neutral" | "warn" }>;
}

const roleLabels = { prerequisite: "前提", invalidation: "失效条件", price: "报价", manual: "人工核验" };
function checkStatus(row: CandidateConditionCheck): { statusLabel: string; tone: "neutral" | "warn" } {
  if (row.status === "met") return row.role === "invalidation" ? { statusLabel: "已触发 · 需复核", tone: "warn" } : { statusLabel: "已满足", tone: "neutral" };
  if (row.status === "unmet") return { statusLabel: row.role === "invalidation" ? "未触发" : "未满足", tone: "neutral" };
  if (row.status === "manual_required") return { statusLabel: "需人工核验", tone: "warn" };
  return { statusLabel: "缺依据", tone: "warn" };
}

/** Render only server evaluations. A reached price never certifies the rest of a strategy. */
export function candidateChecksView(workspace: CandidateWorkspace): CandidateChecksView | null {
  if (!workspace.watch || workspace.ownership.status !== "unowned") return null;
  const checks = workspace.observation?.condition_checks;
  if (workspace.watch.status === "stopped") return {
    mode: checks?.schema_version === 1 ? checks.mode : "pending", title: "停止前检查记录",
    nextStep: "此观察已停止，以下保留最后检查记录，不再后台监控。",
    items: checks?.schema_version === 1 ? checks.items.map((row) => ({ ...row, ...checkStatus(row), roleLabel: roleLabels[row.role] || "条件" })) : [],
  };
  if (workspace.watch.choice === "defer" && !["needs_review", "evidence_unavailable"].includes(workspace.observation?.state ?? "")) {
    const registered = checks?.schema_version === 1 && checks.mode === "registered";
    return { mode: registered ? "registered" : "legacy", title: registered ? "暂缓依据跟进" : "旧版暂缓原因", nextStep: "保持暂缓，依据变化后复核原前提与失效说明。", items: checks?.schema_version === 1 ? checks.items.map((row) => ({ ...row, ...checkStatus(row), roleLabel: roleLabels[row.role] || "条件" })) : [] };
  }
  if (!checks || checks.schema_version !== 1) {
    const registered = workspace.watch.condition_contract?.schema_version === 1;
    return { mode: registered ? "pending" : "legacy", title: registered ? "逐项条件待检查" : "旧版文字条件", items: [],
      nextStep: registered ? "等待后台逐项核对；报价到价不代表其余条件成立。" : "旧版文字条件未逐项登记；仅有报价检查，其余前提需人工核验。" };
  }
  const items = checks.items.map((row) => ({ ...row, ...checkStatus(row), roleLabel: roleLabels[row.role] || "条件" }));
  if (checks.mode === "legacy") return { mode: "legacy", title: "旧版文字条件", items, nextStep: "旧版文字条件未逐项登记；到价后仍需人工核验原前提。" };
  if (!items.length) return { mode: "pending", title: "逐项条件待检查", items, nextStep: "本次尚无逐项检查结果，不能仅凭到价认定前提成立。" };
  const invalidated = items.find((row) => row.role === "invalidation" && row.status === "met");
  const unknown = items.find((row) => row.status === "unknown");
  const unmet = items.find((row) => row.role !== "invalidation" && row.status === "unmet");
  const manual = items.find((row) => row.status === "manual_required");
  const unavailable = workspace.observation?.state === "evidence_unavailable";
  const review = checks.summary === "review" || workspace.observation?.state === "needs_review" || unavailable;
  const nextStep = invalidated ? `先复核：${invalidated.label}已触发。` : unavailable ? `当前依据不可用，先补齐${unknown ? `：${unknown.label}` : "来源"}后重核原条件。` : review ?
    `原依据发生变化，先复核${unknown ? `：${unknown.label}缺依据` : unmet ? `：${unmet.label}未满足` : "当前条件"}。` : unknown ?
      `先补依据：${unknown.label}，暂不能判断条件是否成立。` : unmet ? `继续等待：${unmet.label}尚未满足。` : manual ?
        `下一步人工核验：${manual.label}；报价满足仍不等于可以买入。` : checks.summary === "unknown" ? "本次仍缺完整依据，不能只根据已满足项目判断可以参与。" : checks.summary === "manual_required" ? "仍有人工前提待核验，报价满足不代表完整条件成立。" : "已列项目可复核，仍需人工确认完整前提；这不是买入或成交信号。";
  return { mode: "registered", title: review || invalidated ? "哪些条件需要复核" : "条件逐项检查", items, nextStep };
}
