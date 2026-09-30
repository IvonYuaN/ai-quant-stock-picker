// 选股绩效的展示模型。
//
// 这里最重要的一条不是"怎么画"，而是**什么时候不许画**：
// 宪法 §5.4 规定冷启动期（独立信号日 < 30）**不展示胜率**，只显示积累进度。
// 原因写在架构文档里：样本不足时展示空表或数字会"误导用户以为系统故障或产生虚假信心"。
//
// 后端已经给了 displayable，但前端仍要独立守一道 ——
// 显示层的诚实不能只依赖上游，否则后端一改口径这里就会悄悄违规。
import type {
  PerformanceDecayAlert,
  PerformancePayload,
  PerformanceRecentPick,
} from "./api";
import { asArray, asNumber, asNullableNumber, asRecord, asString } from "./safe";

export type Tone = "ok" | "warn" | "neutral";

export interface StrategyView {
  name: string;
  independentSignalDays: number;
  totalPicks: number;
  winCount: number;
  hitRate: number;
  /** 只有样本够（≥ 门槛）才允许展示命中率 */
  canShowHitRate: boolean;
  weightBase: number;
  weightConfidence: number;
  /** PnL 派生，标注为仅观测 */
  avgReturnPct: number;
}

export interface DecayAlertView {
  strategy: string;
  lookbackDays: number;
  recentWinRate: number;
  severity: string;
  tone: Tone;
  recommendation: string;
}

/** 票级复盘的一行：上次选了哪只票、事后如何（红涨绿跌由调用方上色）。 */
export interface RecentPickView {
  symbol: string;
  name: string;
  signalDate: string;
  exitDate: string;
  /** 绝对收益 %；缺失为 null，区分"未记录"与"真的是 0" */
  returnPct: number | null;
  /** 超额收益 %；缺失为 null */
  excessReturnPct: number | null;
  win: boolean;
  exitReason: string;
  strategies: readonly string[];
}

export interface PerformanceView {
  available: boolean;
  reason: string;
  generatedAt: string;
  /** 冷启动：样本不足以支撑统计推断 */
  coldStart: boolean;
  independentSignalDays: number;
  minSignalDays: number;
  /** 0~1，用于进度条 */
  progress: number;
  overallHitRate: number | null;
  canShowOverallHitRate: boolean;
  strategies: readonly StrategyView[];
  decayAlerts: readonly DecayAlertView[];
  /** 票级复盘明细（最近 N 笔 validated 信号）；旧后端缺失时为 [] */
  recentPicks: readonly RecentPickView[];
  statusCounts: readonly [string, number][];
  notes: readonly string[];
  /** 台账新鲜度 */
  latestSignalDate: string;
  ledgerUpdatedAt: string;
  tradingDaysSinceLatest: number | null;
  stale: boolean;
  staleAfterTradingDays: number;
}

export function severityTone(severity: string): Tone {
  const text = severity.trim().toLowerCase();
  if (text.includes("critical")) return "warn";
  if (text.includes("warning") || text.includes("warn")) return "warn";
  return "neutral";
}

function toStrategy(raw: unknown, minDays: number): StrategyView {
  const record = asRecord(raw);
  const days = asNumber(record.independent_signal_days);
  return {
    name: asString(record.name, "未命名策略"),
    independentSignalDays: days,
    totalPicks: asNumber(record.total_picks),
    winCount: asNumber(record.win_count),
    hitRate: asNumber(record.hit_rate),
    // 双保险：后端给了 displayable，这里再用样本数判一次
    canShowHitRate: Boolean(record.displayable) && days >= minDays,
    weightBase: asNumber(record.weight_base, 1),
    weightConfidence: asNumber(record.weight_confidence),
    avgReturnPct: asNumber(record.avg_return_pct),
  };
}

function toAlert(raw: unknown): DecayAlertView {
  const record = asRecord(raw) as Partial<PerformanceDecayAlert>;
  const severity = asString(record.severity);
  return {
    strategy: asString(record.strategy, "未命名策略"),
    lookbackDays: asNumber(record.lookback_days, 7),
    recentWinRate: asNumber(record.recent_win_rate),
    severity: severity || "unknown",
    tone: severityTone(severity),
    recommendation: asString(record.recommendation),
  };
}

