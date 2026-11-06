import type { PerformanceStrategy } from "@/lib/api";

export function strategyHitRateLabel(strategy: PerformanceStrategy, stale: boolean): string {
  if (!strategy.displayable || stale) return "样本不足或数据停滞";
  return `${(strategy.hit_rate * 100).toFixed(1)}%`;
}
