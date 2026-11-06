import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useLocation } from "react-router-dom";
import { BarChart3, BookOpenCheck, ChevronLeft, ChevronRight, CircleStop, Download, FlaskConical, Play, RefreshCw, SlidersHorizontal, TableProperties, Trash2 } from "lucide-react";
import { Badge, EmptyState, LoadingState, SectionHeader, StatePanel, Tag } from "@/components/ui/primitives";
import { api, type BacktestRun, type FactorDefinition, type FactorDefinitionsPayload, type FactorExpressionCatalog, type FactorFamilyValidation, type FactorTrialResult, type IcDualVerdictPayload, type IcHistoryPayload, type PerformancePayload, type StrategyBacktestRun, type StrategyBacktestTrade, type StrategyPortfolioBacktestTrade, type StrategyDefinition, type VariantReviewArtifact, type VariantReviewChoice, type VariantReviewsPayload } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { cn } from "@/lib/utils";
import { factorTrialSampleLabel, icEvidenceStatusLabel, variantRefreshLabel, variantSuiteLabel } from "@/lib/research-workbench-view";

type ResearchMode = "factors" | "backtest";

function VariantRefreshPanel({ suite }: { suite: NonNullable<ReturnType<typeof useWorkspaceSnapshot>["data"]>["variant_suite"] }) {
  if (!suite) return null;
  const refreshStatus = suite.refresh_status ?? "unknown";
  const tone = refreshStatus === "completed"
    ? "ok"
    : refreshStatus === "waiting" || refreshStatus === "staged" || refreshStatus === "unknown"
      ? "neutral"
      : "warn";
  const stagedCount = suite.profiles_staged ?? 0;
  const totalCount = suite.profiles_total ?? 0;
  const details = [
    variantRefreshLabel(suite),
    suite.refresh_generated_at ? formatEvidenceTime(suite.refresh_generated_at) : "",
    refreshStatus === "staged" && totalCount > 0
      ? String(stagedCount) + "/" + String(totalCount) + " 个变体"
      : "",
    suite.refresh_message ?? "",
  ].filter(Boolean).join(" · ");
  return <StatePanel tone={tone}>
    {details}
    {refreshStatus !== "completed" && suite.variant_count > 0
      ? "；下方仍是最近一次合格发布结果。"
      : ""}
  </StatePanel>;
}

const BACKTEST_STATUS: Record<BacktestRun["status"], string> = {
  starting: "准备中", running: "运行中", cancelling: "正在取消", completed: "已完成", failed: "失败", cancelled: "已取消", timed_out: "超时停止", unknown: "状态未知",
};

function UserBacktestRuns() {
  const [runs, setRuns] = useState<BacktestRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [symbols, setSymbols] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const hasRunning = runs.some((run) => ["starting", "running", "cancelling"].includes(run.status));

  useEffect(() => {
    let current = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      try {
        const result = await api.backtestRuns();
        if (current) { setRuns(result.items); setError(""); }
      } catch (reason) {
        if (current) setError(reason instanceof Error ? reason.message : "回测任务读取失败");
      } finally {
        if (current) {
          setLoading(false);
          timer = setTimeout(refresh, hasRunning ? 2500 : 20000);
        }
      }
    };
    void refresh();
    return () => { current = false; if (timer) clearTimeout(timer); };
  }, [hasRunning]);

  async function startRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const created = await api.startBacktestRun({
        strategy_id: "composite",
        symbols: symbols.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean),
        start,
        end,
        train_days: 120,
        test_days: 30,
        horizon_days: 3,
      });
      setRuns((previous) => [created, ...previous.filter((item) => item.run_id !== created.run_id)]);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "回测任务未启动"); }
    finally { setBusy(false); }
  }

  async function cancelRun(run: BacktestRun) {
    setBusy(true); setError("");
    try {
      const updated = await api.cancelBacktestRun(run.run_id);
      setRuns((previous) => previous.map((item) => item.run_id === updated.run_id ? updated : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "取消失败"); }
    finally { setBusy(false); }
  }

  async function exportReport(run: BacktestRun) {
    try {
      const markdown = await api.backtestRunReport(run.run_id);
      const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `aqsp-backtest-${run.run_id}.md`; anchor.click();
      URL.revokeObjectURL(url);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "报告导出失败"); }
  }

  async function deleteRun(run: BacktestRun) {
    if (!window.confirm(`删除 ${run.config.symbols.join(", ")} 的回测任务、报告和独立研究产物？`)) return;
    setBusy(true); setError("");
    try {
      await api.deleteBacktestRun(run.run_id);
      setRuns((previous) => previous.filter((item) => item.run_id !== run.run_id));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "回测任务删除失败"); }
    finally { setBusy(false); }
  }

  return <section className="aq-section">
    <SectionHeader title="运行回测" description="对指定 A 股标的运行 composite Walk-forward；数据使用 raw SQLite 与可用 PIT 输入，结果是独立研究报告，不改变正式策略或成交台账。" count={`${runs.length} 条任务`} />
    {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
    <form className="aq-candidate-review-form-grid" onSubmit={(event) => void startRun(event)}>
      <label>股票代码<input className="aq-input" required value={symbols} onChange={(event) => setSymbols(event.target.value)} placeholder="例如 600000, 000001" /></label>
      <label>开始日期<input className="aq-input" type="date" required value={start} onChange={(event) => setStart(event.target.value)} /></label>
      <label>结束日期<input className="aq-input" type="date" required value={end} onChange={(event) => setEnd(event.target.value)} /></label>
      <button className="aq-btn aq-btn-primary" type="submit" disabled={busy}><Play aria-hidden="true" />{busy ? "提交中…" : "启动 Walk-forward"}</button>
    </form>
    {loading && !runs.length ? <LoadingState label="恢复回测任务…" /> : !runs.length ? <EmptyState title="还没有用户回测任务" detail="任务状态和完成报告按当前租户保存，可在离开页面后继续查看。" /> : <div className="aq-variant-review-list">
      {runs.map((run) => <article className="aq-variant-review-item" key={run.run_id}>
        <header><div><h3>{run.config.symbols.join(", ")}</h3><span className="aq-code">{run.config.start} 至 {run.config.end}</span></div><Tag tone={run.status === "completed" ? "ok" : run.status === "failed" || run.status === "unknown" ? "warn" : "neutral"}>{BACKTEST_STATUS[run.status]}</Tag></header>
        <p>{run.message}</p>
        <p className="aq-note">{run.config.source} · {run.config.price_mode} · SQLite 数据截至 {run.config.data_as_of} · 创建于 {formatEvidenceTime(run.created_at)}</p>
        {run.gate ? <>
          <p className="aq-note">DSR {String(run.gate.dsr ?? "—")} · PBO {typeof run.gate.pbo === "number" ? `${(run.gate.pbo * 100).toFixed(2)}%` : "—"} · CSCV {run.gate.cscv_reliability === "degraded" ? "可靠性降级" : run.gate.cscv_reliability === "ok" ? "可靠性正常" : "未知"} · 研究门 {run.gate.both_pass === true ? "通过，仍需人工复核" : run.gate.both_pass === false ? "未通过" : "未验证"}</p>
          {Array.isArray(run.gate.cscv_warnings) && run.gate.cscv_warnings.length ? <StatePanel tone="warn">CSCV 可靠性说明：{String(run.gate.cscv_warnings[0])}</StatePanel> : null}
        </> : null}
        {run.oos_evidence ? <StatePanel tone={run.oos_evidence.evidence_status === "ready" ? "ok" : "warn"}>OOS 证据 {run.oos_evidence.evidence_status === "ready" ? "可供人工复核" : "已阻断"} · 有效标的 {run.oos_evidence.effective_symbols ?? "—"}{run.oos_evidence.blockers?.length ? ` · ${run.oos_evidence.blockers.slice(0, 2).join("；")}` : ""}</StatePanel> : null}
        {run.artifact_id ? <p className="aq-code">报告指纹 {run.artifact_id}</p> : null}
        <div className="aq-row-actions">
          {run.status === "starting" || run.status === "running" ? <button className="aq-btn" type="button" disabled={busy} onClick={() => void cancelRun(run)}><CircleStop aria-hidden="true" />取消</button> : null}
          {run.report_available ? <button className="aq-btn" type="button" onClick={() => void exportReport(run)}><Download aria-hidden="true" />导出报告</button> : null}
          {!["starting", "running", "cancelling"].includes(run.status) ? <button className="aq-btn" type="button" disabled={busy} onClick={() => void deleteRun(run)}><Trash2 aria-hidden="true" />删除</button> : null}
        </div>
        {run.log_tail ? <details className="aq-workspace-fold"><summary>任务输出</summary><pre className="aq-note">{run.log_tail}</pre></details> : null}
      </article>)}
    </div>}
  </section>;
}

