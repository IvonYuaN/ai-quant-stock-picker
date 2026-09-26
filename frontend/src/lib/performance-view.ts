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
