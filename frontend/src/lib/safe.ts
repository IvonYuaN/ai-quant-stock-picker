// 外部数据的安全归一化。
//
// 为什么需要：服务端字段来自多源聚合（行情 / 消息 / 讨论 / 持仓），任何一处缺失都会让页面
// 在 `x.length`、`x.toFixed()` 上直接抛错 —— 表现是整页白屏，而且因为被 ErrorBoundary 接住，
// 控制台之外很难看出原因。
//
// 原则：**展示层不信任任何外部结构**，但也不静默篡改语义 —— 缺失就落到"空/未记录"，
// 由页面如实呈现，而不是编一个 0 出来。

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** 数字兜底：非有限数一律落到 fallback（默认 0），避免 NaN 渗透到界面。 */
export function asNumber(value: unknown, fallback = 0): number {
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : fallback;
}

/** 可空数字：无法解析时返回 null（用于"未提供"与"真的是 0"要区分的场景）。 */
export function asNullableNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const num = typeof value === "number" ? value : Number(value);
  return Number.isFinite(num) ? num : null;
}