const STRATEGY_REPLAY_STATUS: Record<StrategyBacktestRun["status"], string> = {
  starting: "准备中", running: "回放中", cancelling: "正在取消", completed: "已完成",
  failed: "失败", cancelled: "已取消", timed_out: "超时停止", unknown: "状态未知",
};

function strategyReplayTradeStatus(trade: StrategyBacktestTrade | StrategyPortfolioBacktestTrade): string {
  if (trade.status === "not_executable") return `不可成交 · ${trade.blocker || "无成交"}`;
  if (trade.status === "entry_pending") return `待确认入场 · ${trade.blocker || "缺少下一交易日"}`;
  if (trade.status === "exit_pending") return `退出受阻 · ${trade.blocker || "待执行"}`;
  if (trade.status === "open") return "期末未平仓";
  return trade.exit_reason || "已平仓";
}

function UserStrategyBacktests() {
  const [definitions, setDefinitions] = useState<StrategyDefinition[]>([]);
  const [runs, setRuns] = useState<StrategyBacktestRun[]>([]);
  const [tradePages, setTradePages] = useState<Record<string, number>>({});
  const [strategyId, setStrategyId] = useState("");
  const [replayMode, setReplayMode] = useState<"single" | "portfolio">("single");
  const [universeMode, setUniverseMode] = useState<"explicit" | "local_history">("explicit");
  const [positionSizing, setPositionSizing] = useState<"equal" | "score_weight">("equal");
  const [symbol, setSymbol] = useState("");
  const [symbols, setSymbols] = useState("");
  const [initialCapital, setInitialCapital] = useState("");
  const [signalStart, setSignalStart] = useState("");
  const [signalEnd, setSignalEnd] = useState("");
  const [outcomeAsOf, setOutcomeAsOf] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const hasRunning = runs.some((run) => ["starting", "running", "cancelling"].includes(run.status));
  const selectedStrategy = definitions.find((item) => item.id === strategyId);
  const tradePageSize = 50;

  useEffect(() => {
    let active = true;
    Promise.allSettled([api.strategyDefinitions(), api.strategyBacktests()]).then(([definitionsResult, runsResult]) => {
      if (!active) return;
      if (definitionsResult.status === "fulfilled") {
        setDefinitions(definitionsResult.value.items);
        setStrategyId((current) => current || definitionsResult.value.items.find((item) => item.enabled)?.id || "");
      } else setError(definitionsResult.reason instanceof Error ? definitionsResult.reason.message : "自定义策略读取失败");
      if (runsResult.status === "fulfilled") setRuns(runsResult.value.items);
      else setError(runsResult.reason instanceof Error ? runsResult.reason.message : "策略回放任务读取失败");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!hasRunning) return;
    let active = true;
    const timer = setInterval(() => {
      void api.strategyBacktests().then((value) => { if (active) setRuns(value.items); })
        .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "策略回放状态读取失败"); });
    }, 1500);
    return () => { active = false; clearInterval(timer); };
  }, [hasRunning]);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setError("");
    try {
      const created = await api.startStrategyBacktest({
        strategy_id: strategyId,
        mode: replayMode,
        universe_mode: replayMode === "portfolio" ? universeMode : "explicit",
        position_sizing: replayMode === "portfolio" ? positionSizing : "equal",
        ...(replayMode === "single"
          ? { symbol: symbol.trim() }
          : {
              ...(universeMode === "explicit"
                ? { symbols: symbols.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean) }
                : {}),
              initial_capital: Number(initialCapital),
            }),
        signal_start: signalStart,
        signal_end: signalEnd,
        outcome_as_of: outcomeAsOf,
      });
      setRuns((current) => [created, ...current.filter((run) => run.run_id !== created.run_id)]);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "自定义策略回放未启动"); }
    finally { setBusy(false); }
  }

  async function cancel(run: StrategyBacktestRun) {
    try {
      const updated = await api.cancelStrategyBacktest(run.run_id);
      setRuns((current) => current.map((item) => item.run_id === run.run_id ? updated : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "策略回放取消失败"); }
  }

  async function report(run: StrategyBacktestRun) {
    try {
      const content = await api.strategyBacktestReport(run.run_id);
      const url = URL.createObjectURL(new Blob([content], { type: "text/markdown;charset=utf-8" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `aqsp-strategy-replay-${run.run_id}.md`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "策略回放报告导出失败"); }
  }

  async function remove(run: StrategyBacktestRun) {
    if (!window.confirm(`删除 ${run.strategy_id} / ${(run.symbols ?? [run.symbol].filter(Boolean) as string[]).join(", ")} 的策略回放及报告？`)) return;
    try {
      await api.deleteStrategyBacktest(run.run_id);
      setRuns((current) => current.filter((item) => item.run_id !== run.run_id));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "策略回放删除失败"); }
  }

  return <section className="aq-section">
    <SectionHeader title="自定义策略回放" description="单标逐笔回放或多标共享现金组合；信号日收盘判断、次日开盘成交。结果与 composite Walk-forward/DSR/PBO/OOS 隔离，风险阈值绑定当前版本。" count={`${runs.length}/20 条任务`} />
    {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
    <form className="aq-candidate-review-form-grid" onSubmit={(event) => void start(event)}>
      <label>自定义策略<select className="aq-input" value={strategyId} onChange={(event) => setStrategyId(event.target.value)} required disabled={!definitions.some((item) => item.enabled)}><option value="" disabled>选择已启用策略</option>{definitions.filter((item) => item.enabled).map((item) => <option key={item.id} value={item.id}>{item.name} · r{item.revision}</option>)}</select></label>
      <label>回放模式<select className="aq-input" value={replayMode} onChange={(event) => setReplayMode(event.target.value as "single" | "portfolio")}><option value="single">单标</option><option value="portfolio">策略组合</option></select></label>
      {replayMode === "portfolio" ? <label>组合仓位<select className="aq-input" value={positionSizing} onChange={(event) => setPositionSizing(event.target.value as "equal" | "score_weight")}><option value="equal">等权</option><option value="score_weight" disabled={!selectedStrategy?.score_expression}>按评分加权</option></select></label> : null}
      {replayMode === "portfolio" ? <label>标的池<select className="aq-input" value={universeMode} onChange={(event) => setUniverseMode(event.target.value as "explicit" | "local_history")}><option value="explicit">指定代码</option><option value="local_history">本机历史行情池</option></select></label> : null}
      {replayMode === "single" ? <label>股票代码<input className="aq-input" required pattern="[0-9]{6}" maxLength={6} value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="例如 600519" /></label> : <>
        {universeMode === "explicit" ? <label>组合股票代码<textarea className="aq-input" required rows={2} value={symbols} onChange={(event) => setSymbols(event.target.value)} placeholder="输入 1–10,000 个六位代码，以逗号或空格分隔" /></label> : <p className="aq-note">标的池按入场区间内 raw 日线实际出现的 A 股代码固定；它不含完整上市状态、历史 ST 或停牌主数据。</p>}
        <label>初始资金（元）<input className="aq-input" type="number" min="1" step="1" required value={initialCapital} onChange={(event) => setInitialCapital(event.target.value)} placeholder="例如 1000000" /></label>
      </>}
      <label>入场信号开始<input className="aq-input" type="date" required value={signalStart} onChange={(event) => setSignalStart(event.target.value)} /></label>
      <label>入场信号结束<input className="aq-input" type="date" required value={signalEnd} onChange={(event) => setSignalEnd(event.target.value)} /></label>
      <label>结果数据截至<input className="aq-input" type="date" required value={outcomeAsOf} onChange={(event) => setOutcomeAsOf(event.target.value)} /></label>
      <button className="aq-btn aq-btn-primary" type="submit" disabled={busy || !strategyId || hasRunning || runs.length >= 20 || (replayMode === "portfolio" && ((universeMode === "explicit" && !symbols.trim()) || !(Number(initialCapital) > 0)))}><Play aria-hidden="true" />{busy ? "提交中…" : replayMode === "portfolio" ? "启动策略组合" : "启动单标回放"}</button>
    </form>
    {loading && !runs.length ? <LoadingState label="恢复自定义策略回放任务…" /> : !runs.length ? <EmptyState title="还没有自定义策略回放" detail="先在策略页创建启用的日线表达式策略。" /> : <div className="aq-variant-review-list">
      {runs.map((run) => <article className="aq-variant-review-item" key={run.run_id}>
        <header><div><h3>{run.strategy_id} · {run.mode === "portfolio" ? `${run.symbols?.length ?? 0} 只组合` : run.symbol}</h3><span className="aq-code">信号 {run.signal_start} 至 {run.signal_end} · 结果截至 {run.outcome_as_of}</span></div><span className="aq-strategy-replay-status"><Tag tone={run.status === "completed" ? "ok" : ["failed", "unknown", "timed_out"].includes(run.status) ? "warn" : "neutral"}>{STRATEGY_REPLAY_STATUS[run.status]}</Tag></span></header>
        <p>{run.message}</p><p className="aq-note">策略 r{run.strategy_revision} · hash {run.strategy_hash.slice(0, 16)} · {run.source} raw · 撮合 {run.fill_rule} · 仓位 {run.position_sizing === "score_weight" ? "评分加权" : "等权"} · 标的池 {run.universe_mode === "local_history" ? `本机历史行情 ${run.universe_as_of ?? ""} · ${run.universe_symbol_count ?? run.symbols?.length ?? 0} 只 · ${run.universe_hash?.slice(0, 12) ?? ""}` : "指定代码"} · thresholds {run.thresholds_version}</p>
        {run.result ? <>
          <div className="aq-total-grid"><div className="aq-total-card"><span>已平仓</span><b>{run.result.closed_trades}</b></div><div className="aq-total-card"><span>胜率</span><b>{run.result.win_rate == null ? "样本不足" : `${(run.result.win_rate * 100).toFixed(1)}%`}</b></div><div className="aq-total-card"><span>{run.mode === "portfolio" ? "组合净值收益" : "逐笔顺序复利估算"}</span><b>{run.result.total_return_pct.toFixed(2)}%</b></div><div className="aq-total-card"><span>不可成交入场</span><b>{run.result.not_executable}</b></div></div>
          {run.mode === "portfolio" ? <>
            <p className="aq-note">初始资金 {run.result.initial_capital?.toLocaleString("zh-CN")} 元 · 期末权益 {run.result.final_equity?.toLocaleString("zh-CN")} 元 · 最大回撤 {run.result.max_drawdown_pct?.toFixed(2)}% · Sharpe {run.result.sharpe_ratio?.toFixed(2) ?? "样本不足"}</p>
            {run.result.score_expression ? <p className="aq-note">评分表达式：<code>{run.result.score_expression}</code></p> : null}
            {run.result.equity_curve?.length ? <div className="aq-table-wrap"><table className="aq-table"><thead><tr><th>日期</th><th className="aq-num">净值</th><th className="aq-num">现金</th><th className="aq-num">曝险</th><th className="aq-num">回撤</th><th className="aq-num">持仓</th></tr></thead><tbody>{run.result.equity_curve.slice(-20).map((point) => <tr key={point.date}><td>{point.date}</td><td className="aq-num">{point.equity.toLocaleString("zh-CN")}</td><td className="aq-num">{point.cash.toLocaleString("zh-CN")}</td><td className="aq-num">{(point.exposure_pct * 100).toFixed(1)}%</td><td className="aq-num">{point.drawdown_pct.toFixed(2)}%</td><td className="aq-num">{point.positions}</td></tr>)}</tbody></table></div> : null}
          </> : <p className="aq-note">收益按每笔全额顺序复利估算；未建模闲置资金与组合曝险，不代表组合净值。</p>}
          {run.result.pending_entries ? <StatePanel tone="warn">有 {run.result.pending_entries} 笔入场没有结果日内的下一市场交易日，仍待成交结果，未计入收益与胜率。</StatePanel> : null}
          {run.result.blocked_symbols.length ? <StatePanel tone="warn">数据阻断 {run.result.blocked_symbols.length} 只：{run.result.blocked_symbols.slice(0, 5).map((item) => `${item.symbol} ${item.reason}`).join("；")}{run.result.blocked_symbols.length > 5 ? `；另有 ${run.result.blocked_symbols.length - 5} 只` : ""}</StatePanel> : null}
          {(Array.isArray(run.result.open_positions) ? run.result.open_positions.length : run.result.open_positions) ? <StatePanel tone="warn">期末仍有 {Array.isArray(run.result.open_positions) ? run.result.open_positions.length : run.result.open_positions} 笔持仓或待执行退出；未计入胜率，不能当成已实现结果。</StatePanel> : null}
          {run.result.trades.length ? <>
            <div className="aq-row-actions aq-strategy-replay-pagination">
              <span className="aq-muted">逐笔 {Math.min((tradePages[run.run_id] ?? 0) * tradePageSize + 1, run.result.trades.length)}–{Math.min(((tradePages[run.run_id] ?? 0) + 1) * tradePageSize, run.result.trades.length)} / {run.result.trades.length} · CSV 含完整结果</span>
              <button className="aq-btn aq-btn-icon" type="button" aria-label="上一页回放逐笔" title="上一页" disabled={(tradePages[run.run_id] ?? 0) === 0} onClick={() => setTradePages((current) => ({ ...current, [run.run_id]: Math.max(0, (current[run.run_id] ?? 0) - 1) }))}><ChevronLeft aria-hidden="true" /></button>
              <button className="aq-btn aq-btn-icon" type="button" aria-label="下一页回放逐笔" title="下一页" disabled={(tradePages[run.run_id] ?? 0) >= Math.ceil(run.result.trades.length / tradePageSize) - 1} onClick={() => setTradePages((current) => ({ ...current, [run.run_id]: Math.min(Math.ceil(run.result!.trades.length / tradePageSize) - 1, (current[run.run_id] ?? 0) + 1) }))}><ChevronRight aria-hidden="true" /></button>
            </div>
            <div className="aq-table-wrap aq-strategy-replay-trades-desktop"><table className="aq-table"><thead><tr><th>入场信号</th><th>买入日/价</th>{run.mode === "portfolio" ? <th className="aq-num">入场评分</th> : null}<th>退出信号</th><th>卖出日/价</th><th>状态</th><th className="aq-num">收益</th></tr></thead><tbody>{run.result.trades.slice((tradePages[run.run_id] ?? 0) * tradePageSize, ((tradePages[run.run_id] ?? 0) + 1) * tradePageSize).map((trade, index) => <tr key={`${trade.entry_signal_date}-${index}`}><td>{trade.entry_signal_date}</td><td>{trade.entry_date || "—"} / {trade.entry_price?.toFixed(2) ?? "—"}</td>{run.mode === "portfolio" ? <td className="aq-num">{trade.entry_score?.toFixed(4) ?? "—"}</td> : null}<td>{trade.exit_signal_date || trade.exit_reason || "—"}</td><td>{trade.exit_date || "—"} / {trade.exit_price?.toFixed(2) ?? "—"}</td><td>{strategyReplayTradeStatus(trade)}</td><td className="aq-num">{trade.return_pct == null ? "—" : `${trade.return_pct.toFixed(2)}%`}</td></tr>)}</tbody></table></div>
            <div className="aq-strategy-replay-trades-mobile">{run.result.trades.slice((tradePages[run.run_id] ?? 0) * tradePageSize, ((tradePages[run.run_id] ?? 0) + 1) * tradePageSize).map((trade, index) => <article className="aq-strategy-replay-trade-row" key={`${trade.entry_signal_date}-${index}`}>
              <header><strong>信号日 {trade.entry_signal_date}</strong><span>{strategyReplayTradeStatus(trade)}</span></header>
              <dl><div><dt>买入</dt><dd>{trade.entry_date || "—"} / {trade.entry_price?.toFixed(2) ?? "—"}</dd></div>{run.mode === "portfolio" ? <div><dt>入场评分</dt><dd>{trade.entry_score?.toFixed(4) ?? "—"}</dd></div> : null}<div><dt>退出信号</dt><dd>{trade.exit_signal_date || trade.exit_reason || "—"}</dd></div><div><dt>卖出</dt><dd>{trade.exit_date || "—"} / {trade.exit_price?.toFixed(2) ?? "—"}</dd></div><div><dt>收益</dt><dd>{trade.return_pct == null ? "—" : `${trade.return_pct.toFixed(2)}%`}</dd></div></dl>
            </article>)}</div>
          </> : <p className="aq-note">此区间没有 entry 触发。</p>}
        </> : null}
        <div className="aq-row-actions">{["starting", "running", "cancelling"].includes(run.status) ? <button className="aq-btn" type="button" onClick={() => void cancel(run)}><CircleStop aria-hidden="true" />取消</button> : null}{run.status === "completed" ? <><button className="aq-btn" type="button" onClick={() => void report(run)}><Download aria-hidden="true" />报告</button><button className="aq-btn" type="button" onClick={() => void api.downloadStrategyBacktestTrades(run.run_id).catch((reason) => setError(reason instanceof Error ? reason.message : "成交 CSV 导出失败"))}><Download aria-hidden="true" />逐笔 CSV</button></> : null}{!["starting", "running", "cancelling"].includes(run.status) ? <button className="aq-btn" type="button" onClick={() => void remove(run)}><Trash2 aria-hidden="true" />删除</button> : null}</div>
      </article>)}
    </div>}
  </section>;
}

function FactorResearch({
  candidates,
  snapshotLoading,
  snapshotError,
  history,
  dual,
  loading,
  error,
  reload,
  definitions,
  definitionsLoading,
  definitionsError,
  reloadDefinitions,
}: {
  candidates: NonNullable<ReturnType<typeof useWorkspaceSnapshot>["data"]>["candidates"];
  snapshotLoading: boolean;
  snapshotError: string | null;
  history: IcHistoryPayload | null;
  dual: IcDualVerdictPayload | null;
  loading: boolean;
  error: string;
  reload: () => void;
  definitions: FactorDefinitionsPayload | null;
  definitionsLoading: boolean;
  definitionsError: string;
  reloadDefinitions: () => void;
}) {
  const factors = candidates.flatMap((candidate) =>
    (candidate.technical_metrics ?? []).map((metric) => ({ symbol: candidate.symbol, name: candidate.display_name, score: candidate.score, ...metric })),
  );
  const latest = history?.points[history.points.length - 1];
  const dualFactors = Object.entries(dual?.latest?.factors ?? {}).sort(([left], [right]) => left.localeCompare(right));
  return (
    <>
      <FactorDraftWorkbench
        definitions={definitions}
        loading={definitionsLoading}
        error={definitionsError}
        reload={reloadDefinitions}
      />
      {error ? <StatePanel tone="warn">部分因子健康数据读取失败：{error}<button type="button" className="aq-btn" onClick={reload}><RefreshCw aria-hidden="true" />重试</button></StatePanel> : null}
      <section className="aq-section">
        <SectionHeader title="候选因子证据" description="来自当日冻结候选快照的技术指标与评分拆解；只读展示，不改正式打分。" count={`${factors.length} 项`} />
        {factors.length ? <div className="aq-table-wrap"><table className="aq-table"><thead><tr><th>标的</th><th>因子</th><th>观测值</th><th className="aq-num">候选评分</th></tr></thead><tbody>
          {factors.map((row, index) => <tr key={`${row.symbol}:${row.key}:${index}`}><td><Link to={`/stock/${row.symbol}`}>{row.name || row.symbol} <span className="aq-code">{row.symbol}</span></Link></td><td>{row.label || row.key}</td><td className="aq-num">{row.value}</td><td className="aq-num">{Number.isFinite(row.score) ? row.score.toFixed(1) : "—"}</td></tr>)}
        </tbody></table></div> : snapshotLoading ? <LoadingState label="读取候选因子快照…" /> : snapshotError ? <EmptyState title="候选因子快照不可用" detail="当前无法核对冻结候选的指标明细；不以估算值补齐。" /> : <EmptyState title="当前快照没有可展示的因子明细" detail="候选快照缺少技术指标字段时保持空缺，不用估算值补齐。" />}
        <div className="aq-row-actions"><Link className="aq-btn" to="/today#candidates"><SlidersHorizontal aria-hidden="true" />回到候选筛选</Link><Link className="aq-btn" to="/backtest"><BarChart3 aria-hidden="true" />查看正式验证</Link></div>
      </section>
      <section className="aq-section">
        <SectionHeader title="滚动 IC 历史" description="只读 runner 诊断产物；展示最新窗口和因子相关性，不作为自动调权依据。" count={`${history?.points.length ?? 0} 个窗口`} />
        {history?.readiness.status === "stale" || history?.readiness.status === "unverified" ? <StatePanel tone="warn">{history.readiness.reason}；下方历史值仅供回看，不代表本轮 runner 已产出。</StatePanel> : null}
        {history?.readiness.status === "missing" || history?.readiness.status === "empty" ? <StatePanel tone="warn">{history.readiness.reason}。</StatePanel> : null}
        {loading && !history ? <LoadingState label="读取 IC 历史…" /> : latest ? <>
          <p className="aq-note">窗口截至 {latest.as_of || "未标注"} · 计算于 {formatEvidenceTime(latest.run_at)} · 整体 {icEvidenceStatusLabel(history?.readiness.status || "unknown")} · 标记 {icEvidenceStatusLabel(history?.readiness.marker_status || "missing")} · 行情数据 {icEvidenceStatusLabel(history?.readiness.data_status || "unknown")}</p>
          <div className="aq-table-wrap"><table className="aq-table"><thead><tr><th>因子</th><th className="aq-num">IC 均值</th><th className="aq-num">t 值</th></tr></thead><tbody>
            {(Object.entries(latest.factors) as Array<[string, number | null]>).sort(([left], [right]) => left.localeCompare(right)).map(([name, value]) => { const tValue = latest.t?.[name]; return <tr key={name}><td>{name}</td><td className="aq-num">{value == null ? "—" : Number(value).toFixed(4)}</td><td className="aq-num">{tValue == null ? "—" : Number(tValue).toFixed(2)}</td></tr>; })}
          </tbody></table></div>
        </> : <EmptyState title="暂无 IC 历史产物" detail="runner 尚未回流单窗 IC 序列，不用候选分数替代 IC 结果。" />}
      </section>
      <section className="aq-section">
        <SectionHeader title="双窗稳定性复核" description="相邻不重叠窗口的只读判决；命中只提示人工复核研究方向，不自动改策略。" count={dual?.available ? `${dualFactors.length} 个因子` : "未就绪"} />
        {loading && !dual ? <LoadingState label="读取双窗判决…" /> : dual?.stale ? <StatePanel tone="warn">双窗产物已过期，保留最后结果供查看，不视为当前结论。</StatePanel> : dual?.available && dual.latest ? <>
          <p className="aq-note">窗口 A 截至 {dual.latest.as_of_a} · 窗口 B 截至 {dual.latest.as_of_b} · 连续达标门槛 {dual.streak_n} 个数据日{dual.latest.event === "revisit_family" ? " · 已触发换族流程复核提示" : ""}</p>
          <div className="aq-table-wrap"><table className="aq-table"><thead><tr><th>因子</th><th className="aq-num">窗口 A IC / t</th><th className="aq-num">窗口 B IC / t</th><th>判定</th><th className="aq-num">连续达标</th></tr></thead><tbody>
            {dualFactors.map(([name, row]) => <tr key={name}><td>{name}</td><td className="aq-num">{row.mean_a?.toFixed(4) ?? "—"} / {row.t_a?.toFixed(2) ?? "—"}</td><td className="aq-num">{row.mean_b?.toFixed(4) ?? "—"} / {row.t_b?.toFixed(2) ?? "—"}</td><td><Tag tone={row.hit ? "ok" : "neutral"}>{row.hit ? "双窗达标" : row.sign_match ? "同号未达显著" : "未稳定"}</Tag></td><td className="aq-num">{dual.latest?.streaks[name] ?? 0}</td></tr>)}
          </tbody></table></div>
        </> : <EmptyState title="双窗判决未就绪" detail="当前没有新鲜双窗产物；该监控为 proposal-only，不影响候选评分。" />}
      </section>
    </>
  );
}

const DEFAULT_FACTOR_EXPRESSION = "rank(close / ts_mean(close, 5))";
const DEFAULT_FACTOR_HYPOTHESIS = "个股收盘价相对自身近期均线的位置，是否能区分后续横截面收益。";
const FACTOR_TEMPLATES = [
  { label: "价格相对均线", expression: "rank(close / ts_mean(close, 20))" },
  { label: "成交量相对均量", expression: "rank(volume / ts_mean(volume, 20))" },
  { label: "五日价格变化", expression: "rank(ts_delta(close, 5) / ts_delay(close, 5))" },
] as const;
const FACTOR_FUNCTION_FAMILIES = ["时序", "截面", "算术", "条件"] as const;

function FactorDraftWorkbench({
  definitions,
  loading,
  error,
  reload,
}: {
  definitions: FactorDefinitionsPayload | null;
  loading: boolean;
  error: string;
  reload: () => void;
}) {
  const [factorId, setFactorId] = useState("");
  const [revision, setRevision] = useState<number | undefined>(undefined);
  const [name, setName] = useState("收盘相对均线位置");
  const [hypothesis, setHypothesis] = useState(DEFAULT_FACTOR_HYPOTHESIS);
  const [expression, setExpression] = useState(DEFAULT_FACTOR_EXPRESSION);
  const [symbolsText, setSymbolsText] = useState("");
  const [signalStart, setSignalStart] = useState("");
  const [signalEnd, setSignalEnd] = useState("");
  const [holdingDays, setHoldingDays] = useState(5);
  const [quantile, setQuantile] = useState(5);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [errorText, setErrorText] = useState("");
  const [trial, setTrial] = useState<FactorTrialResult | null>(null);
  const [activeDefinition, setActiveDefinition] = useState<FactorDefinition | null>(null);
  const [familyValidation, setFamilyValidation] = useState<FactorFamilyValidation | null>(null);
  const [catalog, setCatalog] = useState<FactorExpressionCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [functionFamily, setFunctionFamily] = useState<(typeof FACTOR_FUNCTION_FAMILIES)[number]>("时序");
  const [expressionCheck, setExpressionCheck] = useState<{ fields: string[]; max_lookback: number } | null>(null);
  const [checkingExpression, setCheckingExpression] = useState(false);
  const expressionRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let active = true;
    api.factorExpressionCatalog().then((result) => {
      if (active) setCatalog(result);
    }).catch((reason) => {
      if (active) setCatalogError(reason instanceof Error ? reason.message : "因子语法目录暂不可用");
    });
    return () => { active = false; };
  }, []);

  const selected = definitions?.items.find((item) => item.id === factorId) ?? activeDefinition;
  const latestVersion = selected?.versions[selected.versions.length - 1];
  const selectedTrial = trial ?? latestVersion?.trials[latestVersion.trials.length - 1] ?? null;
  const storedFamilyValidation = definitions?.validation_runs.find(
    (item) => item.target_trial_id === selectedTrial?.trial_id,
  ) ?? null;
  const visibleFamilyValidation = familyValidation?.target_trial_id === selectedTrial?.trial_id
    ? familyValidation
    : storedFamilyValidation;

  function updateExpression(value: string) {
    setExpression(value);
    setExpressionCheck(null);
  }

  function insertExpressionToken(token: string) {
    const editor = expressionRef.current;
    const start = editor?.selectionStart ?? expression.length;
    const end = editor?.selectionEnd ?? expression.length;
    const next = expression.slice(0, start) + token + expression.slice(end);
    updateExpression(next);
    requestAnimationFrame(() => {
      editor?.focus();
      editor?.setSelectionRange(start + token.length, start + token.length);
    });
  }

  function selectDraft(id: string) {
    setFactorId(id);
    const item = definitions?.items.find((candidate) => candidate.id === id);
    setActiveDefinition(item ?? null);
    setFamilyValidation(null);
    setRevision(item?.revision);
    if (!item) {
      setName("收盘相对均线位置");
      setHypothesis(DEFAULT_FACTOR_HYPOTHESIS);
      setExpression(DEFAULT_FACTOR_EXPRESSION);
      setExpressionCheck(null);
      setTrial(null);
      return;
    }
    const version = item?.versions[item.versions.length - 1];
    if (version) {
      setName(version.name);
      setHypothesis(version.hypothesis);
      setExpression(version.expression);
      setExpressionCheck(null);
      setTrial(version.trials[version.trials.length - 1] ?? null);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setErrorText("");
    setMessage("");
    try {
      const saved = await api.saveFactorDefinition({
        factor_id: factorId || undefined,
        expected_revision: revision,
        name,
        hypothesis,
        expression,
      });
      setFactorId(saved.id);
      setRevision(saved.revision);
      setActiveDefinition(saved);
      setTrial(null);
      setMessage(`草稿版本 ${saved.versions[saved.versions.length - 1]?.version ?? "—"} 已保存`);
      reload();
    } catch (reason) {
      setErrorText(reason instanceof Error ? reason.message : "因子草稿保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function checkExpression() {
    setCheckingExpression(true);
    setErrorText("");
    setExpressionCheck(null);
    try {
      const result = await api.checkFactorExpression(expression);
      setExpressionCheck(result);
    } catch (reason) {
      setErrorText(reason instanceof Error ? reason.message : "公式语法检查失败");
    } finally {
      setCheckingExpression(false);
    }
  }

  async function runTrial() {
    if (!selected || !latestVersion) return;
    setBusy(true);
    setErrorText("");
    setMessage("");
    try {
      const symbols = symbolsText.split(/[\s,，]+/).map((item) => item.trim()).filter(Boolean);
      const result = await api.runFactorTrial(selected.id, latestVersion.version, {
        symbols,
        signal_start: signalStart,
        signal_end: signalEnd,
        holding_days: holdingDays,
        quantile,
      });
      setTrial(result);
      setFamilyValidation(null);
      setMessage("试算已保存为诊断预览；不构成 OOS 验证或晋级");
      reload();
    } catch (reason) {
      setErrorText(reason instanceof Error ? reason.message : "因子试算失败");
    } finally {
      setBusy(false);
    }
  }

  async function validateFamily() {
    if (!selectedTrial) return;
    setBusy(true);
    setErrorText("");
    setMessage("");
    try {
      const result = await api.validateFactorFamily(selectedTrial.trial_id);
      setFamilyValidation(result);
      setMessage(result.status === "blocked" ? "因子族检验被阻断；具体原因见下方" : "Newey–West / BH-FDR 因子族诊断已保存");
      reload();
    } catch (reason) {
      setErrorText(reason instanceof Error ? reason.message : "因子族检验失败");
    } finally {
      setBusy(false);
    }
  }

  return <section className="aq-section">
    <SectionHeader title="因子公式草稿" description="公式按日截面与个股时序分别计算；保存会创建不可变公式版本，当前仅开放草稿和诊断试算。" count={definitions?.items.length ?? 0} />
    {error ? <StatePanel tone="warn">{error}<button type="button" className="aq-btn" onClick={reload}>重试读取</button></StatePanel> : null}
    {loading && !definitions ? <LoadingState label="读取因子草稿…" /> : null}
    <form className="aq-factor-draft" onSubmit={save}>
      <label>已有草稿<select className="aq-input aq-input-block" value={factorId} onChange={(event) => selectDraft(event.target.value)} disabled={busy}>
        <option value="">新建因子草稿</option>
        {(definitions?.items ?? []).map((item) => <option key={item.id} value={item.id}>{item.versions[item.versions.length - 1]?.name || item.id} · v{item.versions[item.versions.length - 1]?.version}</option>)}
      </select></label>
      <div className="aq-factor-draft-fields">
        <label>因子名称<input className="aq-input aq-input-block" value={name} maxLength={120} onChange={(event) => setName(event.target.value)} required disabled={busy} /></label>
        <label>研究假设<textarea className="aq-input aq-input-block" value={hypothesis} maxLength={500} rows={2} onChange={(event) => setHypothesis(event.target.value)} required disabled={busy} /></label>
      </div>
      <div className="aq-factor-formula-head">
        <label htmlFor="factor-expression">公式</label>
        <div className="aq-row-actions">
          <button type="button" className="aq-btn" onClick={checkExpression} disabled={busy || checkingExpression || !expression.trim()}>{checkingExpression ? "检查中…" : "检查语法"}</button>
          <button type="submit" className="aq-btn aq-btn-primary" disabled={busy}>保存草稿版本</button>
        </div>
      </div>
      <div className="aq-factor-editor-tools">
        <div className="aq-factor-editor-topline">
          <label>从模板开始<select className="aq-input" value="" onChange={(event) => {
            const template = FACTOR_TEMPLATES.find((item) => item.label === event.target.value);
            if (template) updateExpression(template.expression);
          }} disabled={busy}>
            <option value="">选择公式模板</option>
            {FACTOR_TEMPLATES.map((item) => <option key={item.label} value={item.label}>{item.label}</option>)}
          </select></label>
          <span>字段与算子均来自当前可运行的 raw OHLCV DSL</span>
        </div>
        {catalogError ? <p className="aq-factor-editor-error">算子目录不可用：{catalogError}</p> : null}
        {catalog ? <>
          <div className="aq-factor-editor-group">
            <span>字段</span>
            <div className="aq-factor-token-list">
              {catalog.fields.map((field) => <button key={field} type="button" className="aq-factor-token" onClick={() => insertExpressionToken(field)} disabled={busy} title={`插入 ${field}`}><code>{field}</code></button>)}
            </div>
          </div>
          <div className="aq-factor-editor-group">
            <span>算子</span>
            <div className="aq-factor-function-families" role="tablist" aria-label="算子类别">
              {FACTOR_FUNCTION_FAMILIES.map((family) => <button key={family} type="button" className={`aq-factor-family-tab${functionFamily === family ? " is-active" : ""}`} role="tab" aria-selected={functionFamily === family} onClick={() => setFunctionFamily(family)}>{family}</button>)}
            </div>
            <div className="aq-factor-function-list">
              {catalog.functions.filter((item) => item.family === functionFamily).map((item) => <button key={item.name} type="button" className="aq-factor-function" onClick={() => insertExpressionToken(item.example)} disabled={busy} title={`${item.signature} · ${item.description}`}><code>{item.name}</code><span>{item.signature}</span></button>)}
            </div>
          </div>
        </> : null}
      </div>
      <textarea ref={expressionRef} id="factor-expression" className="aq-input aq-factor-formula" value={expression} maxLength={1000} rows={4} onChange={(event) => updateExpression(event.target.value)} spellCheck={false} required disabled={busy} />
      {expressionCheck ? <p className="aq-factor-editor-valid">语法通过 · 字段 {expressionCheck.fields.join("、")} · 最大预热 {expressionCheck.max_lookback} 根日线</p> : null}
    </form>
    {message ? <p className="aq-note">{message}</p> : null}
    {errorText ? <StatePanel tone="warn">{errorText}</StatePanel> : null}
    {selected && latestVersion ? <div className="aq-factor-trial">
      <h3>本机历史试算 · v{latestVersion.version}</h3>
      <p className="aq-note">仅查询本机 raw 不复权 SQLite 日线，至少 20 个标的和 20 个有效截面日期。仅 OHLCV 字段；未绑定 PIT 财务因子，不替代 purged OOS/CSCV gate，也不用于调分。</p>
      <label>标的代码（20 到 200 个，空格或逗号分隔）<textarea className="aq-input aq-input-block" value={symbolsText} rows={2} onChange={(event) => setSymbolsText(event.target.value)} disabled={busy} /></label>
      <div className="aq-factor-trial-fields">
        <label>信号开始<input className="aq-input aq-input-block" type="date" value={signalStart} onChange={(event) => setSignalStart(event.target.value)} disabled={busy} /></label>
        <label>信号结束<input className="aq-input aq-input-block" type="date" value={signalEnd} onChange={(event) => setSignalEnd(event.target.value)} disabled={busy} /></label>
        <label>持有交易日<input className="aq-input aq-input-block" type="number" min={1} max={20} value={holdingDays} onChange={(event) => setHoldingDays(Number(event.target.value))} disabled={busy} /></label>
        <label>分层数<input className="aq-input aq-input-block" type="number" min={2} max={10} value={quantile} onChange={(event) => setQuantile(Number(event.target.value))} disabled={busy} /></label>
      </div>
      <div className="aq-row-actions">
        <button type="button" className="aq-btn" onClick={runTrial} disabled={busy || !signalStart || !signalEnd}>运行诊断试算</button>
        <button type="button" className="aq-btn" onClick={validateFamily} disabled={busy || !selectedTrial}>检验同样本因子族（NW / BH-FDR）</button>
      </div>
      {trial ? <div className="aq-total-grid"><div className="aq-total-card"><span>有效日期</span><b>{trial.effective_cross_section_dates}</b></div><div className="aq-total-card"><span>Spearman IC</span><b>{trial.metrics.ic_mean.toFixed(4)}</b></div><div className="aq-total-card"><span>ICIR</span><b>{trial.metrics.ic_ir.toFixed(3)}</b></div><div className="aq-total-card"><span>使用标的</span><b>{trial.used_symbols}/{trial.requested_symbols}</b></div></div> : null}
      {trial ? <p className="aq-note">来源 {trial.source} · raw · 信号窗 {trial.signal_start} 至 {trial.signal_end} · 持有 {trial.holding_days} 个有效信号会话 · 标的集 {trial.symbol_set_sha256.slice(0, 12)} · {factorTrialSampleLabel(trial)} · 仅诊断预览</p> : null}
      {trial?.blockers.length ? <StatePanel tone="warn">{trial.blockers.join("；")}</StatePanel> : null}
      {visibleFamilyValidation ? <div className="aq-factor-family-report">
        <header><h4>因子族统计诊断</h4><Badge tone={visibleFamilyValidation.status === "blocked" ? "warn" : "neutral"}>{visibleFamilyValidation.status === "blocked" ? "样本阻断" : "仅诊断"}</Badge></header>
        <p className="aq-note">同一标的集、区间、持有期、分层和 raw 样本下的已保存公式试算 · Newey–West lags={visibleFamilyValidation.newey_west_lags} · BH-FDR · family {visibleFamilyValidation.family_id.slice(0, 12)}</p>
        <p className="aq-note">不构成 OOS 证明，不改变因子状态或评分，也不授权晋级。</p>
        {visibleFamilyValidation.blockers.length ? <StatePanel tone="warn">{visibleFamilyValidation.blockers.join("；")}</StatePanel> : <div className="aq-factor-family-list">
          {visibleFamilyValidation.items.map((item) => <article className="aq-factor-family-row" key={item.expression_sha256}>
            <div><strong>{item.factor_name} · v{item.factor_version}</strong><code>{item.expression}</code></div>
            <dl><div><dt>均值 IC</dt><dd>{item.ic_mean.toFixed(4)}</dd></div><div><dt>NW t</dt><dd>{item.newey_west_t.toFixed(3)}</dd></div><div><dt>p 值</dt><dd>{item.p_value.toExponential(2)}</dd></div><div><dt>BH q 值</dt><dd>{item.q_value.toExponential(2)}</dd></div><div><dt>共同期数</dt><dd>{item.n_dates}</dd></div></dl>
          </article>)}
        </div>}
      </div> : null}
    </div> : null}
  </section>;
}

const VARIANT_REVIEW_CHOICES: Record<VariantReviewChoice, string> = {
  continue_validation: "继续验证",
  defer: "暂缓",
  reject: "不采用该变体",
};

function VariantDecisionControl({
  artifact,
  disabled,
  reload,
}: {
  artifact: VariantReviewArtifact | undefined;
  disabled: boolean;
  reload: () => void;
}) {
  const [choice, setChoice] = useState<VariantReviewChoice>(artifact?.latest_decision?.choice ?? "continue_validation");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setChoice(artifact?.latest_decision?.choice ?? "continue_validation");
  }, [artifact?.latest_decision?.id, artifact?.latest_decision?.choice]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!artifact) return;
    setBusy(true);
    setError("");
    try {
      await api.saveVariantReview({
        artifact_id: artifact.artifact_id,
        batch_id: artifact.batch_id,
        variant_id: artifact.variant_id,
        choice,
        note,
        expected_revision: artifact.revision,
      });
      setNote("");
      reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "个人复核记录保存失败");
    } finally {
      setBusy(false);
    }
  }

  return <div className="aq-variant-review">
    {artifact ? <p className="aq-note">绑定快照 {artifact.artifact_id.slice(0, 12)} · {formatEvidenceTime(artifact.generated_at)}</p> : null}
    {artifact?.latest_decision ? <p className="aq-variant-review-current"><Tag tone="neutral">{VARIANT_REVIEW_CHOICES[artifact.latest_decision.choice]}</Tag><span>{artifact.latest_decision.note} · {formatEvidenceTime(artifact.latest_decision.created_at)}</span></p> : <p className="aq-note">尚无个人复核决定</p>}
    <details>
      <summary>{artifact?.latest_decision ? "记录新的复核决定" : "记录个人复核决定"}</summary>
      <form className="aq-variant-review-form" onSubmit={save}>
        <p className="aq-note">复核绑定此变体快照；记录不会改变评分、运行权重或策略状态。</p>
        <label>我的处理<select className="aq-input" value={choice} onChange={(event) => setChoice(event.target.value as VariantReviewChoice)} disabled={disabled || busy}>
          {Object.entries(VARIANT_REVIEW_CHOICES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <label>理由<textarea className="aq-input" value={note} maxLength={800} rows={3} onChange={(event) => setNote(event.target.value)} placeholder="记录当前证据、缺口或下一步验证" disabled={disabled || busy} required /></label>
        {error ? <StatePanel tone="warn">{error}<button className="aq-btn" type="button" onClick={reload}>重新读取</button></StatePanel> : null}
        <button className="aq-btn aq-btn-primary" type="submit" disabled={disabled || busy || !note.trim()}>{busy ? "保存中…" : "保存复核"}</button>
      </form>
    </details>
  </div>;
}

function BacktestResearch({ snapshot, snapshotLoading, snapshotError, performance, loading, error, reload, variantReviews, variantReviewsLoading, variantReviewsError, reloadVariantReviews }: {
  snapshot: ReturnType<typeof useWorkspaceSnapshot>["data"];
  snapshotLoading: boolean;
  snapshotError: string;
  performance: PerformancePayload | null;
  loading: boolean;
  error: string;
  reload: () => void;
  variantReviews: VariantReviewsPayload | null;
  variantReviewsLoading: boolean;
  variantReviewsError: string;
  reloadVariantReviews: () => void;
}) {
  const variants = snapshot?.variants ?? [];
  const suite = snapshot?.variant_suite;
  return <>
    <UserStrategyBacktests />
    <UserBacktestRuns />
    <section className="aq-section">
      <SectionHeader title="正式信号验证" description="正式台账已结算信号；冷启动与台账新鲜度按原始状态显示。" icon={BookOpenCheck} />
      {loading ? <LoadingState label="读取正式信号表现…" /> : error ? <StatePanel tone="warn">{error}<button className="aq-btn" type="button" onClick={reload}><RefreshCw aria-hidden="true" />重试</button></StatePanel> : !performance?.available ? <EmptyState title="正式台账暂不可用于验证" detail={performance?.reason || "当前没有可用的已结算信号结果。"} /> : <>
        <div className="aq-total-grid">
          <div className="aq-total-card"><span>信号独立日</span><b>{performance.cold_start.independent_signal_days}</b></div>
          <div className="aq-total-card"><span>冷启动门槛</span><b>{performance.cold_start.min_independent_signal_days}</b></div>
          <div className="aq-total-card"><span>台账状态</span><b>{performance.freshness.stale ? "待更新" : "有新数据"}</b></div>
          <div className="aq-total-card"><span>统计时间</span><b>{performance.generated_at ? formatEvidenceTime(performance.generated_at) : "—"}</b></div>
        </div>
        {performance.strategies.length ? <>
          <div className="aq-table-wrap aq-strategy-performance-desktop"><table className="aq-table"><thead><tr><th>策略</th><th className="aq-num">独立信号日</th><th className="aq-num">已结算样本</th><th className="aq-num">命中率</th><th>成熟度</th></tr></thead><tbody>{performance.strategies.map((row) => <tr key={row.name}><td>{row.name}</td><td className="aq-num">{row.independent_signal_days}</td><td className="aq-num">{row.total_picks}</td><td className="aq-num">{performance.freshness.stale ? "仅历史" : row.displayable ? `${(row.hit_rate * 100).toFixed(1)}%` : "样本不足"}</td><td><Tag tone={performance.freshness.stale ? "warn" : row.displayable ? "ok" : "neutral"}>{performance.freshness.stale ? "台账已停滞" : row.displayable ? "可展示" : "积累中"}</Tag></td></tr>)}</tbody></table></div>
          <div className="aq-strategy-performance-mobile" aria-label="正式信号策略表现">
            {performance.strategies.map((row) => <article className="aq-strategy-performance-row" key={row.name}>
              <header><strong>{row.name}</strong><Tag tone={performance.freshness.stale ? "warn" : row.displayable ? "ok" : "neutral"}>{performance.freshness.stale ? "台账已停滞" : row.displayable ? "可展示" : "积累中"}</Tag></header>
              <dl>
                <div><dt>独立信号日</dt><dd>{row.independent_signal_days}</dd></div>
                <div><dt>已结算样本</dt><dd>{row.total_picks}</dd></div>
                <div><dt>命中率</dt><dd>{performance.freshness.stale ? "仅历史" : row.displayable ? `${(row.hit_rate * 100).toFixed(1)}%` : "样本不足"}</dd></div>
              </dl>
            </article>)}
          </div>
        </> : null}
      </>}
    </section>
    <section className="aq-section">
      <SectionHeader title="研究变体" description="独立纸面账户的样本外验证记录，与正式持仓和成交账本分开。表中研究分层由实验样本、收益和排名自动生成，只影响下一代研究候选，不是人工晋级或正式策略启停。" count={snapshotError ? "快照不可用" : variantSuiteLabel(suite)} />
      {snapshotLoading && !snapshot ? <LoadingState label="读取研究变体快照…" /> : snapshotError ? <StatePanel tone="warn">研究变体状态未知：快照不可用；正式台账结果仍可独立查看。</StatePanel> : suite ? <>
        <div className="aq-total-grid"><div className="aq-total-card"><span>套件更新时间</span><b>{suite.generated_at ? formatEvidenceTime(suite.generated_at) : "—"}</b></div><div className="aq-total-card"><span>数据截至</span><b>{suite.end_date || "—"}</b></div><div className="aq-total-card"><span>变体 / 批次</span><b>{suite.variant_count} / {suite.batch_id || "—"}</b></div><div className="aq-total-card"><span>标的覆盖</span><b>{suite.selected_symbols} / {suite.supported_symbols} · {(suite.coverage_pct * 100).toFixed(1)}%</b></div></div>
        <VariantRefreshPanel suite={suite} />
        {suite.last_error ? <StatePanel tone="warn">最近错误：{suite.last_error}</StatePanel> : null}
        {suite.batch_active ? <p className="aq-note">快照记录批次仍在运行；以下行只代表当前已落盘结果，不代表批次已经完成。</p> : null}
        {variantReviewsError ? <StatePanel tone="warn">个人复核记录暂不可用：{variantReviewsError}<button className="aq-btn" type="button" onClick={reloadVariantReviews}><RefreshCw aria-hidden="true" />重试</button></StatePanel> : null}
        {variantReviews && variantReviews.batch_id !== suite.batch_id ? <StatePanel tone="warn">当前显示批次与服务端最新批次不一致；此批次保持只读，请刷新研究快照。</StatePanel> : null}
      </> : <EmptyState title="没有研究变体批次快照" detail="当前只显示已落盘的验证结果；快照缺失时不推测任务状态。" />}
      {snapshot && variants.length ? <div className="aq-variant-review-list">{variants.map((row) => {
        const artifact = variantReviews?.artifacts.find((item) => item.variant_id === row.variant_id && item.batch_id === suite?.batch_id);
        const currentBatch = Boolean(artifact && variantReviews?.batch_id === suite?.batch_id);
        return <article className="aq-variant-review-item" key={row.variant_id}>
          <header><div><h3>{row.label || row.variant_id}</h3><span className="aq-code">{row.variant_id}</span></div><Tag>{row.lifecycle_status || row.data_mode}</Tag></header>
          <p className="aq-variant-review-strategy">{row.strategy || "未标注策略"}</p>
          <dl className="aq-variant-review-metrics">
            <div><dt>区间</dt><dd>{row.start_date || "—"} 至 {row.end_date || "—"}</dd></div>
            <div><dt>收益</dt><dd>{Number.isFinite(row.return_pct) ? `${row.return_pct.toFixed(2)}%` : "—"}</dd></div>
            <div><dt>独立信号日</dt><dd>{row.independent_signal_days ?? "—"}</dd></div>
          </dl>
          {row.lifecycle_reason ? <p className="aq-note">自动分层原因：{row.lifecycle_reason}</p> : null}
          <p className="aq-note">自动实验分层只用于研究候选，不代表人工晋级或正式策略启停。</p>
          {variantReviewsLoading ? <LoadingState label="读取个人复核…" /> : <VariantDecisionControl artifact={artifact} disabled={!currentBatch || Boolean(variantReviewsError)} reload={reloadVariantReviews} />}
        </article>;
      })}</div> : snapshot && !snapshotError && !snapshotLoading ? <EmptyState title="当前快照没有研究变体" detail="变体结果未产出时不以正式台账或真实持仓填充。" /> : null}
    </section>
  </>;
}

export function ResearchWorkbenchPage({ mode }: { mode: ResearchMode }) {
  const location = useLocation();
  const snapshotState = useWorkspaceSnapshot();
  const [performance, setPerformance] = useState<PerformancePayload | null>(null);
  const [icHistory, setIcHistory] = useState<IcHistoryPayload | null>(null);
  const [icDual, setIcDual] = useState<IcDualVerdictPayload | null>(null);
  const [factorDefinitions, setFactorDefinitions] = useState<FactorDefinitionsPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [factorLoading, setFactorLoading] = useState(false);
  const [error, setError] = useState("");
  const [factorError, setFactorError] = useState("");
  const [factorDefinitionsLoading, setFactorDefinitionsLoading] = useState(false);
  const [factorDefinitionsError, setFactorDefinitionsError] = useState("");
  const [variantReviews, setVariantReviews] = useState<VariantReviewsPayload | null>(null);
  const [variantReviewsLoading, setVariantReviewsLoading] = useState(false);
  const [variantReviewsError, setVariantReviewsError] = useState("");
  const [variantReviewReloadKey, setVariantReviewReloadKey] = useState(0);
  const activeMode: ResearchMode = location.pathname === "/backtest" ? "backtest" : mode;
  const candidates = useMemo(() => snapshotState.data?.candidates ?? [], [snapshotState.data]);

  useEffect(() => {
    if (activeMode === "factors") {
      let current = true;
      setFactorLoading(true);
      setFactorDefinitionsLoading(true);
      setFactorError("");
      Promise.allSettled([api.icHistory(), api.icDualVerdict(), api.factorDefinitions()]).then(([historyResult, dualResult, definitionsResult]) => {
        if (!current) return;
        const errors: string[] = [];
        if (historyResult.status === "fulfilled") setIcHistory(historyResult.value);
        else errors.push(historyResult.reason instanceof Error ? historyResult.reason.message : "IC 历史读取失败");
        if (dualResult.status === "fulfilled") setIcDual(dualResult.value);
        else errors.push(dualResult.reason instanceof Error ? dualResult.reason.message : "双窗判决读取失败");
        if (definitionsResult.status === "fulfilled") setFactorDefinitions(definitionsResult.value);
        else setFactorDefinitionsError(definitionsResult.reason instanceof Error ? definitionsResult.reason.message : "因子草稿读取失败");
        setFactorError(errors.join("；"));
      }).finally(() => {
        if (current) {
          setFactorLoading(false);
          setFactorDefinitionsLoading(false);
        }
      });
      return () => { current = false; };
    }
    if (activeMode !== "backtest") return;
    let current = true;
    setLoading(true);
    setError("");
    api.performance().then((payload) => { if (current) setPerformance(payload); })
      .catch((reason) => { if (current) setError(reason instanceof Error ? reason.message : "正式台账读取失败"); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [activeMode]);

  useEffect(() => {
    if (activeMode !== "backtest") return;
    let current = true;
    setVariantReviewsLoading(true);
    setVariantReviewsError("");
    api.variantReviews().then((result) => { if (current) setVariantReviews(result); })
      .catch((reason) => { if (current) setVariantReviewsError(reason instanceof Error ? reason.message : "个人复核读取失败"); })
      .finally(() => { if (current) setVariantReviewsLoading(false); });
    return () => { current = false; };
  }, [activeMode, variantReviewReloadKey]);

  const reloadVariantReviews = () => setVariantReviewReloadKey((value) => value + 1);

  const reloadFactors = () => {
    setIcHistory(null);
    setIcDual(null);
    setFactorError("");
    setFactorLoading(true);
    Promise.allSettled([api.icHistory(), api.icDualVerdict()]).then(([historyResult, dualResult]) => {
      const errors: string[] = [];
      if (historyResult.status === "fulfilled") setIcHistory(historyResult.value);
      else errors.push(historyResult.reason instanceof Error ? historyResult.reason.message : "IC 历史读取失败");
      if (dualResult.status === "fulfilled") setIcDual(dualResult.value);
      else errors.push(dualResult.reason instanceof Error ? dualResult.reason.message : "双窗判决读取失败");
      setFactorError(errors.join("；"));
    }).finally(() => setFactorLoading(false));
  };

  const reloadFactorDefinitions = () => {
    setFactorDefinitionsError("");
    setFactorDefinitionsLoading(true);
    api.factorDefinitions().then(setFactorDefinitions)
      .catch((reason) => setFactorDefinitionsError(reason instanceof Error ? reason.message : "因子草稿读取失败"))
      .finally(() => setFactorDefinitionsLoading(false));
  };

  return <div className="aq-page">
    <header className="aq-page-head"><div><p className="aq-eyebrow"><FlaskConical aria-hidden="true" />AQSP · 研究验证</p><div className="aq-title-row"><h1>{activeMode === "factors" ? "因子" : "回测"}</h1>{snapshotState.data ? <strong>{snapshotState.data.selected_date}</strong> : null}</div><p className="aq-page-sub">沿用 AQSP 候选、正式信号台账与纸面研究变体；各类数据保持原领域口径。{snapshotState.data ? ` 快照生成 ${snapshotState.data.generated_at} · 行情源 ${snapshotState.data.source.effective || "未标注"} · 数据截至 ${snapshotState.data.source.latest_trade_date || "未标注"}` : ""}</p></div></header>
    <div className="aq-toolbar" role="tablist" aria-label="研究模块"><Link role="tab" aria-selected={activeMode === "factors"} className={cn("aq-btn", activeMode === "factors" && "aq-btn-primary")} to="/factors"><TableProperties aria-hidden="true" />因子依据</Link><Link role="tab" aria-selected={activeMode === "backtest"} className={cn("aq-btn", activeMode === "backtest" && "aq-btn-primary")} to="/backtest"><BarChart3 aria-hidden="true" />回测与验证</Link></div>
    {activeMode === "factors" ? <FactorResearch candidates={candidates} snapshotLoading={snapshotState.loading} snapshotError={snapshotState.error} history={icHistory} dual={icDual} loading={factorLoading} error={factorError} reload={reloadFactors} definitions={factorDefinitions} definitionsLoading={factorDefinitionsLoading} definitionsError={factorDefinitionsError} reloadDefinitions={reloadFactorDefinitions} /> : <BacktestResearch snapshot={snapshotState.data} snapshotLoading={snapshotState.loading} snapshotError={snapshotState.error ?? ""} performance={performance} loading={loading} error={error} reload={() => { setPerformance(null); setError(""); setLoading(true); api.performance().then(setPerformance).catch((reason) => setError(reason instanceof Error ? reason.message : "正式台账读取失败")).finally(() => setLoading(false)); }} variantReviews={variantReviews} variantReviewsLoading={variantReviewsLoading} variantReviewsError={variantReviewsError} reloadVariantReviews={reloadVariantReviews} />}
  </div>;
}
