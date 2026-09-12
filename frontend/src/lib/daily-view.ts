// 每日研究的「展示模型」层。
//
// 为什么单独有这一层：快照字段是**服务端契约**，如果让每个页面各自去 if/fallback，
// 同一件事就会在不同页显示成不同结论 —— 例如门禁在复盘页说「仅供观察」、在推荐页却说
// 「已放行」；候选名在一处走了去重、在另一处没走，于是又冒出 "300750 300750"。
//
// 这里把原始 AqspSnapshot **只解析一次**，产出页面直接可渲染的结构；
// 判断逻辑因此只有一份，页面退化成纯渲染。新增模块时只要改这里，不会漏掉某个入口。
import type {
  AqspAgentResult,
  AqspCandidate,
  AqspCrossMarket,
  AqspMarketContext,
  AqspMessage,
  AqspPhase,
  AqspRecommendationGate,
  AqspResearchChain,
  AqspSnapshot,
  AqspUniverse,
  AqspVariant,
} from "@/types/aqsp";
import {
  candidateName,
  candidateResearchReady,
  historicalVariantCount,
  latestVariantDate,
  messagesForCandidate,
  sourceCoverageLines,
} from "./candidate-chain";
import {
  gatePresentation,
  isCurrentEmptyObservation,
  latestReviewDate,
  messageSourceUrl,
  snapshotConclusion,
} from "./research-view";
import { asArray, asRecord, asString } from "./safe";

/** 统一的语气档：ok=可推进 / warn=需注意 / neutral=信息缺失。 */
export type Tone = "ok" | "warn" | "neutral";

export interface ToneView {
  tone: Tone;
  /** 一句话结论，用于标题位。 */
  label: string;
  /** 补充说明，用于正文位。 */
  detail: string;
}

export interface EmptyView {
  title: string;
  detail: string;
}

/** 页签 / 段内导航与正文共用同一份定义，避免"加了模块却漏了入口"。 */
export interface SectionView {
  id: TodaySectionId;
  number: string;
  label: string;
  tabLabel: string;
  description: string;
  count: string;
  empty: EmptyView | null;
}

export interface MetricView {
  key: string;
  label: string;
  value: string;
}

/**
 * 「今日」页的页签目录 —— 静态单一来源。
 *
 * 页签只放"细节"三类；回答"今天能不能动"的结论与门禁不放进页签，
 * 而是常驻在页面头部，任何页签下都第一眼可见。
 */
export const TODAY_SECTION_CATALOGUE = [
  { id: "candidates", number: "01", label: "候选研究", tabLabel: "候选", description: "门禁 · 评分 · 依据" },
  { id: "messages", number: "02", label: "消息证据", tabLabel: "证据", description: "来源与影响" },
  { id: "discussion", number: "03", label: "讨论复核", tabLabel: "讨论", description: "分歧与风险" },
] as const;

export type TodaySectionId = (typeof TODAY_SECTION_CATALOGUE)[number]["id"];

export const TODAY_SECTION_IDS = TODAY_SECTION_CATALOGUE.map((section) => section.id) as readonly TodaySectionId[];

export const DEFAULT_TODAY_SECTION: TodaySectionId = "candidates";

