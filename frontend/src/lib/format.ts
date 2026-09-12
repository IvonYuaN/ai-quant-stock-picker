// 数值格式化与 A 股涨跌配色。
//
// 单独成文件的原因：这些规则被市场页、个股抽屉、持仓页、自选页共用，
// 散在各处会出现"同一个数字在不同页精度/颜色不一样"。
import type { Tone } from "./daily-view";

/** 金额（元）→ 亿，保留 1 位。 */
export function formatYi(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value / 1e8).toFixed(1)} 亿`;
}

/** 带符号百分比，保留 2 位。 */
export function formatSignedPct(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** 比率（0~1）→ 百分比，保留 1 位。 */
export function formatRate(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

/** 金额，千分位 + 2 位小数 + 元。 */
export function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "未提供";
  return `${value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} 元`;
}

/**
 * A 股口径：涨/盈利 = 红（up），跌/亏损 = 绿（down），0 为中性。
 * 注意与"好/坏"无关 —— 不要用 ok/danger 表达涨跌，那是西方口径。
 */
export function changeTone(value: number | null | undefined): Tone {
  if (value == null || !Number.isFinite(value) || value === 0) return "neutral";
  return value > 0 ? "warn" : "ok";
}

/** 涨跌方向对应的 CSS 类。写成字面量，避免 Tailwind 扫不到拼接类名。 */
export function changeClass(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value) || value === 0) return "";
  return value > 0 ? "aq-tone-up" : "aq-tone-down";
}
