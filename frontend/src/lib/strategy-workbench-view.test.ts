import { strategyHitRateLabel } from "./strategy-workbench-view";
import type { PerformanceStrategy } from "./api";

const strategy: PerformanceStrategy = {
  name: "ma_pullback",
  independent_signal_days: 30,
  total_picks: 70,
  win_count: 42,
  hit_rate: 0.6,
  displayable: true,
  weight_base: 1,
  weight_confidence: 1,
  avg_return_pct: 1.2,
  max_drawdown: 0.1,
  sharpe_ratio: 0.8,
};

export const strategyWorkbenchViewContracts = {
  displaysMatureFreshStrategyRate: strategyHitRateLabel(strategy, false) === "60.0%",
  hidesRateForColdStart: strategyHitRateLabel({ ...strategy, displayable: false }, false) === "样本不足或数据停滞",
  hidesRateForStaleLedger: strategyHitRateLabel(strategy, true) === "样本不足或数据停滞",
};
