// 决策层视图：把「今天能不能动 / 动哪些 / 凭什么信」三件事融合成一张读屏。
//
// 设计原则（与整个只读工作台一致）：
//   - 纯函数，不碰网络、不写任何状态（§5 红线：不下单、不覆盖打分）。
//   - 输入只来自两个已存在的只读端点：
//       snapshot（候选 + 走门 recommendation_gate + 数据健康）
//       performance（各策略命中率 / 退化预警 / 台账新鲜度 / 冷启动）
//   - 展示层的诚实双保险：命中率只在 `canShowHitRate` 为真时呈现，
//     冷启动期只报进度不报胜率（宪法 §5.4，与 performance-view 口径一致）。
//
// 这一层的价值不是"再列一遍候选"，而是回答一个过去被割裂在三处的问题：
//   "今天到底能不能下手？" —— 走门放行、台账够新、策略没在退化、有候选，四者齐备才算"可动"。

import type { AqspSnapshot, AqspCandidate } from "@/types/aqsp";
import type { PerformanceView } from "./performance-view";
import { asRecord, asString, asNumber, asArray } from "./safe";

export type Tone = "ok" | "warn" | "neutral";

/** 一个让"今天不能/不该动"的阻塞项。 */
export interface DecisionBlocker {
  kind: "gate" | "cold_start" | "stale" | "decay" | "empty";
  tone: Tone;
  title: string;
  detail: string;
}

/** 一条可执行候选（从 AqspCandidate 投影出决策层真正关心的字段）。 */
export interface DecisionCandidate {
  symbol: string;
  name: string;
  score: number;
  status: string;
  nextStep: string;
  strategies: string;
  evidence: string;
}

/** 一行策略健康度：命中率（仅在样本足够时）+ 是否处于退化预警。 */
export interface DecisionStrategyLine {
  name: string;
  hitRate: number;
  canShowHitRate: boolean;
  /** 命中退化预警时的处置建议；无预警为 null。 */
  decayNote: string | null;
}

/** 决策层视图（一次"读屏"所需的全部派生数据）。 */
export interface DecisionView {
  date: string;
  /** 四者齐备（走门放行 + 非冷启动 + 台账够新 + 有候选）才为 true。 */
  canAct: boolean;
  /** 顶部一句话结论。 */
  headline: string;
  /** 所有阻塞项（空 = 无阻塞）。 */
  blockers: readonly DecisionBlocker[];
  /** 按评分降序的候选；gate 未放行时整表降级为"观察"。 */
  candidates: readonly DecisionCandidate[];
  /** 策略健康度行。 */
  strategies: readonly DecisionStrategyLine[];
  /** 冷启动：只报进度、不报胜率。 */
  coldStart: boolean;
  /** 0~1 冷启动进度（非冷启动时为 1）。 */
  signalProgress: number;
  /** 台账是否停滞。 */
  stale: boolean;
  /** 台账新鲜度人话。 */
  staleness: string;
}

// ---------------------------------------------------------------------------
// 纯投影：候选 → 决策层候选
// ---------------------------------------------------------------------------

export function projectCandidates(snapshot: AqspSnapshot): DecisionCandidate[] {
  const rows: DecisionCandidate[] = asArray<unknown>(snapshot.candidates as readonly unknown[])
    .map((raw) => {
      const r = asRecord(raw) as Partial<AqspCandidate>;
      return {
        symbol: asString(r.symbol),
        name: asString(r.display_name, asString(r.symbol)),
        score: asNumber(r.score),
        status: asString(r.research_status, "状态未记录"),
        nextStep: asString(r.next_step),
        strategies: asArray<unknown>(r.strategies as readonly unknown[])
          .map((s) => asString(s))
          .filter(Boolean)
          .join(" · "),
        evidence: asString(r.evidence_status),
      };
    })
    .filter((row) => row.symbol !== "");
  // 决策层先给"评分最高"的排前：同一份候选，决策排序与全量研究页可能不同。
  return rows.sort((a, b) => b.score - a.score);
}

// ---------------------------------------------------------------------------
// 阻塞项：四类各判各的，互不吞并
// ---------------------------------------------------------------------------