function toRecentPick(raw: unknown): RecentPickView {
  const record = asRecord(raw) as Partial<PerformanceRecentPick>;
  const strategies = asArray<unknown>(record.strategies).map((s) => asString(s)).filter(Boolean);
  return {
    symbol: asString(record.symbol),
    name: asString(record.name, asString(record.symbol)),
    signalDate: asString(record.signal_date),
    exitDate: asString(record.exit_date),
    returnPct: asNullableNumber(record.return_pct),
    excessReturnPct: asNullableNumber(record.excess_return_pct),
    win: Boolean(record.win),
    exitReason: asString(record.exit_reason),
    strategies,
  };
}

export function normalizePerformance(raw: unknown): PerformanceView {
  const record = asRecord(raw) as Partial<PerformancePayload>;
  const cold = asRecord(record.cold_start);
  const minDays = asNumber(cold.min_independent_signal_days, 30);
  const days = asNumber(cold.independent_signal_days);
  const overall = asRecord(record.overall);
  const fresh = asRecord(record.freshness);
  const rawTradingDays = fresh.trading_days_since_latest;
  const tradingDaysSinceLatest =
    rawTradingDays === null || rawTradingDays === undefined ? null : asNumber(rawTradingDays);

  const strategies = asArray<unknown>(record.strategies).map((item) => toStrategy(item, minDays));
  const alerts = asArray<unknown>(record.decay_alerts).map(toAlert);
  // 票级复盘明细：旧后端 payload 没有 recent_picks 键 → asArray 落 [] → 复盘表显示"暂无"，不白屏
  const recentPicks = asArray<unknown>(record.recent_picks).map(toRecentPick);

  const overallHitRate = record.overall ? asNumber(overall.hit_rate) : null;
  const canShowOverallHitRate =
    Boolean(overall.displayable) && days >= minDays && overallHitRate !== null;

  return {
    available: Boolean(record.available),
    reason: asString(record.reason),
    generatedAt: asString(record.generated_at),
    coldStart: Boolean(cold.is_cold_start) || days < minDays,
    independentSignalDays: days,
    minSignalDays: minDays,
    progress: minDays > 0 ? Math.min(1, days / minDays) : 0,
    overallHitRate,
    canShowOverallHitRate,
    strategies,
    decayAlerts: alerts,
    recentPicks,
    statusCounts: Object.entries(asRecord(record.status_counts) ?? {}).map(
      ([key, value]) => [key, asNumber(value)] as [string, number],
    ),
    notes: asArray<unknown>(record.notes).map((item) => asString(item)),
    latestSignalDate: asString(fresh.latest_signal_date),
    ledgerUpdatedAt: asString(fresh.ledger_updated_at),
    tradingDaysSinceLatest,
    stale: Boolean(fresh.stale),
    staleAfterTradingDays: asNumber(fresh.stale_after_trading_days, 5),
  };
}

/**
 * 停滞提示。
 *
 * 这一条的存在理由：**"正在积累"和"已经停止更新"必须能区分开**。
 * 流水线停了以后，冷启动进度会永远停在 27/30，用户却以为系统在正常攒样本。
 */
export function stalenessMessage(view: PerformanceView): string {
  if (!view.latestSignalDate) return "台账里还没有任何信号日。";
  const days = view.tradingDaysSinceLatest;
  if (days === null) return `最新信号日 ${view.latestSignalDate}。`;
  if (days <= 0) return `最新信号日 ${view.latestSignalDate}（今日已更新）。`;
  return `最新信号日 ${view.latestSignalDate}，之后已过 ${days} 个交易日未更新。`;
}

