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

/**
 * 单段取数的两态结果（第三态"正在读"由调用方用 `null` 表示）。
 *
 * 为什么单独建模：`Promise.allSettled` 之后如果把失败段落回 `null`，页面分不清
 * 「还在读」和「已经读失败」，就会永远转圈且不给原因。三态必须是三个可区分的值。
 */
export type SectionResult<T> =
  | { state: "ready"; rows: readonly T[] }
  | { state: "failed"; message: string };

/**
 * 把 `allSettled` 的单个结果收敛成 `SectionResult`。
 *
 * 🔴 不要写成「全部失败才报错」：一段失败、另一段成功时，失败段会停在 null，
 * 页面渲染成「正在读取…」——用户以为还在读，其实早就失败了，且永远等不到。
 * 每段各自结算，失败段必须立刻给出原因，并明确「空表 ≠ 今天没数据」。
 *
 * `fulfilled` 但值为 null/undefined 时归为「读到了空表」（源正常返回但没内容），
 * 这与「读失败」是两件事，不能混。
 */
export function settleSection<T>(
  result: PromiseSettledResult<readonly T[] | null | undefined>,
  label: string,
): SectionResult<T> {
  if (result.status === "fulfilled") return { state: "ready", rows: result.value ?? [] };
  const reason = result.reason instanceof Error ? result.reason.message : String(result.reason ?? "");
  return {
    state: "failed",
    message: `${label}读取失败：${reason || "未知原因"}。这一块是空的，不代表今天没有数据。`,
  };
}

/**
 * 单段取数的三态判定：**失败优先于「正在读」**。
 *
 * 约定同 `SectionResult`：`null` 表示还在读。但请求失败后调用方通常不会把 value
 * 置成非 null，于是页面若仍用 `!value` 判加载态，就会**同时**渲染
 * 「读取失败」和「正在读取…」，而且永远转圈 —— 用户以为还在读，其实早就失败了。
 *
 * `MonitorPage` 原来正是这个写法（`{error ? …}{!followups ? <LoadingState/> : null}`）。
 * 把优先级写进一个函数，页面就不必各自记得先判 error。
 */
export function readState<T>(value: T | null, error: string): "loading" | "failed" | "ready" {
  if (error) return "failed";
  return value === null ? "loading" : "ready";
}
