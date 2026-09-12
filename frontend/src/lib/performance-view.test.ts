// 绩效展示模型的契约断言（由 npm test 真正执行）。
//
// 最关键的一条：宪法 §5.4 —— 冷启动期（独立信号日 < 30）**不得展示胜率**。
// 这条如果在前端失守，用户会看到一个"看起来很准/很不准"的数字，
// 而它其实没有统计意义。所以这里既验证后端 displayable 的传递，也验证前端的独立门控。
import {
  normalizePerformance,
  performanceHeadline,
  severityTone,
} from "./performance-view";

const warm = {
  available: true,
  generated_at: "2026-09-12 12:00",
  cold_start: {
    is_cold_start: false,
    min_independent_signal_days: 30,
    independent_signal_days: 42,
    max_strategy_signal_days: 40,
  },
  overall: { observations: 42, win_count: 24, hit_rate: 0.5714, displayable: true },
  strategies: [
    {
      name: "rps_momentum",
      independent_signal_days: 40,
      total_picks: 120,
      win_count: 55,
      hit_rate: 0.458,
      displayable: true,
      weight_base: 1.12,
      weight_confidence: 1,
      avg_return_pct: 0.83,
      max_drawdown: 0.04,
      sharpe_ratio: 0.2,
    },
  ],
  decay_alerts: [
    {
      strategy: "ma_pullback",
      lookback_days: 7,
      decay_days: 3,
      recent_win_rate: 0.211,
      recent_avg_return_pct: -1.2,
      severity: "critical",
      recommendation: "建议将 ma_pullback 权重降至最低",
    },
  ],
  status_counts: { validated: 248, pending: 32 },
  notes: ["命中率为主指标"],
};

const cold = {
  available: true,
  generated_at: "2026-09-12 12:00",
  cold_start: {
    is_cold_start: true,
    min_independent_signal_days: 30,
    independent_signal_days: 27,
    max_strategy_signal_days: 21,
  },
  // 后端即使误给了 displayable，前端也必须拦住
  overall: { observations: 27, win_count: 14, hit_rate: 0.5185, displayable: true },
  strategies: [
    {
      name: "volume_breakout",
      independent_signal_days: 21,
      total_picks: 80,
      win_count: 38,
      hit_rate: 0.475,
      displayable: false,
      weight_base: 1,
      weight_confidence: 0.7,
      avg_return_pct: 0.92,
    },
  ],
  decay_alerts: [],
  status_counts: { validated: 248 },
  notes: [],
};

const warmView = normalizePerformance(warm);
const coldView = normalizePerformance(cold);

export const performanceViewContract = {
  /* ---- 正常（样本充足）---- */
  warmIsAvailable: warmView.available,
  warmNotColdStart: !warmView.coldStart,
  warmShowsHitRate: warmView.canShowOverallHitRate,
  warmHitRateValue: warmView.overallHitRate === 0.5714,
  warmStrategyShowsHitRate: warmView.strategies[0].canShowHitRate,
  warmStrategyName: warmView.strategies[0].name === "rps_momentum",
  warmProgressIsOne: warmView.progress === 1,
  warmHeadlineMentionsHitRate: performanceHeadline(warmView).includes("57.1%"),

  /* ---- 冷启动（§5.4）：不许展示胜率 ---- */
  coldIsColdStart: coldView.coldStart,
  // 后端 displayable=true 也不行，前端独立门控必须拦住
  coldHidesHitRate: !coldView.canShowOverallHitRate,
  coldStrategyHidesHitRate: !coldView.strategies[0].canShowHitRate,
  coldStillKeepsNumber: coldView.overallHitRate === 0.5185,
  coldProgressPartial: coldView.progress === 27 / 30,
  coldHeadlineShowsProgress: performanceHeadline(coldView).includes("27/30"),
  coldHeadlineNoHitRate: !performanceHeadline(coldView).includes("51.9"),

  /* ---- 衰减告警 ---- */
  alertStrategyName: warmView.decayAlerts[0].strategy === "ma_pullback",
  criticalIsWarnTone: severityTone("critical") === "warn",
  warningIsWarnTone: severityTone("warning") === "warn",
  unknownIsNeutral: severityTone("something") === "neutral",
  emptySeverityIsNeutral: severityTone("") === "neutral",

  /* ---- 边界 ---- */
  nullSafe: !normalizePerformance(null).available,
  stringSafe: normalizePerformance("boom").strategies.length === 0,
  emptyObjectStatusCounts: normalizePerformance({}).statusCounts.length === 0,
  missingOverallNoHitRate: normalizePerformance({ available: true }).overallHitRate === null,
  unavailableShowsReason:
    performanceHeadline(normalizePerformance({ available: false, reason: "未找到台账文件" })).includes("未找到台账文件"),
};
