// 「筛选器判读」工件读不到时，回退内嵌快照的**原因标注**。
//
// 后端 `GET /api/aqsp/filter-verdict` 只读 `pit_cache/filter_verdict.json`（runner 探针写出）。
// 页面拿不到工件时有三种完全不同的原因：
//   ① 请求还在飞 —— 还不知道有没有工件；
//   ② 请求失败（502/503/网络/端点未部署）—— 这是**读取链路**的问题，工件可能好好躺在磁盘上；
//   ③ 请求成功但 `available: false` —— 后端确认工件缺失/损坏，`reason` 里写着具体路径或原因。
//
// 原来页面一律标注「runner 工件未就绪」并把 catch 写成空函数（`.catch(() => {})`）：
// ② 会被误诊成 ③ —— 用户去查 runner 却查不出问题，真正坏的是后端/网络。误诊比不诊断更糟。
import type { FilterVerdict } from "./api";

export type FilterVerdictFallback =
  | { state: "loading"; label: string }
  | { state: "failed"; label: string }
  | { state: "unavailable"; label: string };

/**
 * 生成回退标注。`snapshotAsOf` 是内嵌快照的固化日期（形如 `2026-11-16`）。
 *
 * 🔴 三种 state 必须互不相同：调用方据此决定要不要额外提示「这行不是实时数据」。
 */
export function filterVerdictFallback(
  verdict: Pick<FilterVerdict, "available" | "reason"> | null,
  error: string,
  snapshotAsOf: string,
): FilterVerdictFallback {
  const prefix = `as-of ${snapshotAsOf} 固化快照`;
  if (error.trim()) {
    return {
      state: "failed",
      label: `${prefix}（读取 runner 工件失败：${error.trim()} —— 这是读取链路问题，不等于工件未生成）`,
    };
  }
  if (!verdict) {
    return { state: "loading", label: `${prefix}（正在读取 runner 工件）` };
  }
  if (verdict.available) {
    // 调用方不该在这里问「回退标注」，但真到了这一步也别撒谎说工件未就绪。
    return { state: "unavailable", label: `${prefix}（工件标为可用，但页面按快照渲染）` };
  }
  const reason = (verdict.reason ?? "").trim();
  return {
    state: "unavailable",
    label: reason
      ? `${prefix}（runner 工件未就绪：${reason}）`
      : `${prefix}（runner 工件未就绪，后端未说明原因）`,
  };
}
