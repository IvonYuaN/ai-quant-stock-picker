import type { CandidateAdoptionInput, CandidateChoice, CandidateDiscussion, CandidateWorkspace, WatchComparator } from "./api";

export const CANDIDATE_OBSERVATIONS_CHANGED_EVENT = "aqsp:candidate-observations-changed";
export const CANDIDATE_CHOICES: Record<CandidateChoice, { title: string; detail: string }> = {
  pullback: { title: "等待回落", detail: "等价格回到有依据的位置，再核对承接与风险。" },
  confirmation: { title: "等待确认", detail: "等走势或消息得到验证，再重新考虑是否参与。" },
  defer: { title: "暂缓关注", detail: "先不建立到价条件，等待缺失依据或环境改善。" },
};

export function candidateDiscussionKey(result: CandidateDiscussion | null): string {
  return result ? JSON.stringify([result.id, result.generated_at, result.evidence_version, result.resource_version, result.research_date, result.price_refs, result.summary]) : "";
}

export function candidateWorkspaceIssue(workspace: CandidateWorkspace, researchDate?: string | null, historical = false): string {
  if (workspace.ownership.status === "owned") return "你已持有这只股票，请到持仓策略核对现有仓位。";
  if (workspace.ownership.status !== "unowned") return "持仓身份尚未核清，暂不建立候选观察。";
  if (historical || (researchDate && researchDate !== workspace.research_date)) return "当前正在回看历史研究；请返回当前研究，再分析入场条件。";
  if (!workspace.available) return workspace.issues.join("；") || "当前研究依据不足，暂不能分析入场条件。";
  return "";
}

/** Quote changes alone do not replace the source or the human confirmation. */
export function candidateDiscussionIssue(result: CandidateDiscussion | null, workspace: CandidateWorkspace): string {
  const issue = candidateWorkspaceIssue(workspace);
  if (issue) return issue;
  if (result?.status === "partial") return result.summary_issue || "部分角色未完成，保留原分析；重试完成后再采用。";
  if (!result?.summary || result.status !== "complete") return result?.summary_issue || "尚未形成可采用的汇总条件。";
  if (result.resource_version !== workspace.version || result.evidence_version !== workspace.evidence_version || result.research_date !== workspace.research_date) return "研究依据或观察版本已变化，请重新分析后采用。";
  if (!result.id || !Number.isFinite(Date.parse(result.generated_at))) return "分析来源无法核验，请重新分析。";
  return "";
}

export function candidateAdoptionIssue(result: CandidateDiscussion | null, workspace: CandidateWorkspace, choice: CandidateChoice, index: number | null, comparator: WatchComparator | ""): string {
  const issue = candidateDiscussionIssue(result, workspace);
  if (issue || !result?.summary) return issue;
  const route = result.summary.routes.find((row) => row.choice === choice);
  if (!route) return "本次分析没有这条路线，请重新核对。";
  if (choice === "defer") return "";
  if (workspace.entry_blocked) return "当前研究门禁阻塞，先比较原因或暂缓，不建立入场到价观察。";
  if (workspace.new_entry_blocked) return workspace.new_entry_block_reason || workspace.coverage_note || "当前依据不能建立新的入场条件。";
  const action = index === null ? undefined : result.summary.actions[index];
  const reference = action?.price_ref ? result.price_refs[action.price_ref] : undefined;
  if (!action || action.choice !== choice || !reference || !Number.isFinite(reference.price) || reference.price <= 0 || !reference.source || !Number.isFinite(Date.parse(reference.as_of))) return "这项条件缺少可核验的来源价位，暂不建立到价观察。";
  if (route.judgment !== "investigate") return "这条路线当前不宜或依据待补，补齐依据后重新分析再采用。";
  if (!comparator) return "请核对条件，并选择报价达到或高于、还是达到或低于观察价时提醒。";
  if (workspace.quote_issue) return "当前行情不可用，请刷新核对后采用。";
  return "";
}

export function candidateAdoptionInput(result: CandidateDiscussion, workspace: CandidateWorkspace, choice: CandidateChoice, index: number | null, comparator: WatchComparator | "", operationId: string): CandidateAdoptionInput {
  const issue = candidateAdoptionIssue(result, workspace, choice, index, comparator);
  if (issue) throw new Error(issue);
  if (!operationId.trim()) throw new Error("缺少本次确认编号，请重新核对。");
  return { expected_version: workspace.version, expected_evidence_version: workspace.evidence_version, discussion_id: result.id, generated_at: result.generated_at,
    choice, action_index: choice === "defer" ? null : index, comparator: choice === "defer" ? null : comparator as WatchComparator, operation_id: operationId };
}

/** Server observations are facts at observed_at; the client never invents a trigger. */
export function candidateObservationView(workspace: CandidateWorkspace): { title: string; detail: string; checkedAt: string | null; tone: "neutral" | "warn" } {
  if (workspace.ownership.status === "owned") return { title: "已转入持仓策略", detail: "已有真实持仓，候选入场条件不再作为当前参与计划。", checkedAt: null, tone: "neutral" };
  if (workspace.watch?.status === "stopped") return { title: "候选观察已停止", detail: "原条件不再监控；需要继续时，重新分析并采用。", checkedAt: workspace.observation?.observed_at ?? null, tone: "neutral" };
  if (workspace.ownership.status === "unavailable") return { title: "持仓身份待核清", detail: "保留原条件，暂不能据此判断可以参与。", checkedAt: null, tone: "warn" };
  if (workspace.observation) return { title: workspace.observation.title, detail: workspace.observation.detail, checkedAt: workspace.observation.observed_at, tone: ["price_watch", "needs_review", "evidence_unavailable"].includes(workspace.observation.state) ? "warn" : "neutral" };
  if (workspace.watch) return { title: "观察已保存，等待后台检查", detail: "保存不代表已经到价；离开页面后由后台继续核对。", checkedAt: null, tone: "neutral" };
  return { title: "先比较入场条件", detail: "看看为什么等、等到哪里，以及什么情况需要放弃。", checkedAt: null, tone: "neutral" };
}

export function candidateResearchHref(symbol: string): string {
  if (!/^\d{6}$/.test(symbol)) throw new Error("股票代码无效");
  return `/stock/${symbol}#candidate-observation`;
}

/** Keep current adopted observations separate from unadopted drafts and stopped history. */
export function currentCandidateObservations(items: readonly CandidateWorkspace[]): CandidateWorkspace[] {
  const priority: Record<string, number> = { needs_review: 0, evidence_unavailable: 1, price_watch: 2, waiting: 3, deferred: 4 };
  return items.filter((row) => row.watch?.status === "active" && row.ownership.status !== "owned")
    .slice().sort((a, b) => (priority[a.observation?.state ?? "waiting"] ?? 5) - (priority[b.observation?.state ?? "waiting"] ?? 5) || a.symbol.localeCompare(b.symbol));
}

export function candidateEvidenceRows(workspace: CandidateWorkspace): Array<{ key: string; label: string; summary: string; source: string; asOf: string | null }> {
  return (["market", "sector", "stock"] as const).map((key) => {
    const raw = workspace.evidence[key];
    const value = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
    return { key, label: { market: "大盘", sector: "板块", stock: "个股" }[key], summary: typeof value.summary === "string" && value.summary ? value.summary : "尚缺可核验依据",
      source: typeof value.source === "string" && value.source ? value.source : "来源待补", asOf: typeof value.as_of === "string" ? value.as_of : null };
  });
}
