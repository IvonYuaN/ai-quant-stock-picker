// 数值格式化与 A 股涨跌配色。
//
// 单独成文件的原因：这些规则被市场页、个股抽屉、持仓页、自选页共用，
// 散在各处会出现"同一个数字在不同页精度/颜色不一样"。
import type { Tone } from "./daily-view";

/**
 * 金额（元）→ **按量级自适应**的单位：亿 / 万 / 元。
 *
 * 🔴 为什么不固定成「亿」：`toFixed(1)` 会把非零的小额抹成「0.0 亿」，
 * 而这一列旁边还有红绿配色 —— 颜色说「流出」、数字说「0.0 亿（资金持平）」，
 * 同一行两个相反结论。实测（2026-11-25 东财 bkzj 554 个概念板块）有 19 个板块
 * 的主力净额在 0 < |x| < 1000 万元，例如「天津自贸区 -288.3 万」会渲染成
 * 「-0.0 亿」—— 负号 + 零，本身就是一句自相矛盾的话。
 *
 * 分档：`|v| ≥ 1 亿` → 亿（1 位）；`|v| ≥ 1 万` → 万（0 位）；否则 → 元。
 * **恰好 0 仍给「0.0 亿」**：0 是真零，说「0.0 亿」不失真（这一条保住了既有口径）。
 *
 * 名字里的 `Yi` 是历史名，实际单位由量级决定；表头写「成交额 / 净额 / 流通市值」
 * 不带单位后缀，靠这里自述。
 */
export function formatYi(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value === 0) return "0.0 亿";
  const magnitude = Math.abs(value);
  if (magnitude >= 1e8) return `${(value / 1e8).toFixed(1)} 亿`;
  if (magnitude >= 1e4) return `${(value / 1e4).toFixed(0)} 万`;
  return `${value.toFixed(0)} 元`;
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

/** 比率（0~1）→ 带符号百分比，保留 2 位。缺数返回「—」，绝不先兜 0 再格式化。 */
export function formatSignedRatio(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const pct = value * 100;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
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
 *
 * 两种真实形态都要认：ISO 是 `2026-10-19T10:58`（`as_of`），而成交额榜的
 * `updated` 是 `2026-10-19 15:30`（空格分隔）。只认 `T` 会让两块面板显示不一致。
 */
export function formatEvidenceTime(value: string | null | undefined): string {
  if (!value) return "";
  const match = value.match(/(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : value;
}

/**
 * 快照/产物的生成时刻 → `MM-DD HH:MM`（**北京时间**）。
 *
 * 🔴 必须固定 `timeZone: "Asia/Shanghai"`。不写时区会用**浏览器本地时区**，
 * 同一份快照在 UTC 机器上显示的时刻会差 8 小时 —— 而这类时点正是
 * 「这份数据是什么时候的」的唯一依据（刷新提示、快照生成、候选冻结、研究任务记录）。
 *
 * 这与后端「禁裸 `datetime.now()`、必须走 `now_shanghai()`」是同一条规则的前端对应项：
 * runner 就是 UTC，开发机也未必在东八区。同仓已有两处固定了时区
 * （`CandidateSection` 的冻结时刻、`HoldingsWatchlist` 的报价时刻），这里补齐共享的那一个。
 */
export function formatAqspTime(value: string): string {
  if (!value) return "—";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return value;
  return new Date(timestamp).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
