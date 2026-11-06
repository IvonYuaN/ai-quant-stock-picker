import {
  strategyHitRateLabel,
  configRevisionLabel,
  configWindowLabel,
  configSymbolLabel,
  lastRerunLabel,
  isRerunRejected,
  isTerminalRerun,
  rerunStatusTone,
  rerunGapHints,
  buildRerunView,
  type SavedBacktestConfig,
  type RerunOutcome,
} from "./strategy-workbench-view";
import type { PerformanceStrategy } from "./api";

const strategy: PerformanceStrategy = {
  name: "ma_pullback",
  independent_signal_days: 30,
  total_picks: 70,
  win_count: 42,
  hit_rate: 0.6,
  displayable: true,
  weight_base: 1,
  weight_confidence: 1,
  avg_return_pct: 1.2,
  max_drawdown: 0.1,
  sharpe_ratio: 0.8,
};

export const strategyWorkbenchViewContracts = {
  displaysMatureFreshStrategyRate: strategyHitRateLabel(strategy, false) === "60.0%",
  hidesRateForColdStart: strategyHitRateLabel({ ...strategy, displayable: false }, false) === "样本不足或数据停滞",
  hidesRateForStaleLedger: strategyHitRateLabel(strategy, true) === "样本不足或数据停滞",
};

// ---------------------------------------------------------------------------
// 保存候选回测参数 / 一键复测：前端视图契约
// 断言均为布尔表达式，由 run-contracts.mjs 在 node 下真实求值（任一 false 即失败）。
// fixture 变量刻意保持为模块局部（不 export），避免被收集成断言。
// ---------------------------------------------------------------------------

const OLD_RUN_ID = "oldrun000000000000000000000000";
const NEW_RUN_ID = "newrun000000000000000000000000";

const baseConfig: SavedBacktestConfig = {
  schema_version: 1,
  config_id: "cand_a",
  name: "候选 A",
  revision: 3,
  origin: "saved_from_run",
  source_run_id: OLD_RUN_ID,
  strategy: { id: "entry_exit_demo", revision: 1, expression: "close > ma20" },
  strategy_hash: "a".repeat(64),
  symbols: ["600001", "600002"],
  symbol: null,
  mode: "portfolio",
  universe_mode: "explicit",
  universe_as_of: null,
  universe_hash: "b".repeat(64),
  position_sizing: "equal",
  initial_capital: 100_000,
  signal_start: "2026-10-26",
  signal_end: "2026-10-28",
  outcome_as_of: "2026-10-30",
  policy: {},
  source: "sqlite_db",
  price_mode: "raw",
  max_runtime_seconds: 300,
  params_sha256: "c".repeat(64),
  created_at: "2026-11-01T09:00:00+08:00",
  updated_at: "2026-11-02T09:00:00+08:00",
  audit: [],
  last_rerun: null,
};

const rejectedOutcome: RerunOutcome = {
  outcome: "rejected",
  config_id: "cand_a",
  config_revision: 3,
  run_id: null,
  run: null,
  coverage_gaps: [
    { symbol: "600002", latest_as_of: "2026-10-29", required_as_of: "2026-10-30", reason: "stale" },
    { symbol: "600003", latest_as_of: null, required_as_of: "2026-10-30", reason: "missing" },
  ],
  market_as_of: "2026-10-30",
  message: "一键复测前置校验失败：2 个标的未覆盖结果数据日 2026-10-30；未创建回放任务",
  terminal: true,
  source_run_id: OLD_RUN_ID,
};

const startedOutcome: RerunOutcome = {
  outcome: "started",
  config_id: "cand_a",
  config_revision: 3,
  run_id: NEW_RUN_ID,
  run: { run_id: NEW_RUN_ID, status: "running" },
  coverage_gaps: [],
  market_as_of: "2026-10-30",
  message: "已从保存配置创建全新回放任务；旧结果/gate/证据不被继承",
  terminal: false,
  source_run_id: OLD_RUN_ID,
};

