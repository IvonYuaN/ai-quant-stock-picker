// 影子账户的统计量描述：把「超额未知」与「无超额」分开，并且**不冒充策略判决**。
//
// 为什么必须分：`exc_mean` 是可空字段（`PaperArenaSleeve.exc_mean: number | null`）。
// `research/paper_ledger.py` 在「有结算笔数、但这些笔都没有 `excess_pct`」时会把
// `exc_mean` 留成 `None` —— 也就是 `n > 0` 但超额均值算不出来。此时原来的判定写
// `(row.exc_mean ?? 0) > 0`，落进最后一个分支渲染成「无超额」：
// **同一行的数字列显示「—」（未知），标签却说「无超额」（一个确定的否定结论）**。
//
// 🔴 2026-11-25 第二个坑（老大「我只需要有用的」）：这个标签原来叫「扛住多重检验」、
// 色调是**绿色 ok**。但账本按「检测到信号时已封死也照买」记账（`#574`），
// 封死那一下的收盘价本来就买不到 —— 那批超额里含语义前视，正是 P1/P6 被
// `paper_strategies` 判 `rejected` 的原因。真实数据：P1 q=0.00/exc=+2.03、
// P6 q=0.00/exc=+2.93，Web 上却是两个绿色「扛住多重检验」，
// 而推送闸门 `mode_card.is_pushable` 判 **0/6 过门**。
// 一个维度的统计显著性（账本口径）被当成了另一个维度的结论（策略可用）。
//
// ⇒ 这里只做**统计描述**：`survived` 保留 kind（下游计数不变），但 label 去掉
// 「通过/扛住」这类判决词，色调从绿降为中性，并附 detail 说清口径边界。
// 真正的过门判决在 `mode_card.is_pushable`，本模块不声称自己能判。
import type { PaperArenaSleeve } from "./api";

/** 判定标签的色调；与 `primitives.tsx` 的 `Tag` tone 取值一致。 */
export type SleeveVerdictTone = "ok" | "warn" | "neutral";

export type SleeveVerdict =
  /** 一笔都没结算：谈不上超额，也不能说 0 个通过。 */
  | { kind: "unopened"; label: string; tone: SleeveVerdictTone }
  /** 有结算笔数，但超额均值算不出来（源里这些笔没有基准超额）。 */
  | { kind: "unknown"; label: string; tone: SleeveVerdictTone; detail: string }
  /** q < 0.10 且超额为正（仅描述账本内统计，**不是**策略过门）。 */
  | { kind: "survived"; label: string; tone: SleeveVerdictTone; detail: string }
  /** 超额为正但没扛住多重检验。 */
  | { kind: "uncorrected"; label: string; tone: SleeveVerdictTone; detail: string }
  /** 超额确实 ≤ 0：这才是有证据的否定结论（也只是在账本口径内）。 */
  | { kind: "no-edge"; label: string; tone: SleeveVerdictTone };

/** q 值门槛：与页面上写的「q < 0.10」同一口径。 */
const Q_THRESHOLD = 0.1;

/** 口径边界：任何「超额为正」的标签都必须带着这句，别让它单独出现在页面上。 */
const SCOPE_DETAIL = "只描述账本内统计：打板按「检测时已封死也照买」记账，封死那一下的收盘价买不到；超额显著不等于策略过门，过门见推送闸门。";

function finite(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 单个策略的判定。**绝不把缺失的 `exc_mean` 当成 0**。 */
export function sleeveVerdict(row: Pick<PaperArenaSleeve, "n" | "exc_mean" | "q_exc">): SleeveVerdict {
  if (!(finite(row.n) ?? 0)) {
    return { kind: "unopened", label: "未开仓", tone: "neutral" };
  }
  const exc = finite(row.exc_mean);
  if (exc === null) {
    return {
      kind: "unknown",
      label: "超额未知",
      tone: "neutral",
      detail: "这个策略已有结算笔数，但这些笔没有基准超额记录，算不出超额均值；「无超额」会是另一个结论。",
    };
  }
  const q = finite(row.q_exc);
  if (q !== null && q < Q_THRESHOLD && exc > 0) {
    // 不再用绿色 ok：绿标会被读成「这个策略可以用」，而它恰恰可能是被否证的那批。
    return { kind: "survived", label: "超额显著", tone: "neutral", detail: SCOPE_DETAIL };
  }
  if (exc > 0) {
    // 「未校正通过」里的「通过」同样是判决词 —— 这里只是 q 没过线，不是「通过」。
    return { kind: "uncorrected", label: "超额为正·未过FDR", tone: "neutral", detail: SCOPE_DETAIL };
  }
  return { kind: "no-edge", label: "无超额", tone: "warn" };
}

/** 有结算笔数、但超额均值算不出来的策略数。用于在汇总卡片上显式披露。 */
export function unknownExcessCount(rows: readonly Pick<PaperArenaSleeve, "n" | "exc_mean">[]): number {
  return rows.filter((row) => (finite(row.n) ?? 0) > 0 && finite(row.exc_mean) === null).length;
}
