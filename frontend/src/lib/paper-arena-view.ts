// 影子账户的策略判定：把「超额未知」与「无超额」分开。
//
// 为什么必须分：`exc_mean` 是可空字段（`PaperArenaSleeve.exc_mean: number | null`）。
// `research/paper_ledger.py` 在「有结算笔数、但这些笔都没有 `excess_pct`」时会把
// `exc_mean` 留成 `None` —— 也就是 `n > 0` 但超额均值算不出来。此时原来的判定写
// `(row.exc_mean ?? 0) > 0`，落进最后一个分支渲染成「无超额」：
// **同一行的数字列显示「—」（未知），标签却说「无超额」（一个确定的否定结论）**。
// 未知不等于零，更不等于「没有超额」。这里把五种状态显式建模，页面只消费枚举。
import type { PaperArenaSleeve } from "./api";

/** 判定标签的色调；与 `primitives.tsx` 的 `Tag` tone 取值一致。 */
export type SleeveVerdictTone = "ok" | "warn" | "neutral";

export type SleeveVerdict =
  /** 一笔都没结算：谈不上超额，也不能说 0 个通过。 */
  | { kind: "unopened"; label: string; tone: SleeveVerdictTone }
  /** 有结算笔数，但超额均值算不出来（源里这些笔没有基准超额）。 */
  | { kind: "unknown"; label: string; tone: SleeveVerdictTone; detail: string }
  /** q < 0.10 且超额为正。 */
  | { kind: "survived"; label: string; tone: SleeveVerdictTone }
  /** 超额为正但没扛住多重检验。 */
  | { kind: "uncorrected"; label: string; tone: SleeveVerdictTone }
  /** 超额确实 ≤ 0：这才是有证据的否定结论。 */
  | { kind: "no-edge"; label: string; tone: SleeveVerdictTone };

/** q 值门槛：与页面上写的「q < 0.10 才算扛住多重检验」同一口径。 */
const Q_THRESHOLD = 0.1;

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
    return { kind: "survived", label: "扛住多重检验", tone: "ok" };
  }
  if (exc > 0) return { kind: "uncorrected", label: "未校正通过", tone: "neutral" };
  return { kind: "no-edge", label: "无超额", tone: "warn" };
}

/** 有结算笔数、但超额均值算不出来的策略数。用于在汇总卡片上显式披露。 */
export function unknownExcessCount(rows: readonly Pick<PaperArenaSleeve, "n" | "exc_mean">[]): number {
  return rows.filter((row) => (finite(row.n) ?? 0) > 0 && finite(row.exc_mean) === null).length;
}
