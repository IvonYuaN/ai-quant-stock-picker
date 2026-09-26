// 决策层视图的契约断言（由 npm test 真正求值）。
//
// 核心语义锁死在这四句：
//   1. canAct 必须同时满足：走门放行 + 非冷启动 + 台账够新 + 无重度退化 + 有候选；
//   2. 命中率只在样本足够（canShowHitRate）时呈现，冷启动期一律 "—"（§5.4 诚实门槛）；
//   3. gate 拦截时表模式必须是 "observe"，禁止暗示可下单；
//   4. 阻塞项按严重度排序，headline 取最重的一条。
import {
  buildDecision,
  decisionBlockers,
  formatHitRate,
  projectCandidates,
  strategyLines,
  tableMode,
  type DecisionView,
} from "./decision-view";
import { normalizePerformance, type PerformanceView } from "./performance-view";
import type { AqspSnapshot, AqspRecommendationGate } from "@/types/aqsp";

// ── 测试夹具（名字带 fixture 前缀，执行器不把它当断言）────────────────

const perfBase = {
  schema_version: "v1",
  generated_at: "2026-09-26T10:00:00+08:00",
  available: true,
  reason: "",
  cold_start: {
    is_cold_start: false,
    min_independent_signal_days: 30,
    independent_signal_days: 42,
    max_strategy_signal_days: 38,
  },
  freshness: {
    latest_signal_date: "2026-09-24",
    ledger_updated_at: "2026-09-25T08:30:00+08:00",
    trading_days_since_latest: 1,
    stale: false,
    stale_after_trading_days: 5,
  },
  overall: { observations: 120, win_count: 63, hit_rate: 0.525, displayable: true },
  strategies: [
    {
      name: "morning_breakout",
      independent_signal_days: 38,
      total_picks: 90,
      win_count: 48,
      hit_rate: 0.533,
      displayable: true,
      weight_base: 1,
      weight_confidence: 0.9,
      avg_return_pct: 1.2,
      max_drawdown: -0.08,
      sharpe_ratio: 0.6,
    },
    {
      name: "closing_premium",
      independent_signal_days: 8, // 样本不足 → 命中率不可展示
      total_picks: 20,
      win_count: 12,
      hit_rate: 0.6,
      displayable: false,
      weight_base: 1,
      weight_confidence: 0.3,
      avg_return_pct: 0.5,
      max_drawdown: -0.04,
      sharpe_ratio: 0.2,
    },
  ],
  decay_alerts: [
    {
      strategy: "closing_premium",
      lookback_days: 7,
      decay_days: 5,
      recent_win_rate: 0.2,
      recent_avg_return_pct: -0.4,
      severity: "critical",
      recommendation: "建议暂停该策略信号",
    },
  ],
  status_counts: { validated: 80, pending: 30, observation_only: 10 },
  notes: [],
};

const gateOpen: AqspRecommendationGate = {
  recommendation_allowed: true,
  status: "放行",
  reasons: [],
};
const gateBlocked: AqspRecommendationGate = {
  recommendation_allowed: false,
  status: "数据降级",
  reasons: ["行情源滞后 2 天", "样本覆盖不足"],
};

function snapshotFixture(over: { gate?: AqspRecommendationGate; candidates?: string[] } = {}): AqspSnapshot {
  const gate = over.gate ?? gateOpen;
  const symbols = over.candidates ?? ["600519", "000858"];
  return {
    schema_version: "v1",
    generated_at: "2026-09-26T09:30:00+08:00",
    selected_date: "2026-09-24",
    available_dates: ["2026-09-24"],
    candidates: symbols.map((symbol, i) => ({
      symbol,
      display_name: symbol === "600519" ? "贵州茅台" : "五粮液",
      score: 90 - i * 10,
      research_status: "validated",
      next_step: "明日开盘验证",
      context: "",
      deterministic_reasons: ["放量突破"],
      strategies: ["放量突破"],
      evidence_status: "已闭环",
    })),
    debates: [],
    summaries: [],
    source: { effective: "ok", latest_trade_date: "2026-09-24", lag_days: 0, status: "fresh" },
    coldstart: { status: "ok", detail: "" },
    stale_after: "2026-09-26T19:00:00+08:00",
    message_status: "ok",
    messages: [],
    market_context: null,
    recommendation_gate: gate,
  } as unknown as AqspSnapshot;
}

