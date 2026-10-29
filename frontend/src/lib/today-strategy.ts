import { api, type AlertData, type CandidateWorkspace, type FollowupObservation, type PortfolioData } from "./api";
import { normalizeAlerts, ALERT_LEVEL_NAMES } from "./alert-view";
import { normalizePortfolio } from "./portfolio-view";
import { positionPlanHref } from "./position-workspace";
import { candidateChecksView } from "./candidate-conditions";

export interface TodayStrategySources {
  portfolio: PortfolioData | null;
  alerts: AlertData | null;
  followups: FollowupObservation[] | null;
  candidates: CandidateWorkspace[] | null;
  issues: string[];
}
export interface StrategyAttention {
  key: string; symbol: string; name: string; kind: "position" | "candidate";
  title: string; reason: string; next: string; href: string;
  observedAt: string | null; quoteAsOf: string | null; priority: number;
}
export interface TodayStrategyView { items: StrategyAttention[]; total: number; issues: string[]; noHoldings: boolean; }

/** Fixed four reads; never starts a model, monitor check or per-stock request. */
export async function readTodayStrategySources(client: Pick<typeof api, "portfolio" | "alerts" | "positionFollowups" | "candidateObservations"> = api): Promise<TodayStrategySources> {
  const [portfolio, alerts, followups, candidates] = await Promise.allSettled([
    client.portfolio(), client.alerts(), client.positionFollowups(), client.candidateObservations(),
  ]);
  const issues: string[] = [];
  const validPortfolio = portfolio.status === "fulfilled" && Array.isArray(portfolio.value?.holdings) && portfolio.value.holdings.every((row) => row && /^\d{6}$/.test(row.code) && typeof row.shares === "number" && Number.isFinite(row.shares));
  const validAlerts = alerts.status === "fulfilled" && Array.isArray(alerts.value?.events);
  const validFollowups = followups.status === "fulfilled" && Array.isArray(followups.value?.items);
  const validCandidates = candidates.status === "fulfilled" && Array.isArray(candidates.value?.items);
  if (!validPortfolio) issues.push("持仓身份暂未读全，无法核对全部持仓策略");
  if (!validAlerts) issues.push("风险提醒暂未读全，不能判断是否还有待复核风险");
  if (!validFollowups) issues.push("持仓后台跟进暂未读全");
  if (!validCandidates) issues.push("候选观察暂未读全");
  return {
    portfolio: validPortfolio && portfolio.status === "fulfilled" ? normalizePortfolio(portfolio.value) : null,
    alerts: validAlerts && alerts.status === "fulfilled" ? normalizeAlerts(alerts.value) : null,
    followups: validFollowups && followups.status === "fulfilled" ? followups.value.items : null,
    candidates: validCandidates && candidates.status === "fulfilled" ? candidates.value.items : null,
    issues,
  };
}

const attentionStates = new Set(["price_watch", "below_watch", "paused", "needs_review", "evidence_unavailable", "missing_prices"]);
const statePriority: Record<string, number> = { paused: 1, needs_review: 1, evidence_unavailable: 2, missing_prices: 2, price_watch: 3, below_watch: 3 };
const timestamp = (value: string | null | undefined) => value && Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;

