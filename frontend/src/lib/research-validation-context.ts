import type { AqspSnapshot } from "@/types/aqsp";
import { gateReasonLabel } from "./display-labels";
import { gateView } from "./daily-view";
import { gatePresentation } from "./research-view";
import { isAqspSnapshotStale } from "./snapshot-freshness";

// Matches candidate_observation.research/current_evidence; this is display context,
// never a substitute for the authoritative workspace's available/entry_blocked.
const CURRENT_SOURCE_STATES = new Set(["fresh", "current", "realtime", "实时", "新鲜"]);
const REFRESH_STATES = new Map(Object.entries({ waiting: "当日验证等待中", staged: "当日验证分段进行中", completed: "当日验证运行已结束",
  timed_out: "当日验证本轮超时", skipped_lock: "当日验证等待运行资源", rejected: "当日验证本轮未被接纳", failed: "当日验证本轮失败" }));
const SOURCE_RUN_STATES = new Map(Object.entries({ running: "研究任务进行中", failed: "研究任务失败", error: "研究任务失败", partial_failed: "研究任务部分失败",
  completed: "研究任务已结束", success: "研究任务已结束", ok: "研究任务已结束", skipped: "研究任务本轮跳过" }));

export interface ResearchRunContext {
  status: string;
  detail: string;
  generatedAt: string;
  endDate: string;
  progress: string;
}

export interface ResearchValidationContext {
  historical: boolean;
  summary: string;
  history: { date: string; status: string; detail: string };
  refresh: ResearchRunContext | null;
  sourceRun: ResearchRunContext | null;
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
  const sourceDay = (value: string | undefined): string => validTimestamp(value)
    ? new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value!))
    : "";
  const refresh = historical ? null : (() => {
    const day = sourceDay(suite?.refresh_generated_at);
    const endDate = validDate(suite?.refresh_end_date) ? suite.refresh_end_date : "";
    const base = { generatedAt: day ? suite!.refresh_generated_at! : "", endDate: endDate || "未记录", progress: "" };
    if (!day || !endDate) return { ...base, status: "当日验证进度待核实", detail: "刷新时点或目标截至日未取得、无效或在未来；不能判断今日验证是否已运行，历史结果仍单独保留。" };
    if (day !== snapshot.selected_date || endDate !== snapshot.selected_date) return {
      ...base, status: day < snapshot.selected_date && endDate < snapshot.selected_date ? "历史验证刷新记录" : "验证刷新日期不匹配",
      detail: `这条运行记录属于 ${day}，目标截至 ${endDate}；不能代表 ${snapshot.selected_date} 的验证进度。`,
    };
    const staged = suite?.profiles_staged;
    const total = suite?.profiles_total;
    const progress = Number.isInteger(staged) && Number.isInteger(total) && staged! >= 0 && total! > 0 && staged! <= total!
      ? `${staged}/${total} 组策略配置已暂存；不是候选覆盖数，也不代表验证通过。` : "";
    const status = REFRESH_STATES.get(suite?.refresh_status || "") || "当日验证运行状态未知";
    const detail = [suite?.refresh_detail, progress, "运行进度与历史结果分开；运行结束不等于这只候选已覆盖或正式推荐已放行。"].filter(Boolean).join(" ");
    return { ...base, progress, status, detail };
  })();
  const sourceRun = historical || !(snapshot.source.run_status || snapshot.source.run_updated_at || snapshot.source.run_detail) ? null : (() => {
    const day = sourceDay(snapshot.source.run_updated_at);
    const base = { generatedAt: day ? snapshot.source.run_updated_at! : "", endDate: "", progress: "" };
    if (!day) return { ...base, status: "研究任务状态待核实", detail: "运行事件时点缺失、无效或在未来，不能作为今日任务完成或行情有效的依据。" };
    if (day !== snapshot.selected_date) return { ...base, status: day < snapshot.selected_date ? "历史研究任务记录" : "研究任务日期不匹配", detail: `运行事件属于 ${day}，不能代表 ${snapshot.selected_date} 的研究状态。` };
    const status = SOURCE_RUN_STATES.get(snapshot.source.run_status || "") || "研究任务状态未知";
    return { ...base, status, detail: [snapshot.source.run_detail, "任务运行状态不等于行情新鲜度，也不代替正式推荐门禁。"].filter(Boolean).join(" ") };
  })();
  // Runtime uncertainty is separate from quote freshness. A ready gate from an
  // older snapshot must not hide a failed or unverified current run record.
  const sourceRunUnavailable = Boolean(sourceRun && (
    sourceDay(snapshot.source.run_updated_at) !== snapshot.selected_date ||
    !SOURCE_RUN_STATES.has(snapshot.source.run_status || "") ||
    ["failed", "error", "partial_failed"].includes(snapshot.source.run_status || "")
  ));
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
  if (sourceRunUnavailable && sourceRun) gaps.push(`${sourceRun.status}；先核对本研究日的有效运行记录。`);
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
      : sourceRunUnavailable
        ? "先核对本研究日的有效运行记录；可继续看 AI 的支持、反证与缺证，暂不据此采用入场条件。"
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
    summary: (historical
      ? `${snapshot.selected_date} 的历史候选记录；以下不表示当前推荐状态。`
      : historyDate && historyDate < snapshot.selected_date
        ? `历史策略结果截至 ${historyDate}；${snapshot.selected_date} 的候选研究和验证需分别核对。`
        : `${snapshot.selected_date} 的候选研究、实验覆盖与正式推荐资格分别如下。`) + (refresh ? ` ${refresh.status}。` : ""),
    history: { date: historyDate || "未记录", status: historyStatus, detail: historyDetail },
    refresh,
    sourceRun,
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
            : sourceRunUnavailable ? "研究任务待核实 · 仅供观察"
            : presentation === "ready" ? "原门禁已放行 · 仍需人工复核" : "正式推荐未放行",
      reasons,
    },
    gaps: [...new Set(gaps)],
    nextStep,
    parameterDetail,
  };
}