export function decisionBlockers(snapshot: AqspSnapshot, perf: PerformanceView): DecisionBlocker[] {
  const out: DecisionBlocker[] = [];
  const gate = asRecord(snapshot.recommendation_gate) as {
    recommendation_allowed?: boolean;
    status?: string;
    reasons?: readonly string[];
  };

  // 1) 走门未放行（硬阻塞）
  if (gate && gate.recommendation_allowed === false) {
    const reasons = asArray<unknown>(gate.reasons as readonly unknown[]).map((s) => asString(s)).filter(Boolean);
    out.push({
      kind: "gate",
      tone: "warn",
      title: `走门未放行${gate.status ? `（${gate.status}）` : ""}`,
      detail: reasons.length ? reasons.join("；") : "推荐闸当前关闭，候选仅可观察。",
    });
  }

  // 2) 冷启动（只挡"用命中率背书"，不挡"看候选"）
  if (perf.coldStart) {
    out.push({
      kind: "cold_start",
      tone: "neutral",
      title: "策略仍在冷启动",
      detail: `已积累 ${perf.independentSignalDays}/${perf.minSignalDays} 个独立信号日，命中率暂不作结论。`,
    });
  }

  // 3) 台账停滞（数据不再前进）
  if (perf.stale) {
    out.push({
      kind: "stale",
      tone: "warn",
      title: "台账已停滞",
      detail: "最新信号日后未再更新，先检查产出流水线。",
    });
  }

  // 4) 策略退化预警（任一 critical 即拦"可下手"）
  const decay = perf.decayAlerts.filter((a) => a.severity.toLowerCase().includes("critical"));
  if (decay.length) {
    out.push({
      kind: "decay",
      tone: "warn",
      title: `${decay.length} 个策略触发重度退化预警`,
      detail: decay.map((a) => `${a.strategy}：${a.recommendation || "建议降权/停用"}`).join("；"),
    });
  }

  // 5) 无任何候选（gate 放行也没得选）
  if (projectCandidates(snapshot).length === 0) {
    out.push({
      kind: "empty",
      tone: "neutral",
      title: "今日无候选",
      detail: "当日流水线没有产出候选，无可下手的标的。",
    });
  }

  return out;
}

// ---------------------------------------------------------------------------
// 策略健康度行
// ---------------------------------------------------------------------------

export function strategyLines(perf: PerformanceView): DecisionStrategyLine[] {
  const alertByStrategy = new Map<string, string>();
  for (const a of perf.decayAlerts) alertByStrategy.set(a.strategy, a.recommendation || a.severity);
  return perf.strategies.map((s) => ({
    name: s.name,
    hitRate: s.hitRate,
    canShowHitRate: s.canShowHitRate,
    decayNote: alertByStrategy.get(s.name) ?? null,
  }));
}

// ---------------------------------------------------------------------------
// 合成
// ---------------------------------------------------------------------------

function buildHeadline(v: Omit<DecisionView, "headline">): string {
  if (v.canAct && v.candidates.length > 0) {
    return `可动：${v.candidates.length} 个候选，评分最高 ${v.candidates[0].name}（${v.candidates[0].score}）`;
  }
  if (v.blockers.length === 0) return "暂无明确结论";
  // 取最重的一条阻塞项当主句（gate > decay > stale > cold_start > empty）
  const priority: Record<DecisionBlocker["kind"], number> = {
    gate: 0,
    decay: 1,
    stale: 2,
    cold_start: 3,
    empty: 4,
  };
  const top = [...v.blockers].sort((a, b) => priority[a.kind] - priority[b.kind])[0];
  return top.title;
}

export function buildDecision(snapshot: AqspSnapshot, perf: PerformanceView, stalenessText: string): DecisionView {
  const candidates = projectCandidates(snapshot);
  const blockers = decisionBlockers(snapshot, perf);
  const gateBlocked = blockers.some((b) => b.kind === "gate");
  // "可动" = 走门放行 + 非冷启动 + 台账够新 + 无重度退化 + 有候选
  const canAct =
    !gateBlocked &&
    !perf.coldStart &&
    !perf.stale &&
    !blockers.some((b) => b.kind === "decay") &&
    candidates.length > 0;

  const draft = {
    date: snapshot.selected_date,
    canAct,
    candidates,
    strategies: strategyLines(perf),
    blockers,
    coldStart: perf.coldStart,
    signalProgress: perf.coldStart ? perf.progress : 1,
    stale: perf.stale,
    staleness: stalenessText,
  };
  const headline = buildHeadline(draft);
  return { ...draft, headline };
}

// ---------------------------------------------------------------------------
// 展示口径工具
// ---------------------------------------------------------------------------

/** 命中率只在 canShowHitRate 为真时呈现，否则 "—"（§5.4 诚实门槛）。 */
export function formatHitRate(line: DecisionStrategyLine): string {
  return line.canShowHitRate ? `${(line.hitRate * 100).toFixed(1)}%` : "—";
}

/** 候选表在 gate 未放行时整表降级为"观察"，禁止暗示可下单。 */
export function tableMode(snapshot: AqspSnapshot, perf: PerformanceView): "act" | "observe" {
  const blocked = decisionBlockers(snapshot, perf).some((b) => b.kind === "gate");
  return blocked ? "observe" : "act";
}
