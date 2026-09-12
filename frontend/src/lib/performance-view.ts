// 选股绩效的展示模型。
//
// 这里最重要的一条不是"怎么画"，而是**什么时候不许画**：
// 宪法 §5.4 规定冷启动期（独立信号日 < 30）**不展示胜率**，只显示积累进度。
// 原因写在架构文档里：样本不足时展示空表或数字会"误导用户以为系统故障或产生虚假信心"。
//
// 后端已经给了 displayable，但前端仍要独立守一道 ——
// 显示层的诚实不能只依赖上游，否则后端一改口径这里就会悄悄违规。
import type { PerformanceDecayAlert, PerformancePayload } from "./api";
import { asArray, asNumber, asRecord, asString } from "./safe";

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
  statusCounts: readonly [string, number][];
  notes: readonly string[];
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

export function normalizePerformance(raw: unknown): PerformanceView {
  const record = asRecord(raw) as Partial<PerformancePayload>;
  const cold = asRecord(record.cold_start);
  const minDays = asNumber(cold.min_independent_signal_days, 30);
  const days = asNumber(cold.independent_signal_days);
  const overall = asRecord(record.overall);

  const strategies = asArray<unknown>(record.strategies).map((item) => toStrategy(item, minDays));
  const alerts = asArray<unknown>(record.decay_alerts).map(toAlert);

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
    statusCounts: Object.entries(asRecord(record.status_counts) ?? {}).map(
      ([key, value]) => [key, asNumber(value)] as [string, number],
    ),
    notes: asArray<unknown>(record.notes).map((item) => asString(item)),
  };
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