const rejectedView = buildRerunView(rejectedOutcome);
const startedViewIdleResult = buildRerunView(startedOutcome, []);
const startedViewOldRunCompleted = buildRerunView(startedOutcome, [OLD_RUN_ID]);
const startedViewNewRunCompleted = buildRerunView(startedOutcome, [NEW_RUN_ID]);
const idleView = buildRerunView(null);

export const savedConfigRerunContracts = {
  // 配置列表标签
  revisionLabel: configRevisionLabel(baseConfig) === "r3",
  windowLabel: configWindowLabel(baseConfig) === "2026-10-26 → 2026-10-28 · 结果日 2026-10-30",
  symbolLabel: configSymbolLabel(baseConfig) === "组合 · 600001、600002",
  lastRerunEmpty: lastRerunLabel(baseConfig) === "尚未复测",
  lastRerunTerminal: lastRerunLabel({
    ...baseConfig,
    last_rerun: {
      outcome: "rejected", run_id: null, message: "缺覆盖", terminal: true,
      coverage_gaps: [], market_as_of: null, config_revision: 3, at: "2026-11-03T09:00:00+08:00",
    },
  }).includes("终态"),

  // 复测前置校验失败态呈现
  rejectedFlag: isRerunRejected(rejectedOutcome) === true,
  rejectedIsTerminal: isTerminalRerun(rejectedOutcome) === true,
  rejectedToneWarn: rerunStatusTone(rejectedOutcome) === "warn",
  rejectionHintCount: rerunGapHints(rejectedOutcome).length === 3,
  rejectionMentionsStaleSymbol: rerunGapHints(rejectedOutcome).some((hint) => hint.includes("600002") && hint.includes("数据陈旧")),
  rejectionMentionsMissingSymbol: rerunGapHints(rejectedOutcome).some((hint) => hint.includes("600003") && hint.includes("缺失覆盖")),
  rejectionMentionsNoRun: rerunGapHints(rejectedOutcome).some((hint) => hint.includes("未创建回放任务")),

  // 失败视图：终态、无结果、不展示旧结论、带覆盖缺口提示
  rejectedViewKind: rejectedView.kind === "rejected",
  rejectedViewHints: rejectedView.hints.length >= 2,
  rejectedViewNoOldConclusion: rejectedView.showOldConclusion === false,
  rejectedViewNoResultRun: rejectedView.resultRunId === null,
  rejectedViewCannotShowResult: rejectedView.canShowResult === false,
  rejectedViewTerminal: rejectedView.terminal === true,

  // 成功复测：只认「本次新建 run」自身的结果，绝不继承旧 gate/结论
  startedNotRejected: isRerunRejected(startedOutcome) === false,
  startedNotTerminal: isTerminalRerun(startedOutcome) === false,
  startedToneOk: rerunStatusTone(startedOutcome) === "ok",
  startedNoGapHints: rerunGapHints(startedOutcome).length === 0,
  startedViewKindRunning: startedViewIdleResult.kind === "running",
  startedViewResultRunId: startedViewIdleResult.resultRunId === NEW_RUN_ID,
  startedViewNoOldConclusion: startedViewIdleResult.showOldConclusion === false,
  startedViewCannotShowResultYet: startedViewIdleResult.canShowResult === false,
  oldRunCompletionDoesNotLeakResult: startedViewOldRunCompleted.canShowResult === false,
  oldRunCompletionNoOldConclusion: startedViewOldRunCompleted.showOldConclusion === false,
  newRunCompletionShowsOwnResult: startedViewNewRunCompleted.canShowResult === true,
  newRunCompletionKeepsNoOldConclusion: startedViewNewRunCompleted.showOldConclusion === false,

  // 空闲态
  idleKind: idleView.kind === "idle",
  idleNoOldConclusion: idleView.showOldConclusion === false,
  idleCannotShowResult: idleView.canShowResult === false,
  idleNoRun: idleView.resultRunId === null,
};