/** Priorities describe existing risk/observation states, never stock scores or AI votes. */
export function buildTodayStrategy(sources: TodayStrategySources): TodayStrategyView {
  const issues = [...sources.issues];
  const holdings = sources.portfolio?.holdings.filter((row) => row.shares > 0) ?? [];
  const ownedCodes = new Set(holdings.map((row) => row.code));
  const rows: StrategyAttention[] = [];
  const seen = new Set<string>();
  for (const holding of holdings) {
    const id = holding.position_id;
    if (!id) { issues.push(`${holding.name || holding.code} 的持仓身份待核对，请到我的持仓查看`); continue; }
    if (seen.has(id)) continue;
    seen.add(id);
    const events = (sources.alerts?.events ?? []).filter((event) => event.status === "OPEN" && event.position_id === id && event.symbol === holding.code);
    const latestFollowup = (sources.followups ?? []).filter((row) => row.position_id === id && row.position_snapshot?.position_id === id && row.symbol === holding.code)
      .sort((a, b) => timestamp(b.observed_at) - timestamp(a.observed_at))[0];
    const followup = latestFollowup && (attentionStates.has(latestFollowup.state) || latestFollowup.state === "waiting") ? latestFollowup : undefined;
    const hardRisk = events.some((event) => event.level === "L1");
    const event = (hardRisk ? events.filter((row) => row.level === "L1") : events).slice().sort((a, b) => timestamp(b.updated_at) - timestamp(a.updated_at))[0];
    const changedSnapshot = !!followup && (followup.position_snapshot.shares !== holding.shares || followup.position_snapshot.cost !== holding.cost);
    const needsRefresh = !!followup?.requires_refresh || changedSnapshot;
    const followupFirst = followup && (!event || statePriority[followup.state] <= 1);
    const title = hardRisk ? "风险记录待复核" : followupFirst ? followup.state === "waiting" ? "继续等待持仓条件" : "原方案需要核对" : event ? "风险记录待复核" : "核对这笔持仓的下一步";
    const reason = followupFirst && !hardRisk ? followup.detail : event ? [event.basis_json.rule || ALERT_LEVEL_NAMES[event.level] || "风险条件", event.basis_json.actual].filter(Boolean).join("；") : "当前没有读到需要关注的后台记录；这不代表没有风险。";
    rows.push({ key: `position:${id}`, symbol: holding.code, name: holding.name && holding.name !== holding.code ? holding.name : "股票名称待补", kind: "position",
      title, reason, next: needsRefresh ? followup?.issue || "用当前大盘、板块和仓位重新核对原条件，再决定继续等待或调整路线。" : followup ? followup.title + "；进入策略页核对原条件与当前依据。" : "比较持有、减仓和做 T 的适用条件，保留能持续跟踪的方案。",
      href: positionPlanHref(id, "/today")!, observedAt: followupFirst && !hardRisk ? followup.observed_at : event?.updated_at ?? null,
      quoteAsOf: followupFirst && !hardRisk ? followup.quote_as_of : null,
      priority: hardRisk ? 0 : followup ? statePriority[followup.state] ?? 4 : event ? 2 : 5 });
  }
  const legacy = (sources.alerts?.events ?? []).some((event) => event.status === "OPEN" && ownedCodes.has(event.symbol) && !event.position_id);
  if (legacy) issues.push("部分旧风险记录缺少仓位身份，未合并到当前策略；可展开完整风险记录核对");
  const candidateRows = new Map<string, StrategyAttention>();
  for (const item of sources.candidates ?? []) {
    if (!/^\d{6}$/.test(item.symbol) || ownedCodes.has(item.symbol) || item.ownership?.status === "owned" || item.watch?.status !== "active") continue;
    const observation = item.observation;
    const unknown = item.ownership?.status !== "unowned";
    const checks = unknown ? null : candidateChecksView(item);
    const row: StrategyAttention = { key: `candidate:${item.symbol}`, symbol: item.symbol, name: item.name && item.name !== item.symbol ? item.name : "股票名称待补", kind: "candidate",
      title: unknown ? "持仓身份待核清" : observation?.state === "price_watch" ? "上次检查已到价，待核验" : observation?.state === "needs_review" || observation?.state === "evidence_unavailable" ? "候选依据需要重核" : item.watch.choice === "defer" ? "保持暂缓，等待新依据" : "继续等待候选条件",
      reason: unknown ? "暂不能确认仍是未持仓候选，先核对身份。" : checks?.mode === "registered" && observation && !["needs_review", "evidence_unavailable"].includes(observation.state) ? "后台分别记录报价、已登记前提与人工核验项。" : observation?.state === "price_watch" ? "后台报价曾达到已保存观察价；止跌、承接等其他前提仍需人工核验。" : observation?.detail || "观察方案已保存，尚未读到后台检查记录。",
      next: unknown ? "核对身份后再决定是否沿用入场条件。" : checks?.nextStep || "更新观察记录后，核对原前提与当前依据。",
      href: `/stock/${item.symbol}#candidate-observation`, observedAt: observation?.observed_at ?? null, quoteAsOf: observation?.quote_as_of ?? null,
      priority: unknown ? 2 : statePriority[observation?.state ?? ""] ?? (item.watch.choice === "defer" ? 6 : 4) };
    const previous = candidateRows.get(item.symbol);
    if (!previous || timestamp(row.observedAt) > timestamp(previous.observedAt)) candidateRows.set(item.symbol, row);
  }
  rows.push(...candidateRows.values());
  rows.sort((a, b) => a.priority - b.priority || timestamp(b.observedAt) - timestamp(a.observedAt) || a.key.localeCompare(b.key));
  return { items: rows.slice(0, 3), total: rows.length, issues: [...new Set(issues)], noHoldings: sources.portfolio !== null && holdings.length === 0 };
}
