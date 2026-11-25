// 「刷新失败、保留上次成功数据」提示的判定与文案。
//
// 为什么需要：`AqspWorkspaceProvider` 每 60s 轮询一次（`AQSP_LIVE_REFRESH_INTERVAL_MS`），
// 且**刻意不清空 data**（清空会让整页闪烁、刷新入口一起消失）。于是轮询失败时页面会继续
// 渲染上一次成功的快照 —— 这在数据上是合理的降级，但**必须让用户看出来**：
// 页面上那批候选/门禁/因子可能已经是几分钟甚至几小时前的，而不是"现在"。
//
// 原来只有「今日研究」页有这个提示条，其余模块页只处理 `error && !data`，
// 刷新失败时静默沿用旧值 —— 这正是「不得把失败渲染成 0 或沿用旧值」里的后一半。
//
// 本模块只做纯文本，不 import 任何组件：时点字符串由调用方用 `formatAqspTime` 先格式化好传进来。
export interface RefreshWarningInput {
  /** 上一次请求的错误；null/空串表示没失败。 */
  error: string | null | undefined;
  /** 手上是否还有可用快照。没有快照时该走 HeroState，不是提示条。 */
  hasData: boolean;
  /** 正在切换研究日期：此时内容区已切加载态，不该再叠一条「刷新失败」。 */
  switching: boolean;
}

/** 是否显示「刷新失败，保留上次成功数据」。 */
export function shouldShowRefreshWarning(input: RefreshWarningInput): boolean {
  if (!input.hasData) return false;
  if (input.switching) return false;
  return Boolean((input.error ?? "").trim());
}

/**
 * 提示条文案。
 *
 * 🔴 必须同时说三件事：① 这次刷新失败了；② 你看到的是**上次成功**的数据；
 * ③ 那份数据是什么时候的。只说 ① 用户会以为页面已经坏了；不说 ③ 就分不清「几分钟前」
 * 和「昨天」。`stamp` 传 `formatAqspTime()` 的结果（时点缺失时是 "—"），
 * 此时明说「时点未记录」，不拿当前时间冒充。
 */
export function refreshWarningText(
  error: string | null | undefined,
  stamp: string,
): { title: string; detail: string } {
  const reason = (error ?? "").trim() || "原因未记录";
  const when = stamp && stamp !== "—" ? stamp : "时点未记录";
  return {
    title: "刷新失败，保留上次成功数据",
    detail: `${reason} · 上次更新 ${when}`,
  };
}
