// 侧栏「AQSP 数据状态」指示点的四态判定。
//
// 为什么单独建模：这个点是全站唯一一处**全局**健康指示——用户想知道「系统现在好不好」时
// 最先看它。原来只有三态 `loading / data / else`，于是：
//   - 后端连不上、快照读失败（error 且无 data）⇒ 显示「暂无数据」+ 灰点
//   - 今天确实没有产物（error 为空、data 为空）⇒ 也显示「暂无数据」+ 灰点
// 两者对用户的含义完全不同：前者是「去查后端」，后者是「今天没数据，正常」。
// 把故障说成「暂无数据」，等于把唯一一个能提示故障的地方也变成了噪音。
import type { Tone } from "./daily-view";

export type DataStatusTone = "loading" | "ready" | "error" | "empty";

export interface DataStatus {
  tone: DataStatusTone;
  label: string;
  /** 指示点 CSS 类；集中在这里，避免页面里散落三元嵌套。 */
  dotClass: string;
}

export interface DataStatusInput {
  loading: boolean;
  hasData: boolean;
  error: string | null | undefined;
}

/**
 * 判定侧栏数据状态。
 *
 * 优先级：正在读 > 失败 > 有数据 > 空。
 * 🔴 `error` 且 `hasData` 时报「刷新失败」而不是「数据已接入」：手上确实是上次成功的数据，
 * 但此刻读不到新的，用户点刷新才有意义。
 */
export function sidebarDataStatus(input: DataStatusInput): DataStatus {
  const failed = Boolean((input.error ?? "").trim());
  if (input.loading) return { tone: "loading", label: "读取中", dotClass: "aq-dot aq-dot-loading" };
  if (failed) {
    return {
      tone: "error",
      label: input.hasData ? "刷新失败" : "读取失败",
      dotClass: "aq-dot aq-dot-error",
    };
  }
  if (input.hasData) return { tone: "ready", label: "数据已接入", dotClass: "aq-dot aq-dot-ready" };
  return { tone: "empty", label: "暂无数据", dotClass: "aq-dot aq-dot-empty" };
}

/** 状态点是否属于「有问题」的一类（供告警色/aria 使用）。 */
export function dataStatusIsProblem(status: DataStatus): boolean {
  return status.tone === "error";
}

/** 与 `Tone` 的映射，给需要语气的场景用。 */
export function dataStatusTone(status: DataStatus): Tone {
  switch (status.tone) {
    case "ready":
      return "ok";
    case "error":
      return "warn";
    default:
      return "neutral";
  }
}