/** hash（#candidates）→ 合法的段 id；无法识别时回到首段。 */
export function resolveTodaySection(raw: string): TodaySectionId {
  const value = raw.replace(/^#/, "").trim();
  return TODAY_SECTION_IDS.includes(value as TodaySectionId) ? (value as TodaySectionId) : DEFAULT_TODAY_SECTION;
}

/** 候选行：候选研究表与推荐清单共用，保证两处口径完全一致。 */
export interface CandidateRow {
  symbol: string;
  name: string;
  score: number;
  scoreText: string;
  status: string;
  evidence: string;
  /** 消息证据 + 讨论 + 链路确认三者齐备，才允许进入纸面复核。 */
  ready: boolean;
  strategies: string;
  keyMetric: string;
  messageCount: number;
  firstMessageTitle: string;
  debateRounds: number | null;
  debateConclusion: string;
  variantCount: number;
  nextStep: string;
  context: string;
  reasons: readonly string[];
  metrics: readonly MetricView[];
  breakdown: readonly string[];
  provenance: string;
}

export interface MessageRow {
  key: string;
  title: string;
  summary: string;
  category: string;
  impact: string;
  impactTone: Tone;
  eventType: string;
  publishedAt: string;
  source: string;
  sourceUrl: string;
  sectors: readonly string[];
  transmissionPath: readonly string[];
  transmissionHypothesis: string;
  validation: readonly string[];
  invalidation: readonly string[];
}

export interface PhaseLaneView {
  id: string;
  label: string;
  status: string;
  candidateCount: number;
  overlapSymbols: number;
  note: string;
  produced: boolean;
}

export interface CrossMarketView {
  theme: string;
  strength: string;
  watch: string;
  confirm: string;
  invalid: string;
}

/** 消息证据段要用的市场背景（比页首的「四段式」更完整，含原始条目）。 */
export interface CrossMarketLinkView {
  ruleId: string;
  theme: string;
  action: string;
  summary: string;
  sourceTitle: string;
}

export interface MarketContextView {
  status: string;
  overview: string;
  summaryLines: readonly string[];
  crossMarket: readonly CrossMarketLinkView[];
  warnings: readonly string[];
}

export interface DailyView {
  date: string;
  dateLabel: { day: string; weekday: string };
  generatedAt: string;
  isHistorical: boolean;

  conclusion: string;
  /** 门禁/推荐状态：全站唯一口径。 */
  gate: ToneView;
  /** 研究链是否闭环（候选 → 消息 → 讨论 → 复核）。 */
  chain: ToneView;
  chainDetail: string;

  candidates: readonly CandidateRow[];
  messages: readonly MessageRow[];
  debates: readonly AqspAgentResult[];
  sections: readonly SectionView[];

  /** 当天是否有实时产物；false 时不应用历史数据顶替。 */
  hasLiveContent: boolean;
  /** 当前日期没有任何候选/消息且门禁未放行 —— 用于「当天暂无产物」提示。 */
  isEmptyObservation: boolean;
  previousReviewDate: string;

  phaseLanes: readonly PhaseLaneView[];
  universe: AqspUniverse | null;
  coverageText: string;
  crossMarket: CrossMarketView | null;
  marketContext: MarketContextView | null;
  /** 消息采集侧的数据告警，空态里用于说明"为什么没有可引用证据"。 */
  marketWarnings: readonly string[];
  sourceCoverage: readonly string[];
  researchChain: AqspResearchChain | null;
  variants: readonly AqspVariant[];
  variantLatestDate: string;
  /** 数据缺失时的诚实提示，避免把 "0/0" 当成真实覆盖率。 */
  variantCoverageText: string;
}

const MARKET_PHASES = [
  { id: "pre", label: "盘前", keywords: ["盘前", "pre_market", "pre-market"] },
  { id: "intraday", label: "盘中", keywords: ["盘中", "intraday"] },
  { id: "post", label: "盘后", keywords: ["盘后", "post_market", "post-market"] },
] as const;

/** 与展示无关的纯文本工具：去重、截断。 */
export function unique(values: readonly string[] | undefined, limit = 4): string[] {
  return Array.from(new Set((values ?? []).map((value) => value.trim()).filter(Boolean))).slice(0, limit);
}

/** 把一批代码转成"代码 名称"的紧凑文本；只需要 symbol + display_name 两个字段。 */
export function symbolNames(
  symbols: readonly string[],
  candidates: readonly Pick<AqspCandidate, "symbol" | "display_name">[],
): string {
  const names = new Map(candidates.map((candidate) => [candidate.symbol, candidateName(candidate)]));
  return symbols.map((symbol) => (names.get(symbol) ? `${symbol} ${names.get(symbol)}` : symbol)).join(" · ");
}

/**
 * 门禁状态的唯一判定入口。
 * 先走 research-view 的 gatePresentation 得到分支，再只在这里做一次文案映射。
 */
export function gateView(gate: AqspRecommendationGate | undefined): ToneView {
  const presentation = gatePresentation(gate);
  if (presentation === "unavailable") {
    return {
      tone: "neutral",
      label: "门禁状态未记录",
      detail: "服务端未返回推荐门禁状态，当前只展示可核验的数据。",
    };
  }
  if (presentation === "ready") {
    return {
      tone: "ok",
      label: "已放行",
      detail: "当前结果可进入纸面复核，不自动下单。",
    };
  }
  const reason = gate?.reasons?.[0] ?? "";
  if (gate?.status === "research_display" || reason.startsWith("research_display")) {
    return {
      tone: "warn",
      label: "仅研究展示",
      detail: "服务端标记为研究展示，不进入正式推荐或纸面复核。",
    };
  }
  if (reason.startsWith("freshness_not_ready")) {
    return {
      tone: "warn",
      label: "实时数据新鲜度未达标",
      detail: "行情或消息未达到新鲜度要求，当前为研究展示，不进入正式推荐。",
    };
  }
  if (reason.startsWith("circuit_breaker")) {
    return {
      tone: "warn",
      label: "组合保护处于冷却状态",
      detail: "组合熔断冷却期内不生成新推荐，当前信号仅供参考。",
    };
  }
  return {
    tone: "warn",
    label: "当前结果仅供观察",
    detail: reason ? `未放行原因：${reason}` : "当前结果仅供观察，不进入正式推荐或纸面复核。",
  };
}

function buildPhaseLanes(phases: readonly AqspPhase[]): PhaseLaneView[] {
  return MARKET_PHASES.map((phase) => {
    const record = phases.find((item) => {
      const text = `${item.task_id} ${item.label}`.toLowerCase();
      return phase.keywords.some((keyword) => text.includes(keyword.toLowerCase()));
    });
    return {
      id: phase.id,
      label: phase.label,
      status: record?.status || "未产出",
      candidateCount: record?.candidate_count ?? 0,
      overlapSymbols: record?.overlap_symbols ?? 0,
      produced: Boolean(record),
      note: record
        ? `候选 ${record.candidate_count} · 重叠 ${record.overlap_symbols}${
            record.status === "复用盘中结果" ? " · 未形成独立复盘" : " · 独立产出"
          }`
        : "未产出独立数据段",
    };
  });
}

function buildCrossMarket(link: AqspCrossMarket | undefined): CrossMarketView | null {
  if (!link) return null;
  return {
    theme: [link.theme, link.action].filter(Boolean).join(" · ") || link.summary || "暂无跨市主线",
    strength: link.strength || "",
    watch: unique([...link.affected_sectors, ...link.transmission_path], 3).join(" → ") || "等待产业链传导信号",
    confirm: unique(link.validation_signals, 2).join("；") || "暂无确认信号",
    invalid: unique(link.invalidation_signals, 2).join("；") || "暂无失效信号",
  };
}

function buildMarketContext(context: AqspMarketContext | null): MarketContextView | null {
  if (!context) return null;
  return {
    status: context.status,
    overview: context.overview,
    summaryLines: context.summary_lines,
    crossMarket: context.cross_market.map((link) => ({
      ruleId: link.rule_id,
      theme: link.theme,
      action: link.action,
      summary: link.summary,
      sourceTitle: link.source_title,
    })),
    warnings: context.warnings,
  };
}

function buildCandidateRows(snapshot: AqspSnapshot): CandidateRow[] {
  const debates = new Map(snapshot.debates.map((debate) => [debate.symbol, debate]));
  return snapshot.candidates.map((candidate) => {
    const symbol = candidate.symbol;
    const messages = messagesForCandidate(snapshot.messages, symbol);
    const debate = debates.get(symbol);
    const metrics = (candidate.technical_metrics ?? []).map((metric) => ({
      key: metric.key,
      label: metric.label,
      value: metric.value,
    }));
    const keyMetric =
      metrics.find((metric) => metric.key === "ret20_pct") ?? metrics[0] ?? null;
    return {
      symbol,
      name: candidateName(candidate),
      score: candidate.score,
      scoreText: Number.isFinite(candidate.score) ? candidate.score.toFixed(1) : "—",
      status: candidate.research_status || "状态未记录",
      evidence: candidate.evidence_status || "证据未记录",
      ready: candidateResearchReady(snapshot, symbol),
      strategies: candidate.strategies.join(" · "),
      keyMetric: keyMetric ? `${keyMetric.label} ${keyMetric.value}` : "",
      messageCount: messages.length,
      firstMessageTitle: messages[0]?.title ?? "",
      debateRounds: debate ? debate.round_count : null,
      debateConclusion: debate?.conclusion ?? "",
      variantCount: historicalVariantCount(snapshot, symbol),
      nextStep: candidate.next_step,
      context: candidate.context,
      reasons: candidate.deterministic_reasons,
      metrics,
      breakdown: candidate.score_breakdown ?? [],
      provenance: [candidate.data_source, candidate.freshness].filter(Boolean).join(" · "),
    };
  });
}

function buildMessageRows(messages: readonly AqspMessage[]): MessageRow[] {
  return messages.map((message, index) => ({
    key: `${message.title}-${message.published_at}-${index}`,
    title: message.title,
    summary: message.summary,
    category: message.category,
    impact: message.impact,
    impactTone: message.impact === "利空" ? "warn" : message.impact === "利好" ? "ok" : "neutral",
    eventType: message.event_type ?? "",
    publishedAt: message.published_at,
    source: message.source,
    sourceUrl: messageSourceUrl(message),
    sectors: unique(message.affected_sectors, 4),
    transmissionPath: unique(message.transmission_path, 4),
    transmissionHypothesis: message.transmission_hypothesis ?? "",
    validation: unique(message.validation_signals, 2),
    invalidation: unique(message.invalidation_signals, 2),
  }));
}

/**
 * 把服务端返回的快照补齐成结构完整、可直接渲染的对象。
 *
 * 为什么需要：页面到处在读 `snapshot.candidates.length` / `available_dates.map`，
 * 任一字段缺失都会直接抛错白屏（而且被 ErrorBoundary 接住后只显示一句"页面加载失败"，
 * 很难定位）。快照由多源聚合而来，不能假设每个数组都在。
 *
 * 归一化只在数据入口做一次（见 useAqspSnapshot），下游都拿到结构完整的对象。
 */
export function normalizeSnapshot(raw: unknown): AqspSnapshot {
  const source = asRecord(raw) as Partial<AqspSnapshot>;
  return {
    ...(source as AqspSnapshot),
    schema_version: asString(source.schema_version),
    generated_at: asString(source.generated_at),
    selected_date: asString(source.selected_date),
    available_dates: asArray<string>(source.available_dates),
    candidates: asArray<AqspCandidate>(source.candidates),
    debates: asArray<AqspAgentResult>(source.debates),
    summaries: asArray<string>(source.summaries),
    source: source.source ?? { effective: "", latest_trade_date: "", lag_days: 0, status: "" },
    coldstart: source.coldstart ?? { status: "", detail: "" },
    stale_after: asString(source.stale_after),
    message_status: asString(source.message_status),
    messages: asArray<AqspMessage>(source.messages),
    market_context: source.market_context ?? null,
    phases: asArray<AqspPhase>(source.phases),
    variants: asArray<AqspVariant>(source.variants),
  };
}

export function buildDailyView(snapshot: AqspSnapshot): DailyView {
  const candidates = buildCandidateRows(snapshot);
  const messages = buildMessageRows(snapshot.messages);
  const chain = snapshot.research_chain ?? null;
  const readyCount = candidates.filter((row) => row.ready).length;
  const total = candidates.length;

  const gate = gateView(snapshot.recommendation_gate);

  const chainView: ToneView =
    total === 0
      ? { tone: "neutral", label: "当天没有候选", detail: "当前没有通过数据质量与短线筛选的对象。" }
      : readyCount === total
        ? { tone: "ok", label: "证据链已闭环", detail: "候选、个股消息、讨论与复核结论已闭环。" }
        : {
            tone: "warn",
            label: "证据链未完整",
            detail: `${readyCount}/${total} 个候选完成复核闭环，其余仅观察。`,
          };

  const chainDetail =
    total === 0
      ? "当前没有候选，无需复核。"
      : readyCount === total
        ? "候选、个股消息、讨论与复核结论已闭环。"
        : `${candidates.filter((row) => row.debateRounds !== null).length}/${total} 个候选完成讨论，${
            candidates.filter((row) => row.messageCount > 0).length
          }/${total} 个候选有可引用消息证据；当前仅观察。`;

  const universe = snapshot.universe ?? null;
  const coverageText =
    universe?.coverage_pct == null ? "—" : `${(universe.coverage_pct * 100).toFixed(1)}%`;

  const variants = snapshot.variants ?? [];
  const suite = snapshot.variant_suite;
  const variantCoverageText =
    suite && (suite.supported_symbols > 0 || suite.selected_symbols > 0)
      ? `${suite.selected_symbols}/${suite.supported_symbols} · ${(suite.coverage_pct * 100).toFixed(1)}%`
      : "覆盖率未记录";

  const isEmptyObservation = isCurrentEmptyObservation(snapshot);

  // 各页签的「计数 + 空态原因」只在这里定义一次；页签栏与正文读同一份。
  const sectionContent: Record<TodaySectionId, { count: string; empty: EmptyView | null }> = {
    candidates: {
      count: `${total} 个`,
      empty:
        total === 0
          ? {
              title: "当天没有候选",
              detail: "当前没有通过数据质量与短线筛选的对象，不用历史候选填充。",
            }
          : null,
    },
    messages: {
      count: `${messages.length} 条`,
      empty:
        messages.length === 0
          ? {
              title: "当天未形成可引用消息证据",
              detail:
                snapshot.market_context?.summary_lines?.[0] ||
                snapshot.message_status ||
                "消息产物未提供可展示事件。",
            }
          : null,
    },
    discussion: {
      count: `${snapshot.debates.length} 条`,
      empty:
        snapshot.debates.length === 0
          ? {
              title: "当天讨论未启动",
              detail: "缺少带原文链接的个股级支持、反证与可证伪条件，因此不生成模板化讨论。",
            }
          : null,
    },
  };

  const sections: SectionView[] = TODAY_SECTION_CATALOGUE.map((section) => ({
    ...section,
    ...sectionContent[section.id],
  }));

  return {
    date: snapshot.selected_date,
    dateLabel: formatDateParts(snapshot.selected_date),
    generatedAt: snapshot.generated_at,
    isHistorical: snapshot.meta?.historical ?? false,
    conclusion: snapshotConclusion(snapshot),
    gate,
    chain: chainView,
    chainDetail,
    candidates,
    messages,
    debates: snapshot.debates,
    sections,
    hasLiveContent: total > 0 || messages.length > 0 || snapshot.debates.length > 0,
    isEmptyObservation,
    previousReviewDate: latestReviewDate(snapshot),
    phaseLanes: buildPhaseLanes(snapshot.phases ?? []),
    universe,
    coverageText,
    crossMarket: buildCrossMarket(snapshot.market_context?.cross_market?.[0]),
    marketContext: buildMarketContext(snapshot.market_context),
    marketWarnings: snapshot.market_context?.warnings ?? [],
    sourceCoverage: sourceCoverageLines(snapshot),
    researchChain: chain,
    variants,
    variantLatestDate: latestVariantDate(snapshot),
    variantCoverageText,
  };
}

function formatDateParts(date: string): { day: string; weekday: string } {
  const value = new Date(`${date}T00:00:00+08:00`);
  if (Number.isNaN(value.getTime())) return { day: date, weekday: "" };
  return {
    day: new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(value),
    weekday: new Intl.DateTimeFormat("zh-CN", { weekday: "short" }).format(value),
  };
}
