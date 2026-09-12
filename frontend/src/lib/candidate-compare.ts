// 候选并排对比（选股）。
//
// 多日对比看的是**时间维度**的变化，这里看的是**同一天里不同标的**的横向差异 ——
// 最后筛剩三五只时，光看卡片很难判断该先动哪只，并排摆开才看得出差别。
//
// 纯逻辑，UI 在 components/aqsp/sections/CandidateSection.tsx。
import type { CandidateRow } from "./daily-view";

export const MAX_COMPARE_SYMBOLS = 4;

export interface MetricDef {
  key: string;
  label: string;
  value: (row: CandidateRow) => string;
  /** 数字类指标右对齐并等宽 */
  numeric?: boolean;
}

export const CANDIDATE_METRICS: readonly MetricDef[] = [
  { key: "score", label: "评分", value: (row) => row.scoreText, numeric: true },
  { key: "status", label: "状态", value: (row) => row.status },
  { key: "evidence", label: "证据", value: (row) => row.evidence },
  { key: "ready", label: "可复核", value: (row) => (row.ready ? "✅ 可进入纸面复核" : "— 仅观察") },
  { key: "keyMetric", label: "关键指标", value: (row) => row.keyMetric || "—" },
  { key: "messages", label: "消息证据", value: (row) => `${row.messageCount} 条`, numeric: true },
  {
    key: "debate",
    label: "讨论轮数",
    value: (row) => (row.debateRounds == null ? "未讨论" : `${row.debateRounds} 轮`),
    numeric: true,
  },
  { key: "variants", label: "历史变体", value: (row) => `${row.variantCount} 组`, numeric: true },
  { key: "nextStep", label: "下一观察", value: (row) => row.nextStep || "—" },
];

/** 解析 URL 上的 `?cmp=a,b`：只认当天确实存在的候选，去重、守上限。 */
export function parseCompareSymbols(raw: string | null, available: readonly string[]): string[] {
  if (!raw) return [];
  const known = new Set(available);
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const symbol = part.trim();
    if (known.has(symbol) && !out.includes(symbol)) out.push(symbol);
    if (out.length >= MAX_COMPARE_SYMBOLS) break;
  }
  return out;
}

/** 满了就不动（由 UI 提示），不静默丢弃用户已选的标的。 */
export function toggleCompareSymbol(
  selected: readonly string[],
  symbol: string,
  max = MAX_COMPARE_SYMBOLS,
): string[] {
  if (selected.includes(symbol)) return selected.filter((item) => item !== symbol);
  if (selected.length >= max) return [...selected];
  return [...selected, symbol];
}

export interface CompareMatrix {
  metrics: readonly { key: string; label: string; numeric: boolean }[];
  columns: readonly { symbol: string; name: string }[];
  /** [指标 key][代码] = 展示值 */
  values: Readonly<Record<string, Readonly<Record<string, string>>>>;
}

/**
 * 生成对比矩阵。列的顺序**跟随传入的 symbols**，而不是候选列表顺序 ——
 * 用户点选的顺序就是他想看的顺序。
 */
export function buildCompareMatrix(
  rows: readonly CandidateRow[],
  symbols: readonly string[],
): CompareMatrix {
  const bySymbol = new Map(rows.map((row) => [row.symbol, row]));
  const columns = symbols
    .map((symbol) => bySymbol.get(symbol))
    .filter((row): row is CandidateRow => Boolean(row))
    .map((row) => ({ symbol: row.symbol, name: row.name }));

  const values: Record<string, Record<string, string>> = {};
  for (const metric of CANDIDATE_METRICS) {
    const perSymbol: Record<string, string> = {};
    for (const column of columns) {
      const row = bySymbol.get(column.symbol);
      perSymbol[column.symbol] = row ? metric.value(row) : "—";
    }
    values[metric.key] = perSymbol;
  }

  return {
    metrics: CANDIDATE_METRICS.map((metric) => ({
      key: metric.key,
      label: metric.label,
      numeric: Boolean(metric.numeric),
    })),
    columns,
    values,
  };
}
