import type { PerformanceStrategy } from "@/lib/api";

export function strategyHitRateLabel(strategy: PerformanceStrategy, stale: boolean): string {
  if (!strategy.displayable || stale) return "样本不足或数据停滞";
  return `${(strategy.hit_rate * 100).toFixed(1)}%`;
}

// ---------------------------------------------------------------------------
// 保存候选回测参数 / 一键复测：纯视图逻辑。
// 不依赖任何浏览器全局，可被 run-contracts.mjs 在 node 下直接求值断言。
// ---------------------------------------------------------------------------

export type RerunCoverageReason = "missing" | "stale";

export interface RerunCoverageGap {
  symbol: string;
  latest_as_of: string | null;
  required_as_of: string;
  reason: RerunCoverageReason;
}

/** 后端 rerun_from_config 返回的一次复测结果（成功 / 前置校验拒绝）。 */
export interface RerunOutcome {
  outcome: "started" | "rejected";
  config_id: string;
  config_revision: number;
  run_id: string | null;
  run: Record<string, unknown> | null;
  coverage_gaps: RerunCoverageGap[];
  market_as_of: string | null;
  message: string;
  /** 终态不自动重跑；rejected 恒为 true。 */
  terminal: boolean;
  source_run_id: string | null;
}

/** 配置审计留痕（created / updated / rerun_*）。 */
export interface ConfigAuditEvent {
  action: "created" | "updated" | "rerun_started" | "rerun_rejected";
  at: string;
  revision: number;
  params_sha256?: string;
  outcome?: string;
  run_id?: string | null;
  terminal?: boolean;
  message?: string;
  coverage_gaps?: RerunCoverageGap[];
}

/** 配置上记录的最近一次复测终态摘要（可读、不自动重跑）。 */
export interface LastRerun {
  outcome: "started" | "rejected";
  run_id: string | null;
  message: string;
  terminal: boolean;
  coverage_gaps: RerunCoverageGap[];
  market_as_of: string | null;
  config_revision: number;
  at: string;
}

/** 按租户隔离的可复用命名回测配置；只含参数，绝不含结果/gate/证据。 */
export interface SavedBacktestConfig {
  schema_version: number;
  config_id: string;
  name: string;
  revision: number;
  origin: "saved_from_run" | "manual";
  source_run_id: string | null;
  strategy: Record<string, unknown>;
  strategy_hash: string;
  symbols: string[];
  symbol: string | null;
  mode: "single" | "portfolio";
  universe_mode: "explicit" | "local_history";
  universe_as_of: string | null;
  universe_hash: string;
  position_sizing: "equal" | "score_weight";
  initial_capital: number | null;
  signal_start: string;
  signal_end: string;
  outcome_as_of: string;
  policy: Record<string, unknown>;
  source: string;
  price_mode: string;
  max_runtime_seconds: number;
  params_sha256: string;
  created_at: string;
  updated_at: string;
  audit: ConfigAuditEvent[];
  last_rerun: LastRerun | null;
  last_rerun_at?: string;
}

export function configRevisionLabel(config: SavedBacktestConfig): string {
  return `r${config.revision}`;
}

export function configWindowLabel(config: SavedBacktestConfig): string {
  return `${config.signal_start} → ${config.signal_end} · 结果日 ${config.outcome_as_of}`;
}

export function configSymbolLabel(config: SavedBacktestConfig): string {
  const preview = config.symbols.slice(0, 3).join("、");
  const more = config.symbols.length > 3 ? ` 等 ${config.symbols.length} 只` : "";
  return `${config.mode === "portfolio" ? "组合" : "单标"} · ${preview}${more}`;
}

export function lastRerunLabel(config: SavedBacktestConfig): string {
  const last = config.last_rerun;
  if (!last) return "尚未复测";
  if (last.outcome === "rejected") return `上次复测被拒（终态，不自动重跑）· ${last.message}`;
  return `上次复测已创建 r${last.config_revision} · 新任务 ${last.run_id ?? "—"}`;
}

export function isRerunRejected(outcome: RerunOutcome | null | undefined): boolean {
  return outcome?.outcome === "rejected";
}

export function isTerminalRerun(outcome: RerunOutcome | null | undefined): boolean {
  return outcome?.terminal === true;
}

export function rerunStatusTone(outcome: RerunOutcome | null | undefined): "ok" | "warn" {
  return outcome?.outcome === "started" ? "ok" : "warn";
}

/** 失败态呈现：前置校验失败消息 + 逐标的 as-of 覆盖缺口提示。 */
export function rerunGapHints(outcome: RerunOutcome | null | undefined): string[] {
  if (!outcome || outcome.outcome !== "rejected") return [];
  const hints = [outcome.message];
  for (const gap of outcome.coverage_gaps) {
    const actual = gap.latest_as_of ?? "无数据";
    const reason = gap.reason === "missing" ? "缺失覆盖" : "数据陈旧";
    hints.push(`${gap.symbol}：${reason}（最新 ${actual}，需覆盖至 ${gap.required_as_of}）`);
  }
  return hints;
}

export type RerunViewKind = "idle" | "rejected" | "running" | "completed";

export interface RerunViewState {
  kind: RerunViewKind;
  hints: string[];
  /**
   * 是否把旧 run 的成功结论当作新 run 的结果展示 —— 恒为 false。
   * 这是「不把旧 run 的成功结论渲染成新 run 的结果」的单一入口。
   */
  showOldConclusion: boolean;
  /** 只认本次一键复测新建的 run id；绝不回退到 source_run_id / last_rerun 的旧 run。 */
  resultRunId: string | null;
  /** 仅当本次新建 run 自身完成，才允许展示其结果（前端不渲染任何旧结论）。 */
  canShowResult: boolean;
  terminal: boolean;
  message: string;
}

/**
 * 把一次复测 outcome 转成视图状态。
 * completedRunIds 只用于判断「本次新建 run」是否已有自身结果；
 * 即便传入的是旧 run 的 id，也不会让 canShowResult 变 true。
 */
export function buildRerunView(
  outcome: RerunOutcome | null | undefined,
  completedRunIds: readonly string[] = [],
): RerunViewState {
  if (!outcome) {
    return {
      kind: "idle", hints: [], showOldConclusion: false, resultRunId: null,
      canShowResult: false, terminal: false, message: "",
    };
  }
  if (outcome.outcome === "rejected") {
    return {
      kind: "rejected", hints: rerunGapHints(outcome), showOldConclusion: false,
      resultRunId: null, canShowResult: false, terminal: true, message: outcome.message,
    };
  }
  const newRunId = outcome.run_id;
  const completed = newRunId !== null && completedRunIds.includes(newRunId);
  return {
    kind: completed ? "completed" : "running",
    hints: [],
    showOldConclusion: false,
    resultRunId: newRunId,
    canShowResult: completed,
    terminal: false,
    message: outcome.message,
  };
}
