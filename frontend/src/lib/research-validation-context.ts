import type { AqspSnapshot } from "@/types/aqsp";
import { gateReasonLabel } from "./display-labels";
import { gateView } from "./daily-view";
import { gatePresentation } from "./research-view";
import { isAqspSnapshotStale } from "./snapshot-freshness";

// Matches candidate_observation.research/current_evidence; this is display context,
// never a substitute for the authoritative workspace's available/entry_blocked.
const CURRENT_SOURCE_STATES = new Set(["fresh", "current", "realtime", "实时", "新鲜"]);

export interface ResearchValidationContext {
  historical: boolean;
  summary: string;
  history: { date: string; status: string; detail: string };
  candidate: { date: string; discussion: string; validation: string; sourceTime: string };
  recommendation: { status: string; reasons: readonly string[] };
  gaps: readonly string[];
  nextStep: string;
  parameterDetail: string;
}

/** Presentation only: no new eligibility rule, expiry budget, or recommendation override. */
export function researchValidationContext(
  snapshot: AqspSnapshot,
  symbol: string,
  now = Date.now(),
): ResearchValidationContext {
  const historical = Boolean(snapshot.meta?.historical);
  const candidate = snapshot.candidates.find((item) => item.symbol === symbol);
  const suite = snapshot.variant_suite;
  const chain = snapshot.research_chain;
  const validDate = (date: string | undefined): date is string => {
    if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return false;
    const parsed = new Date(`${date}T00:00:00Z`);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date;
  };
  const historyDate = validDate(suite?.end_date) ? suite.end_date : "";
  const historyFuture = Boolean(historyDate && historyDate > snapshot.selected_date);
  const historyCurrent = historyDate === snapshot.selected_date;
  const hasResults = Boolean(historyDate && suite && suite.variant_count > 0 && !suite.last_error);
  const discussionRecorded = Boolean(candidate && chain?.debated_symbols.includes(symbol) &&
    !chain.pending_review_symbols.includes(symbol) && snapshot.debates.some((item) => item.symbol === symbol));
  // This records experiment coverage, never profitability, validity, or entry permission.
  const covered = Boolean(candidate && hasResults && historyCurrent && chain?.variant_candidate_symbols.includes(symbol));
  const stale = isAqspSnapshotStale(snapshot, now);
  const currentSource = (value: string): boolean => CURRENT_SOURCE_STATES.has(value.trim().toLowerCase());
  const validTimestamp = (value: string | undefined, allowFuture = false): boolean => {
    if (!value || !validDate(value.slice(0, 10)) || !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return false;
    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp) && (allowFuture || timestamp <= now);
  };
  const sourceIssues: string[] = [];
  if (!currentSource(snapshot.source.status) || snapshot.source.lag_days > 0) sourceIssues.push("总体行情来源未确认为当前有效状态。");
  if (candidate && !currentSource(candidate.freshness || snapshot.source.status)) sourceIssues.push("这只候选的来源未确认为当前有效状态。");
  if (!validTimestamp(snapshot.generated_at)) sourceIssues.push("快照生成时点缺失、无时区或在未来，需核实。");
  if (!validTimestamp(snapshot.stale_after, true)) sourceIssues.push("快照有效截止时点缺失或无效，需核实。");
  if (candidate && !validTimestamp(candidate.data_fetched_at)) sourceIssues.push("候选取数时点缺失、无时区或在未来，需核实。");
  const sourceUnavailable = sourceIssues.length > 0;
  const gate = snapshot.recommendation_gate;
  const presentation = gatePresentation(gate);
  const reasons = [...new Set((gate?.reasons ?? []).map((reason) =>
    /^(circuit_breaker|freshness_not_ready|research_display)/.test(reason)
      ? gateView({ ...gate!, reasons: [reason] }).detail : gateReasonLabel(reason),
  ).filter(Boolean))];
  const gaps: string[] = [];
  if (!candidate) gaps.push("这只股票不在所选日期候选中。");
  if (!hasResults) gaps.push(suite?.last_error || "历史策略结果尚未完整记录。");
  if (historyFuture) gaps.push("变体截至日晚于研究日，不能用于当时的候选判断。");
  if (candidate && !discussionRecorded) gaps.push("这只候选的同日多空讨论尚未齐全。");
  if (candidate && !covered) gaps.push(historyDate && historyDate < snapshot.selected_date
    ? `历史结果截至 ${historyDate}，这只候选的 ${snapshot.selected_date} 验证仍待完成。`
    : "这只候选尚无当日实验池覆盖记录。");
  if (!historical && stale) gaps.push("当前研究已过期，需先取得有效数据。");
  if (!historical) gaps.push(...sourceIssues);
  if (chain?.blocker && !gaps.includes(chain.blocker)) gaps.push(chain.blocker);

  const historyStatus = historyFuture ? "日期不匹配" : !hasResults ? "结果待补" : historyCurrent ? "当日结果已生成" : "历史结果已生成";
  const historyDetail = hasResults
    ? `${suite!.variant_count} 个纸面变体；结果生成不等于验证通过。快照未提供历史变体参数身份，不能确认与当前候选一致。`
    : suite?.last_error || "尚无可核实的变体结果，不能用候选评分代替策略验证。";
  const candidateValidation = !candidate ? "无同日候选记录" : covered ? "已进入当日实验池 · 不等于验证通过" : "当日候选验证待完成";
  const nextStep = historical
    ? "这里只回看当时依据；判断当前机会，请返回今日研究后重新核对。"
    : stale || sourceUnavailable
      ? "先刷新有效研究与行情；恢复前仅核对缺口，不据此采用入场条件。"
      : !candidate
        ? "返回今日候选查看已有研究对象，也可保留自选等待入选。"
        : presentation === "ready"
          ? "先看下方 AI 的支持、反证与缺证；结合当前有源依据人工复核条件，采用观察方案不会自动成交。"
          : "先看下方 AI 的支持、反证与缺证；暂缓观察可跟进补证，入场条件仍须等待正式推荐门禁复核。";
  const identity = snapshot.parameter_snapshot;
  const parameterDetail = identity
    ? `本候选参数：${identity.version}，生效于 ${identity.effective_from}，指纹 ${identity.fingerprint}。历史变体未提供对应指纹，不能证明参数相同。`
    : "本候选参数身份未记录；历史变体也未提供可比对的参数指纹。";
  return {
    historical,
    summary: historical
      ? `${snapshot.selected_date} 的历史候选记录；以下不表示当前推荐状态。`
      : historyDate && historyDate < snapshot.selected_date
        ? `历史策略结果截至 ${historyDate}；${snapshot.selected_date} 的候选研究和验证需分别核对。`
        : `${snapshot.selected_date} 的候选研究、实验覆盖与正式推荐资格分别如下。`,
    history: { date: historyDate || "未记录", status: historyStatus, detail: historyDetail },
    candidate: {
      date: snapshot.selected_date,
      discussion: !candidate ? "无同日候选记录" : discussionRecorded ? "同日多空讨论已记录" : "同日多空讨论待补",
      validation: candidateValidation,
      sourceTime: candidate?.data_fetched_at || "未记录",
    },
    recommendation: {
      status: historical ? "历史门禁记录 · 仅供回看"
        : presentation === "unavailable" ? "推荐状态未取得"
          : stale ? "依据失效 · 仅供观察"
            : sourceUnavailable ? "依据待核实 · 仅供观察"
            : presentation === "ready" ? "原门禁已放行 · 仍需人工复核" : "正式推荐未放行",
      reasons,
    },
    gaps: [...new Set(gaps)],
    nextStep,
    parameterDetail,
  };
}