/** 顶部那句话：冷启动期只报进度，不报命中率。 */
export function performanceHeadline(view: PerformanceView): string {
  if (!view.available) return view.reason || "暂无绩效数据";
  if (view.coldStart) {
    return `冷启动期：已积累 ${view.independentSignalDays}/${view.minSignalDays} 个独立信号日`;
  }
  if (view.overallHitRate !== null) {
    return `整体命中率 ${(view.overallHitRate * 100).toFixed(1)}%（${view.independentSignalDays} 个独立信号日）`;
  }
  return "暂无可统计的观测";
}

/**
 * 收益百分比的展示格式：带正负号，缺失显示 "—"（区分"未记录"与"真的是 0"）。
 * 色值由调用方按 A 股"红涨绿跌"约定决定（正=红/ok，负=绿），这里只管数值。
 */
export function formatReturnPct(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

/** 了结原因的人话映射；未知值原样透传，不吞。 */
export function exitReasonLabel(reason: string): string {
  switch (reason.trim()) {
    case "horizon_close":
      return "到期了结";
    case "take_profit":
      return "止盈触发";
    case "stop_loss":
      return "止损触发";
    default:
      return reason || "未记录";
  }
}

/**
 * 票级复盘明细的汇总。
 *
 * 诚实边界（§5.4 / §8）：命中/未中是**逐笔事实计数**（不是策略级统计推断，不受样本
 * 门槛约束，可如实展示）；平均收益是 **PnL 派生，仅作观测**，绝不当主指标。
 * 全空时 total=0、hitRatio/avgReturnPct=null —— 渲染层据此整块隐藏，不编数字。
 */
export interface RecentPicksSummary {
  total: number;
  winCount: number;
  loseCount: number;
  /** 命中笔数占比（0~1）；total=0 时为 null（不编）。 */
  hitRatio: number | null;
  /** 已记录收益的算术平均（仅对非 null 求）；无任何已记录收益时 null（区分"未记录"与"真的是 0"）。 */
  avgReturnPct: number | null;
  /** 非 null 收益的条数（分母）。 */
  recordedReturns: number;
  posCount: number;
  negCount: number;
}

export function recentPicksSummary(picks: readonly RecentPickView[]): RecentPicksSummary {
  let winCount = 0;
  let loseCount = 0;
  let posCount = 0;
  let negCount = 0;
  let recordedReturns = 0;
  let sum = 0;
  for (const p of picks) {
    if (p.win) winCount += 1;
    else loseCount += 1;
    if (p.returnPct !== null) {
      recordedReturns += 1;
      sum += p.returnPct;
      if (p.returnPct > 0) posCount += 1;
      else if (p.returnPct < 0) negCount += 1;
    }
  }
  const total = picks.length;
  return {
    total,
    winCount,
    loseCount,
    hitRatio: total > 0 ? winCount / total : null,
    avgReturnPct: recordedReturns > 0 ? sum / recordedReturns : null,
    recordedReturns,
    posCount,
    negCount,
  };
}

export type RecentPicksSortKey = "signalDate" | "returnPct" | "excessReturnPct";
export type SortDir = "asc" | "desc";

/**
 * 票级明细排序：返回**新数组**（不 mutate 入参，调用方 state 可安全重排）。
 * 收益/超额列把 null（未记录）永远排到末尾，无论升/降序——"未记录"不是 0，
 * 混进数值序会误导；signalDate 是 YYYY-MM-DD 字符串，字典序即时间序。
 */
export function sortRecentPicks(
  picks: readonly RecentPickView[],
  key: RecentPicksSortKey,
  dir: SortDir,
): RecentPickView[] {
  const sign = dir === "asc" ? 1 : -1;
  const copy = [...picks];
  copy.sort((a, b) => {
    if (key === "signalDate") {
      const av = a.signalDate || "";
      const bv = b.signalDate || "";
      if (av === bv) return 0;
      return (av > bv ? 1 : -1) * sign;
    }
    const av = key === "returnPct" ? a.returnPct : a.excessReturnPct;
    const bv = key === "returnPct" ? b.returnPct : b.excessReturnPct;
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (av === bv) return 0;
    return (av > bv ? 1 : -1) * sign;
  });
  return copy;
}
