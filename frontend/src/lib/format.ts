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

/**
 * 证据链（#317 W3）：后端给的**数据时间** → `MM-DD HH:MM`。
 *
 * 这是「数据是什么时候的」，不是「页面什么时候刷的」。三点约定：
 * - 空值返回空串，由调用方决定怎么措辞（榜单后缀直接省略，快讯区写「时间未记录」）；
 * - 跨天/跨节假日必须带日期 —— 只显示 `HH:MM` 会让隔夜外围看起来像刚刚更新；
 * - 格式认不出就**原样返回**，不猜、不补零（宁可难看，不可失真）。
 */
export function formatEvidenceTime(value: string | null | undefined): string {
  if (!value) return "";
  const match = value.match(/(\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : value;
}
