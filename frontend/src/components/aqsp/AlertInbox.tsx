import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, BellRing, Check, RefreshCw, X } from "lucide-react";
import { Badge, LoadingState } from "@/components/ui/primitives";
import { api, type AlertData, type AlertEvent, type PortfolioData } from "@/lib/api";
import { ALERT_LEVEL_NAMES, formatAlertPrice, groupPositionAlerts, normalizeAlerts, type PositionAlertGroup } from "@/lib/alert-view";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { formatEvidenceTime } from "@/lib/format";
import { WatchlistActivity } from "./WatchlistActivity";
import { AlertEvidence } from "./AlertEvidence";
import { ALERTS_CHANGED_EVENT, PORTFOLIO_CHANGED_EVENT, requestOperation, shanghaiDate } from "@/lib/operation";

type ReviewChoice = NonNullable<AlertEvent["review_plan"]>["choice"];
const PLAN_LABELS: Record<ReviewChoice, string> = { hold: "持有观察", reduce: "减仓 / 退出", t_watch: "做 T 观察" };

function PositionAlert({ group, busy, onTransition, onPlan }: { group: PositionAlertGroup; busy: boolean; onTransition: (events: AlertEvent[], action: "handled" | "ignored") => void; onPlan: (event: AlertEvent, choice: ReviewChoice) => void }) {
  const { latest: event, holding } = group;
  const context = event.position_context;
  const plan = group.events.map((row) => row.review_plan).filter((value) => !!value).sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  const hardStop = group.events.some((row) => row.level === "L1") || context?.assessment.stance === "risk_review";
  const planText: Record<ReviewChoice, string> = {
    hold: "暂不改变仓位，保留风险提醒。继续持有需要重新核对买入逻辑和可承受损失；不能仅因已经亏损就取消原止损纪律。",
    reduce: "只有大盘、所属板块和个股在同一交易日都有可核验的走弱证据时，才优先复核减仓或退出；其他情况结合个股依据、可卖底仓与风险承受能力判断。实际成交后再回录数量。",
    t_watch: "先核对可卖底仓、日内量价和往返费用，再制定先卖后买的差价计划。当天新买数量受 T+1 限制；回补失败或继续下跌都可能扩大损失，做 T 不保证解套。",
  };
  return <article className="aq-position-alert" data-alert-symbol={group.symbol}>
    <header className="aq-position-alert-head"><div className="aq-alert-row-title"><Link to={`/stock/${group.symbol}`}><b>{group.name}</b></Link><span className="aq-code">{group.symbol}</span><Badge tone="warn">{hardStop ? "成本风险线已触发" : "风险待复核"}</Badge>{!group.sellable ? <Badge>T+1 观察</Badge> : null}</div><span className="aq-note">{group.events.length} 项触发 · {formatEvidenceTime(event.updated_at)}</span></header>
    <dl className="aq-position-alert-metrics">
      <div><dt>检查价</dt><dd>{formatAlertPrice(event.trigger_at)}</dd></div>
      <div><dt>持仓成本</dt><dd>{holding ? formatAlertPrice(holding.cost) : "—"}</dd></div>
      <div><dt>浮动盈亏</dt><dd>{(event.pnl_pct * 100).toFixed(1)}%</dd></div>
      <div><dt>持仓交易日</dt><dd>{context?.holding_trade_days == null ? "日期待补" : `${context.holding_trade_days} 天`}</dd></div>
    </dl>
    <div className="aq-position-assessment"><strong>{context?.assessment.title || (hardStop ? "成本风险线已越过，需重新评估仓位" : "价格或趋势触发风险条件")}</strong><p>{context?.assessment.reason || `${group.events.map((row) => ALERT_LEVEL_NAMES[row.level] || row.level).join("、")}已触发。规则越线说明风险增加，尚不能据此判断应全部卖出；需要结合行情与持仓计划复核。`}</p></div>
    <dl className="aq-position-context">{([ ["大盘", context?.market], ["板块", context?.sector], ["个股", context?.stock] ] as const).map(([label, evidence]) => <div key={label}><dt>{label}</dt><dd>{evidence?.summary || "分析依据待补，暂无法判断强弱"}<small>{evidence?.source ? `${evidence.source} · ${formatEvidenceTime(evidence.as_of) || "时间待核验"}` : "缺少可核验数据"}</small></dd></div>)}</dl>
    <AlertEvidence events={group.events} symbol={group.symbol} />
    <div className="aq-position-plan"><div className="aq-position-plan-heading"><b>我的处置方案</b><span>{plan ? "已记录，风险提醒仍保留" : "结合依据选择，不自动成交"}</span></div><div className="aq-position-plan-options" role="group" aria-label={`${group.symbol} 处置方案`}>{(Object.keys(PLAN_LABELS) as ReviewChoice[]).map((choice) => <button key={choice} type="button" className="aq-btn" disabled={busy} aria-pressed={plan?.choice === choice} onClick={() => onPlan(event, choice)}>{PLAN_LABELS[choice]}</button>)}</div>{plan ? <p className="aq-position-plan-note">{planText[plan.choice]}{plan.choice === "t_watch" ? ` ${context?.assessment.t_plan.reason || "当前缺少日内可执行证据，尚不能生成做 T 价位。"}` : ""}</p> : null}</div>
    <footer className="aq-position-alert-actions"><div>{group.sellable ? <button type="button" className="aq-btn" disabled={busy} onClick={() => requestOperation({ direction: "sell", code: group.symbol, alertId: event.id })}><ArrowUpRight aria-hidden="true" />回录已成交卖出</button> : <span className="aq-note">{event.t1_note || "当日新增数量不可卖"}</span>}{plan?.choice === "t_watch" ? <button type="button" className="aq-btn" disabled={busy} onClick={() => requestOperation({ direction: "buy", code: group.symbol })}>回录已成交回补</button> : null}</div><div><button type="button" className="aq-btn" disabled={busy} onClick={() => onTransition(group.events, "handled")}><Check aria-hidden="true" />已复核</button><button type="button" className="aq-btn aq-btn-icon" disabled={busy} title="忽略本仓位本次全部提醒" aria-label={`忽略 ${group.symbol} 提醒`} onClick={() => onTransition(group.events, "ignored")}><X aria-hidden="true" /></button></div></footer>
  </article>;
}

