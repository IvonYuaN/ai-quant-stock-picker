// 市场风险快照（`GET /api/market-risk`）在页面上的三态判定。
//
// 为什么需要单独建模：后端有三种完全不同的「没有数」——
//   ① 请求还在飞（没结论）
//   ② 请求失败（502/503/网络）—— 我们不知道市场面如何
//   ③ 请求成功但后端说 `available: false` —— 后端**知道**为什么（样本不足 / 库里没交易日），
//      并把原因放在 `reason` 里
// 三者对用户的含义完全不同。原来页面把 ①②③ 都渲染成「暂无数据」，
// 等于把「后端算不出来，因为只有 40 个交易日」这条可行动的信息丢掉了。
import type { MarketRisk } from "./api";

export type MarketRiskStatus =
  /** 请求还没回来。 */
  | { state: "loading" }
  /** 请求失败；`text` 是失败原因。 */
  | { state: "failed"; text: string }
  /** 后端明确说不可用；`text` 优先用后端给的原因，后端没给就明说「没说明原因」。 */
  | { state: "unavailable"; text: string }
  /** 有数据可展示。 */
  | { state: "ready" };

/**
 * 判定市场风险区块该显示什么。
 *
 * 🔴 `available: false` 且 `reason` 为空时，不要写「暂无数据」——那会让用户以为
 * 「今天没有市场面这回事」。要写「后端未说明原因」，把不确定性留给系统而不是用户。
 */
export function marketRiskStatus(
  snapshot: Pick<MarketRisk, "available" | "reason"> | null,
  error: string,
): MarketRiskStatus {
  if (error.trim()) return { state: "failed", text: error };
  if (!snapshot) return { state: "loading" };
  if (snapshot.available) return { state: "ready" };
  const reason = (snapshot.reason ?? "").trim();
  return { state: "unavailable", text: reason || "后端返回不可用，但没有说明原因" };
}

/** 非 ready 状态要显示的一行文案；ready 时返回空串（调用方渲染真实指标）。 */
export function marketRiskStatusText(status: MarketRiskStatus): string {
  switch (status.state) {
    case "loading":
      return "正在读取当前市场面…";
    case "failed":
      return status.text;
    case "unavailable":
      return status.text;
    default:
      return "";
  }
}
