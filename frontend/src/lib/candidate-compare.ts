// 候选并排对比（选股）。
//
// 多日对比看的是**时间维度**的变化，这里看的是**同一天里不同标的**的横向差异 ——
// 最后筛剩三五只时，光看卡片很难判断该先动哪只，并排摆开才看得出差别。
//
// 纯逻辑，UI 在 components/aqsp/sections/CandidateSection.tsx。
import type { CandidateRow } from "./daily-view";
import type { CandidateWorkspace } from "./api";

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

type DirectionFact = { status: string; summary: string; source: string; asOf: string; industry: string };

function directionFact(value: unknown): DirectionFact {
  const fact = value && typeof value === "object" ? value as Record<string, unknown> : {};
  return {
    status: typeof fact.status === "string" ? fact.status : "unknown",
    summary: typeof fact.summary === "string" ? fact.summary : "",
    source: typeof fact.source === "string" ? fact.source : "",
    asOf: typeof fact.as_of === "string" ? fact.as_of : "",
    industry: typeof fact.industry === "string" ? fact.industry : "",
  };
}

function sourced(fact: DirectionFact, allowed: readonly string[]): boolean {
  return allowed.includes(fact.status) && Boolean(fact.source && fact.asOf && Number.isFinite(Date.parse(fact.asOf)));
}

function directionLabel(status: string): string {
  return ({ strong: "偏强", weak: "偏弱", neutral: "中性", rising: "上涨", falling: "下跌", flat: "持平" } as Record<string, string>)[status] ?? "未知";
}

export interface CandidateDecisionCard {
  symbol: string; name: string; support: string; counter: string; sector: string; debate: string; next: string;
  researchable: boolean;
}

export interface CandidateDecisionComparison {
  context: string; contextSource: string; conclusion: string; cards: CandidateDecisionCard[]; comparable: boolean;
}

/**
 * 同日研究取舍仅解释可核验证据和缺口，不按分数/模型票数给交易排序。
 * undefined=正在读取，null=读取失败；历史页绝不借用当前工作区。
 */
export function buildCandidateDecisionComparison(
  rows: readonly CandidateRow[], symbols: readonly string[],
  workspaces: Readonly<Record<string, CandidateWorkspace | null | undefined>>,
  researchDate: string, historical: boolean,
): CandidateDecisionComparison {
  const bySymbol = new Map(rows.map((row) => [row.symbol, row]));
  const chosen = symbols.map((symbol) => bySymbol.get(symbol)).filter((row): row is CandidateRow => Boolean(row));
  const current = (row: CandidateRow): CandidateWorkspace | null => {
    const workspace = workspaces[row.symbol];
    return !historical && workspace?.symbol === row.symbol && workspace.research_date === researchDate ? workspace : null;
  };
  const marketFacts = chosen.map((row) => directionFact(current(row)?.evidence.market));
  const first = marketFacts[0];
  const comparable = !historical && chosen.length >= 2 && marketFacts.every((fact) =>
    sourced(fact, ["strong", "weak", "neutral"]) && fact.status === first.status && fact.source === first.source
    && Date.parse(fact.asOf) === Date.parse(first.asOf));
  const cards = chosen.map((row): CandidateDecisionCard => {
    const workspace = current(row);
    const sector = directionFact(workspace?.evidence.sector);
    const pending = !historical && workspaces[row.symbol] === undefined;
    const sectorText = pending ? "板块证据读取中" : sourced(sector, ["rising", "falling", "flat"])
      ? `${sector.industry || "所属板块"}${directionLabel(sector.status)} · ${sector.source} · ${sector.asOf}`
      : "板块方向或来源待核验";
    const blocker = workspace?.issues[0] || workspace?.new_entry_block_reason || row.coverageNote ||
      (!row.ready ? "原研究材料未齐" : "");
    const counter = historical ? "历史反证与缺口请查看当日研究记录"
      : pending ? "当前个股证据读取中"
      : !workspace ? "当前个股证据未取得；不能凭旧评分排先后"
      : blocker || (sector.status === "falling" ? `${sector.industry || "所属板块"}当日下跌，需核对是否影响原理由`
        : "尚未取得可替代逐票复核的反证结论；须核对失效条件");
    const discussion = workspace?.discussion;
    const debate = historical ? row.debateConclusion ? `当日讨论：${row.debateConclusion}` : "当日讨论未记录"
      : pending ? "AI 讨论记录读取中"
      : discussion?.status === "complete" && discussion.evidence_version === workspace?.evidence_version
      && discussion.resource_version === workspace?.version && discussion.research_date === researchDate
      && discussion.id && Number.isFinite(Date.parse(discussion.generated_at)) && discussion.summary
      ? discussion.summary.disagreements[0] || "本轮未记录明确分歧，仍需核对缺证"
      : discussion?.status === "partial" ? "AI 仅完成部分讨论，不能采用其条件" : "当前 AI 讨论尚未完整可用";
    const followup = workspace?.watch?.status === "active"
      ? `后台跟进：${workspace.observation?.title || "等待首次检查"}`
      : row.nextStep ? `原研究下一观察：${row.nextStep}` : "进入个股页核对讨论和等待条件";
    return {
      symbol: row.symbol, name: row.name,
      support: row.reasons[0] ? `原研究：${row.reasons[0]}` : "原研究支持理由尚未记录",
      counter, sector: sectorText, debate, next: followup,
      researchable: Boolean(comparable && workspace?.available && row.ready && !workspace.entry_blocked
        && !workspace.new_entry_blocked && !workspace.quote_issue),
    };
  });
  if (historical) return { context: `${researchDate} 历史候选，仅比较当时记录`, contextSource: "不借用当前行情", conclusion: "历史对比不能作为今天的买卖依据。", cards, comparable: false };
  if (!comparable) return { context: "当前大盘背景未能确认为同源同刻", contextSource: "部分候选的市场依据尚未取得、日期不符或来源不同", conclusion: "先补齐同日市场和逐票证据；暂不按评分给候选排先后。", cards, comparable: false };
  const ready = cards.filter((card) => card.researchable);
  const conclusion = ready.length === 1
    ? `${ready[0].name}可先核对观察条件；其余候选先处理下方反证或缺口。这不是买入建议。`
    : ready.length > 1 ? "多只候选均可继续研究；现有证据不能只凭评分或 AI 票数排出买入先后。"
      : "这些候选目前都还缺少可继续采用条件的依据；先按下方缺口逐票复核。";
  return { context: `共同大盘背景：${directionLabel(first.status)} · ${first.summary || "方向来源已核验"}`,
    contextSource: `${first.source} · ${first.asOf}`, conclusion, cards, comparable: true };
}
