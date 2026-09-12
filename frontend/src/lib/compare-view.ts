// 多日对比（复盘）。
//
// 一天天地看只能看到"当天发生了什么"，看不出**趋势与持续性**。这里回答三个复盘真正关心的问题：
//   1. 门禁这几天有没有变化（是不是一直卡着不放行）
//   2. 候选数量与可复核数在怎么动
//   3. 哪些候选**连续多天在榜**（持续被选中，最值得跟踪），哪些只是昙花一现
//
// 纯逻辑：日期解析 + 汇总。数据获取在 components/aqsp/useComparison.ts。
import type { DailyView, Tone } from "./daily-view";

export const MAX_COMPARE = 5;

/**
 * 解析 URL 上的 `?dates=a,b,c`。
 *
 * 只接受**确实存在**于归档索引里的日期，且最多 MAX_COMPARE 个 ——
 * 一是防止拼出一堆不存在的日期把后端打一遍，二是对比超过 5 列就没法读了。
 */
export function parseCompareDates(raw: string | null, available: readonly string[]): string[] {
  if (!raw) return [];
  const known = new Set(available);
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const date = part.trim();
    if (known.has(date) && !out.includes(date)) out.push(date);
    if (out.length >= MAX_COMPARE) break;
  }
  return out;
}

/** 在现有选择里加/去一个日期，保持"最新在前"的顺序，并守住上限。 */
export function toggleCompareDate(
  selected: readonly string[],
  date: string,
  max = MAX_COMPARE,
): string[] {
  if (selected.includes(date)) return selected.filter((item) => item !== date);
  if (selected.length >= max) return [...selected]; // 满了就先不动，由 UI 给出提示
  return [date, ...selected];
}

export interface CompareRow {
  date: string;
  gateLabel: string;
  gateTone: Tone;
  candidateCount: number;
  readyCount: number;
  conclusion: string;
  /** 当天评分最高的 3 只 */
  topSymbols: readonly string[];
}

export interface CompareSummary {
  rows: readonly CompareRow[];
  /** 所选日期里**每一天**都在榜的候选 —— 持续被选中，最值得跟踪 */
  persistentSymbols: readonly string[];
  /** 只出现过一次的候选数量 —— 昙花一现 */
  oneOffCount: number;
  totalDates: number;
  /** 覆盖到的全部候选（去重） */
  allSymbols: readonly string[];
}

function toRow(view: DailyView): CompareRow {
  const sorted = [...view.candidates].sort((a, b) => b.score - a.score);
  return {
    date: view.date,
    gateLabel: view.gate.label,
    gateTone: view.gate.tone,
    candidateCount: view.candidates.length,
    readyCount: view.candidates.filter((row) => row.ready).length,
    conclusion: view.conclusion,
    topSymbols: sorted.slice(0, 3).map((row) => `${row.name}(${row.scoreText})`),
  };
}

export function buildCompareSummary(views: readonly DailyView[]): CompareSummary {
  // 归档按日回看：日期新的在前
  const ordered = [...views].sort((a, b) => b.date.localeCompare(a.date));
  const rows = ordered.map(toRow);

  const counts = new Map<string, number>();
  for (const view of ordered) {
    for (const candidate of view.candidates) {
      counts.set(candidate.symbol, (counts.get(candidate.symbol) ?? 0) + 1);
    }
  }
  const all = [...counts.keys()];
  const persistent = all.filter((symbol) => counts.get(symbol) === ordered.length);
  const oneOff = all.filter((symbol) => counts.get(symbol) === 1).length;

  return {
    rows,
    persistentSymbols: persistent,
    oneOffCount: oneOff,
    totalDates: ordered.length,
    allSymbols: all,
  };
}