export function AlertInbox() {
  const [data, setData] = useState<AlertData | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [portfolioError, setPortfolioError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [checkNote, setCheckNote] = useState("");
  const [dispositionNote, setDispositionNote] = useState("");
  const generation = useRef(0);
  const mutation = useRef(false);
  const load = useCallback(async () => {
    const version = ++generation.current;
    setLoading(true);
    const [alerts, positions] = await Promise.allSettled([api.alerts(), api.portfolio()]);
    if (version !== generation.current) return;
    if (alerts.status === "fulfilled" && Array.isArray(alerts.value?.events)) { setData(normalizeAlerts(alerts.value)); setError(""); }
    else setError(alerts.status === "rejected" && alerts.reason instanceof Error ? alerts.reason.message : "提醒响应不完整，请重试");
    if (positions.status === "fulfilled") { setPortfolio(positions.value); setPortfolioError(""); }
    else setPortfolioError(positions.reason instanceof Error ? positions.reason.message : "持仓读取失败");
    setLoading(false);
  }, []);

  useEffect(() => {
    const stop = subscribeAlertRefresh(() => void load(), browserAlertRefreshSource());
    return () => { generation.current++; stop(); };
  }, [load]);

  const transition = async (events: AlertEvent[], action: "handled" | "ignored") => {
    if (mutation.current) return;
    mutation.current = true; setBusy(true); setDispositionNote(""); setActionError("");
    let completed = 0;
    try {
      const notes: string[] = [];
      for (const event of events) { const updated = await api.alertTransition(event.id, action); completed++; if (updated.frequency_note) notes.push(updated.frequency_note); }
      setDispositionNote([...new Set(notes)].join("；") || (action === "ignored" ? "已忽略本仓位本次提醒" : "已复核本仓位，未记录成交"));
    }
    catch (reason) { setActionError(`已确认 ${completed}/${events.length} 项处置，其余结果待确认：${reason instanceof Error ? reason.message : "提醒处置失败"}。请核对刷新后的剩余提醒。`); }
    finally { mutation.current = false; setBusy(false); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); window.dispatchEvent(new Event(PORTFOLIO_CHANGED_EVENT)); }
  };
  const savePlan = async (event: AlertEvent, choice: ReviewChoice) => {
    if (mutation.current) return;
    mutation.current = true; setBusy(true); setDispositionNote(""); setActionError("");
    try { await api.alertReviewPlan(event.id, choice); setDispositionNote(`已记录${PLAN_LABELS[choice]}方案；未改变持仓或风险规则`); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); }
    catch (reason) { setActionError(reason instanceof Error ? reason.message : "方案保存失败，请重试"); }
    finally { mutation.current = false; setBusy(false); }
  };
  const refreshRisk = async () => {
    if (mutation.current) return;
    mutation.current = true; setBusy(true); setActionError(""); setCheckNote("");
    try {
      const result = await api.runLiveRiskCheck(undefined, true);
      const coverage = result.checks?.skipped.length ? `本次有 ${result.checks.skipped.length} 项未能检查，请查看缺失依据。` : "检查已完成。";
      const delivery = result.notify.length ? result.notify.join("；") : result.notify_suppressed ? "本次未外发通知。" : "没有新增通知。";
      setCheckNote(`${coverage} ${delivery}`);
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { setActionError(`检查失败：${reason instanceof Error ? reason.message : "请稍后重试"}`); }
    finally { mutation.current = false; setBusy(false); }
  };

  const groups = data ? groupPositionAlerts(data, portfolio?.holdings) : [];
  const actionable = groups.filter((group) => group.sellable);
  const watch = groups.filter((group) => !group.sellable);
  const noHoldings = !!portfolio && portfolio.holdings.length === 0;
  const checkedDate = data?.last_checked_at ? new Date(data.last_checked_at) : null;
  const validChecked = checkedDate && Number.isFinite(checkedDate.getTime()) ? checkedDate : null;
  const checkedToday = validChecked ? shanghaiDate(validChecked) === shanghaiDate() : false;
  const history = data?.events.filter((event) => event.status !== "OPEN").slice().reverse().slice(0, 15) ?? [];
  return <section className="aq-risk-home" aria-label="我的持仓风险提醒" id="alerts">
    <header className="aq-risk-heading"><div><p className="aq-eyebrow"><BellRing aria-hidden="true" />我的持仓</p><h2>持仓风险与处置</h2><p>{data ? `${groups.length} 只待复核 · ${data.events.filter((event) => event.status === "OPEN").length} 项规则触发` : "正在读取提醒"}；先核对原因，再决定仓位。</p></div><button type="button" className="aq-btn" disabled={busy || loading || noHoldings || !!portfolioError} onClick={() => void refreshRisk()}><RefreshCw aria-hidden="true" />{busy ? "处理中…" : "检查持仓并通知"}</button></header>
    <div className="aq-risk-summary"><span>跟进 {portfolio?.holdings.length ?? "—"} 只</span>{watch.length ? <span>T+1 观察 {watch.length} 只</span> : null}<span>最近检查 {validChecked ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(validChecked) : "尚未检查"}</span></div>
    {dispositionNote ? <p className="aq-operation-note" role="status">{dispositionNote}</p> : null}
    {error || actionError || portfolioError ? <div className="aq-refresh-warning" role="alert"><strong>{actionError || error || `持仓读取失败：${portfolioError}`}</strong><span>当前检查状态待确认，请重试。</span><button type="button" className="aq-btn" disabled={busy} onClick={() => void load()}>重新读取</button></div> : null}
    {loading && !data ? <LoadingState label="正在读取持仓提醒…" /> : null}
    {noHoldings ? <div className="aq-risk-empty"><strong>先把手中的持仓交给工作台跟进</strong><p>录入股票、成交价格和数量后，自动关联风险规则；真实交易仍在你的交易软件完成。</p><button type="button" className="aq-btn aq-btn-primary" onClick={() => requestOperation()}>我操作了 · 录入持仓</button></div> : data && groups.length === 0 && !error && !portfolioError ? <div className="aq-risk-empty"><strong>{checkedToday ? "最近一次检查没有未处理提醒" : "持仓已就绪，今天尚未完成检查"}</strong><p>{checkedToday ? "无提醒不代表不存在风险，可在我的持仓中核对规则。" : "运行一次持仓检查，查看触发依据与 T+1 可执行状态。"}</p></div> : null}
    {[...actionable, ...watch].map((group) => <PositionAlert key={group.key} group={group} busy={busy} onTransition={(events, action) => void transition(events, action)} onPlan={(event, choice) => void savePlan(event, choice)} />)}
    {data?.last_check?.skipped.length ? <details className="aq-risk-incomplete"><summary>分析依据不完整 · {data.last_check.skipped.length} 项待补</summary>{data.last_check.skipped.map((item, index) => <p key={`${item.symbol}:${item.level}:${index}`}>{portfolio?.holdings.find((row) => row.code === item.symbol)?.name || item.symbol} · {item.level} · {item.reason}</p>)}</details> : null}
    <footer className="aq-risk-footer"><Link to="/my/holdings">持仓与风险规则 <ArrowUpRight aria-hidden="true" /></Link><span>L3 炸板未上线</span></footer>
    {checkNote ? <p className="aq-operation-note" role="status">{checkNote}</p> : null}
    {history.length ? <details className="aq-alert-history"><summary>已处理与忽略记录（最近 {history.length} 条）</summary>{history.map((event) => <p key={event.id}>{event.symbol} · {event.level} · {event.status === "HANDLED" ? "已处理" : event.status === "IGNORED" ? "已忽略" : "已失效"} · {event.updated_at}</p>)}</details> : null}
    <WatchlistActivity />
  </section>;
}
