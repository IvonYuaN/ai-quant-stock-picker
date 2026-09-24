// 事件日历（解禁预警 / 龙虎榜佐证）展示层纯逻辑。
//
// 后端 `GET /api/events` 的数据契约：
//   data.upcoming_unlocks[].severity ∈ high | medium | low | negligible
//   data.recent_longhubang[].net_amount  —— null 表示「净额未披露」（NaN 收敛）
// 徽标/方向口径与全站一致：severity 高=红、中=橙、低=灰；
// 净买入=红（aq-tone-up）、净卖出=绿（aq-tone-down），同 WatchlistPage 红涨绿跌。
//
// 类名一律写成**显式字面量**，不要用 `aq-badge-${x}` 拼接 —— Tailwind 扫不到。
import type { UpcomingUnlockEvent } from "./api";

/** severity → 中文标签。未知档位归「微」，不编造语义。 */
export function severityLabel(severity: string): string {
  if (severity === "high") return "高";
  if (severity === "medium") return "中";
  if (severity === "low") return "低";
  return "微";
}

/** severity → 徽标配色类（配合 <Badge className>）。 */
export function severityBadgeClass(severity: string): string {
  if (severity === "high") return "aq-badge-up";
  if (severity === "medium") return "aq-badge-warn";
  return "aq-badge-neutral";
}

/** 解禁按 days_until 升序（最近的排前面）；返回新数组，不改输入。 */
export function sortUnlocksByDaysUntil(
  events: readonly UpcomingUnlockEvent[],
): UpcomingUnlockEvent[] {
  return [...events].sort((a, b) => a.days_until - b.days_until);
}

export interface NetAmountView {
  text: string;
  /** 空串 = 中性（不上色）。 */
  cls: string;
}

/** 龙虎榜净额 → 方向文案 + 红涨绿跌配色；null = 未披露，绝不编成「净卖出 0」。 */
export function netAmountView(netAmount: number | null): NetAmountView {
  if (netAmount == null || !Number.isFinite(netAmount)) {
    return { text: "净额未披露", cls: "" };
  }
  if (netAmount > 0) {
    return { text: `净买入 ${netAmount.toFixed(0)} 万元`, cls: "aq-tone-up" };
  }
  if (netAmount < 0) {
    return { text: `净卖出 ${Math.abs(netAmount).toFixed(0)} 万元`, cls: "aq-tone-down" };
  }
  return { text: "净额 0 万元", cls: "" };
}

/** 解禁比例（0~1 小数）→ 展示文案；null = 未披露。 */
export function unlockRatioText(ratio: number | null): string {
  if (ratio == null || !Number.isFinite(ratio)) return "占比未披露";
  return `占总股本 ${(ratio * 100).toFixed(2)}%`;
}
