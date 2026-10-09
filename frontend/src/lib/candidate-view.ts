// 候选筛选与排序（选股的核心动作）。
//
// 展示模型 `daily-view` 只负责"算出有哪些候选、各自是什么状态"；
// 用户**怎么挑**是另一件事，放这里，纯函数、可断言。
import type { CandidateRow } from "./daily-view";

export type CandidateSortKey = "score" | "ready" | "evidence";

export interface CandidateFilterOptions {
  sort: CandidateSortKey;
  /** 只看证据链已闭环（可进入纸面复核）的候选 */
  onlyReady: boolean;
  /** 按策略名过滤；null = 不过滤 */
  strategy: string | null;
  /**
   * 额外排除条件（返回 true 表示排除）。例如"隐藏已持有的"。
   * 做成谓词而不是具体开关，是为了让**隐藏数量始终只有一个来源** ——
   * 若各算各的，用户看到的"已隐藏 N 只"就会漏掉其中一部分。
   */
  exclude?: (row: CandidateRow) => boolean;
}

export interface CandidateSelection {
  rows: readonly CandidateRow[];
  total: number;
  /** 被筛掉了多少 —— 必须让用户知道"还有 N 只被隐藏"，否则会以为只有这些候选 */
  hidden: number;
}

/** 把 `strategyA · strategyB` 这样的串拆成可选项（去重、去空）。 */
export function strategyOptions(rows: readonly CandidateRow[]): string[] {
  const seen = new Set<string>();
  for (const row of rows) {
    for (const part of (row.strategies ?? "").split(/[·、,，]/)) {
      const name = part.trim();
      if (name) seen.add(name);
    }
  }
  return [...seen].sort((a, b) => a.localeCompare(b, "zh-CN"));
}

/** 每个策略各挂几只候选 —— 「分策略选股」的一等公民入口（issue #317）。 */
export interface StrategyCount {
  name: string;
  /** 挂了该策略的候选数（一只候选可同时挂多个策略，各项之和会大于候选数）。 */
  count: number;
}

/**
 * 策略 × 计数，按候选数降序（点选率最高的策略排前面），同数按 zh-CN 字典序。
 * 与 `strategyOptions` 共用同一套拆分规则，保证「chip 显示的策略」与「过滤命中的策略」必然一致。
 */
export function strategyCounts(rows: readonly CandidateRow[]): StrategyCount[] {
  const counts = new Map<string, number>();
  for (const row of rows) {
    for (const part of (row.strategies ?? "").split(/[·、,，]/)) {
      const name = part.trim();
      if (name) counts.set(name, (counts.get(name) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-CN"));
}

function matchStrategy(row: CandidateRow, strategy: string | null): boolean {
  if (!strategy) return true;
  return strategyOptions([row]).includes(strategy);
}

export function selectCandidates(
  rows: readonly CandidateRow[],
  options: CandidateFilterOptions,
): CandidateSelection {
  const filtered = rows.filter(
    (row) =>
      (!options.onlyReady || row.ready) &&
      matchStrategy(row, options.strategy) &&
      !(options.exclude?.(row) ?? false),
  );

  const sorted = [...filtered].sort((a, b) => {
    switch (options.sort) {
      case "ready":
        // 可复核优先，其次评分降序 —— 选股时"能不能下手"比分数高低更重要
        if (a.ready !== b.ready) return a.ready ? -1 : 1;
        return b.score - a.score;
      case "evidence":
        if (b.messageCount !== a.messageCount) return b.messageCount - a.messageCount;
        return b.score - a.score;
      case "score":
      default:
        return b.score - a.score;
    }
  });

  return { rows: sorted, total: rows.length, hidden: rows.length - sorted.length };
}