function healthyPerf(): PerformanceView {
  // 去掉 critical 退化 → 健康的性能视图
  const raw = { ...perfBase, decay_alerts: [] };
  return normalizePerformance(raw);
}
function decayedPerf(): PerformanceView {
  return normalizePerformance(perfBase);
}
function coldPerf(): PerformanceView {
  const raw = {
    ...perfBase,
    cold_start: { ...perfBase.cold_start, is_cold_start: true, independent_signal_days: 12 },
    decay_alerts: [],
  };
  return normalizePerformance(raw);
}
function stalePerf(): PerformanceView {
  const raw = {
    ...perfBase,
    freshness: { ...perfBase.freshness, stale: true, trading_days_since_latest: 12 },
    decay_alerts: [],
  };
  return normalizePerformance(raw);
}

// ── 断言 ────────────────────────────────────────────────────────────

export const decisionContract: Record<string, boolean> = {
  // 候选投影
  "projectCandidates 按评分降序":
    projectCandidates(snapshotFixture()).map((c) => c.symbol).join("|") === "600519|000858",
  "projectCandidates 空快照安全": projectCandidates(snapshotFixture({ candidates: [] })).length === 0,

  // 走门放行 + 健康 → 可动
  "gate放行+健康+有候选 ⇒ canAct": buildDecision(snapshotFixture(), healthyPerf(), "").canAct === true,
  "可动时零阻塞项": buildDecision(snapshotFixture(), healthyPerf(), "").blockers.length === 0,
  "可动时表模式为 act": tableMode(snapshotFixture(), healthyPerf()) === "act",
  "可动时 headline 报可动": buildDecision(snapshotFixture(), healthyPerf(), "").headline.startsWith("可动"),

  // 走门拦截
  "gate拦截 ⇒ 不可动": buildDecision(snapshotFixture({ gate: gateBlocked }), healthyPerf(), "").canAct === false,
  "gate拦截产生 gate 阻塞项":
    decisionBlockers(snapshotFixture({ gate: gateBlocked }), healthyPerf()).some((b) => b.kind === "gate"),
  "gate拦截时表模式降级 observe": tableMode(snapshotFixture({ gate: gateBlocked }), healthyPerf()) === "observe",
  "gate阻塞 headline 含走门未放行":
    buildDecision(snapshotFixture({ gate: gateBlocked }), healthyPerf(), "").headline.includes("走门未放行"),

  // 冷启动
  "冷启动 ⇒ 不可用命中率背书": buildDecision(snapshotFixture(), coldPerf(), "").canAct === false,
  "冷启动产生 cold_start 阻塞项":
    decisionBlockers(snapshotFixture(), coldPerf()).some((b) => b.kind === "cold_start"),
  "冷启动阻塞项报进度而非胜率":
    decisionBlockers(snapshotFixture(), coldPerf()).find((b) => b.kind === "cold_start")?.detail.includes("12/30") === true,

  // 台账停滞
  "台账停滞 ⇒ 不可动": buildDecision(snapshotFixture(), stalePerf(), "").canAct === false,
  "台账停滞产生 stale 阻塞项":
    decisionBlockers(snapshotFixture(), stalePerf()).some((b) => b.kind === "stale"),

  // 重度退化
  "critical 退化 ⇒ 不可动": buildDecision(snapshotFixture(), decayedPerf(), "").canAct === false,
  "critical 退化产生 decay 阻塞项":
    decisionBlockers(snapshotFixture(), decayedPerf()).some((b) => b.kind === "decay"),
  "decay 阻塞项带处置建议":
    decisionBlockers(snapshotFixture(), decayedPerf()).find((b) => b.kind === "decay")?.detail.includes("暂停") === true,

  // 无候选
  "gate放行但零候选 ⇒ 不可动": buildDecision(snapshotFixture({ candidates: [] }), healthyPerf(), "").canAct === false,
  "零候选产生 empty 阻塞项":
    decisionBlockers(snapshotFixture({ candidates: [] }), healthyPerf()).some((b) => b.kind === "empty"),

  // 策略健康度行 + 命中率诚实门槛
  "策略行数与输入一致": strategyLines(healthyPerf()).length === 2,
  "样本足策略展示命中率": formatHitRate(strategyLines(healthyPerf())[0]) === "53.3%",
  "样本不足策略命中率显示 —": formatHitRate(strategyLines(healthyPerf())[1]) === "—",
  "退化策略带处置建议": strategyLines(decayedPerf())[1].decayNote !== null,
  "健康策略无退化注": strategyLines(healthyPerf())[0].decayNote === null,

  // 阻塞项排序：gate 永远排第一（比 decay 重）
  "阻塞排序 gate 优先": (() => {
    const view: DecisionView = buildDecision(snapshotFixture({ gate: gateBlocked }), decayedPerf(), "");
    return view.blockers[0].kind === "gate";
  })(),
};
