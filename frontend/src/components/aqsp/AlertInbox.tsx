import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, BellRing, Check, RefreshCw, X } from "lucide-react";
import { Badge, LoadingState } from "@/components/ui/primitives";
import { api, type AlertData, type AlertEvent, type PortfolioData } from "@/lib/api";
import { ALERT_LEVEL_NAMES, formatAlertPrice, groupPositionAlerts, normalizeAlerts, type PositionAlertGroup } from "@/lib/alert-view";
import { normalizePortfolio } from "@/lib/portfolio-view";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { formatEvidenceTime } from "@/lib/format";
import { WatchlistActivity } from "./WatchlistActivity";
import { PositionActionPlan } from "./PositionActionPlan";
import { PLAN_CHOICES } from "@/lib/position-plan";
import { AlertEvidence } from "./AlertEvidence";
import { ALERTS_CHANGED_EVENT, PORTFOLIO_CHANGED_EVENT, requestOperation, shanghaiDate } from "@/lib/operation";

type ReviewChoice = NonNullable<AlertEvent["review_plan"]>["choice"];

function PositionAlert({ group, busy, onTransition, onPlan }: { group: PositionAlertGroup; busy: boolean; onTransition: (events: AlertEvent[], action: "handled" | "ignored") => void; onPlan: (event: AlertEvent, choice: ReviewChoice) => void }) {
  const { latest: event, holding } = group;
  const context = event.position_context;
  const plan = group.events.map((row) => row.review_plan).filter((value) => !!value).sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
  const stopEvent = group.events.find((row) => row.level === "L1");
  const hardStop = group.events.some((row) => row.level === "L1") || context?.assessment.stance === "risk_review";
  const choices = <div className="aq-position-plan-options aq-beginner-options aq-route-choices" role="group" aria-label={`${group.symbol} 处置方案`}>{(Object.keys(PLAN_CHOICES) as ReviewChoice[]).map((choice) => <button key={choice} type="button" className="aq-btn" disabled={busy} aria-pressed={plan?.choice === choice} onClick={() => onPlan(event, choice)}><b>{PLAN_CHOICES[choice].title}</b><span>{PLAN_CHOICES[choice].benefit}</span><small>代价：{PLAN_CHOICES[choice].cost}</small></button>)}</div>;
  return <article className="aq-position-alert" data-alert-symbol={group.symbol}>
    <header className="aq-position-alert-head"><div className="aq-alert-row-title"><Link to={`/stock/${group.symbol}`}><b>{group.name}</b></Link><span className="aq-code">{group.symbol}</span><Badge tone="warn">{hardStop ? "风险线已触发" : "需要复核"}</Badge>{!group.sellable ? <Badge>T+1 观察</Badge> : null}</div><span className="aq-note">提醒更新 {formatEvidenceTime(event.updated_at)}</span></header>
    <dl className="aq-position-alert-metrics">
      <div><dt>检查时价格</dt><dd>{formatAlertPrice(event.trigger_at)}</dd></div>
      <div><dt>持仓成本</dt><dd>{holding ? formatAlertPrice(holding.cost) : "—"}</dd></div>
      <div><dt>检查时盈亏</dt><dd>{(event.pnl_pct * 100).toFixed(1)}%</dd></div>
      <div><dt>持有交易日</dt><dd>{context?.holding_trade_days == null ? "待补日期" : `${context.holding_trade_days} 天`}</dd></div>
    </dl>
    <div className="aq-position-assessment aq-beginner-assessment"><span className="aq-beginner-label">现在发生了什么</span><strong>{context?.assessment.title || (hardStop ? "价格跌破了你的风险线" : "价格或趋势触发了风险提醒")}</strong><p>{stopEvent ? `检查价已跌破${stopEvent.threshold_at > 0 ? " " + formatAlertPrice(stopEvent.threshold_at) + " 元" : "你设定的"}风险线。请先判断能否承受继续下跌，再选处理方式。` : context?.assessment.reason || `${group.events.map((row) => ALERT_LEVEL_NAMES[row.level] || row.level).join("、")}已触发，需要重新核对风险。`}</p><small>选择后可以随时改，不会自动成交。</small></div>
    <details className="aq-beginner-details"><summary>为什么会出现这条提醒</summary>{context?.assessment.reason ? <p className="aq-note">{context.assessment.reason}</p> : null}<dl className="aq-position-context">{([ ["大盘", context?.market], ["板块", context?.sector], ["个股", context?.stock] ] as const).map(([label, evidence]) => <div key={label}><dt>{label}</dt><dd>{evidence?.summary || "暂时没有足够数据"}<small>{evidence?.source ? `${evidence.source} · ${formatEvidenceTime(evidence.as_of) || "时间待核验"}` : "缺少可核验数据"}</small></dd></div>)}</dl><AlertEvidence events={group.events} symbol={group.symbol} /></details>
    <div className="aq-position-plan"><div className="aq-position-plan-heading"><b>{plan ? "你的跟进计划" : "三条路，各有什么代价"}</b>{plan ? <span>提醒仍保留</span> : null}</div>{plan ? <details className="aq-change-route" key={plan.choice}><summary>已选{PLAN_CHOICES[plan.choice].title} · 更换处理方式</summary>{choices}</details> : <><p className="aq-note">没有无代价的解套办法。先比较你愿意承担什么，再看对应步骤。</p>{choices}<p className="aq-note">{context?.assessment.t_plan.eligible === false ? `上次检查暂不具备做 T 条件：${context.assessment.t_plan.reason}` : "目前没有足够依据确认哪条路最适合你；做 T 还需核对走势、可卖数量和费用。"}</p></>}{plan ? <PositionActionPlan key={`${event.id}:${plan.choice}`} eventId={event.id} choice={plan.choice} /> : null}</div>
    <footer className="aq-position-alert-actions"><div>{group.sellable ? <button type="button" className="aq-btn" disabled={busy} onClick={() => requestOperation({ direction: "sell", code: group.symbol })}><ArrowUpRight aria-hidden="true" />回录已成交卖出</button> : <span className="aq-note">{event.t1_note || "当日新增数量不可卖"}</span>}</div><div><button type="button" className="aq-btn" disabled={busy} onClick={() => onTransition(group.events, "handled")}><Check aria-hidden="true" />已复核</button><button type="button" className="aq-btn aq-btn-icon" disabled={busy} title="忽略本仓位本次全部提醒" aria-label={`忽略 ${group.symbol} 提醒`} onClick={() => onTransition(group.events, "ignored")}><X aria-hidden="true" /></button></div></footer>
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
    if (positions.status === "fulfilled") { setPortfolio(normalizePortfolio(positions.value)); setPortfolioError(""); }
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
    try { await api.alertReviewPlan(event.id, choice); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); }
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
  const renderAlertGroup = (items: PositionAlertGroup[], title: string, id: string) => items.length ? <section className="aq-risk-group" aria-labelledby={id}><h3 id={id} className="aq-risk-group-heading">{title}<span>{items.length} 只</span></h3>{items.map((group) => <PositionAlert key={group.key} group={group} busy={busy} onTransition={(events, action) => void transition(events, action)} onPlan={(event, choice) => void savePlan(event, choice)} />)}</section> : null;
  return <section className="aq-risk-home" aria-label="我的持仓风险提醒" id="alerts">
    <header className="aq-risk-heading"><div><p className="aq-eyebrow"><BellRing aria-hidden="true" />我的持仓</p><h2>哪些持仓需要看一眼</h2><p>{data ? `${groups.length} 只持仓需要关注` : "正在读取提醒"}，点开一只，看看接下来怎么处理。</p></div><button type="button" className="aq-btn" disabled={busy || loading || noHoldings || !!portfolioError} onClick={() => void refreshRisk()}><RefreshCw aria-hidden="true" />{busy ? "处理中…" : "检查持仓并通知"}</button></header>
    <div className="aq-risk-summary"><span>跟进 {portfolio?.holdings.length ?? "—"} 只</span>{watch.length ? <span>T+1 观察 {watch.length} 只</span> : null}<span>最近检查 {validChecked ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(validChecked) : "尚未检查"}</span></div>
    {dispositionNote ? <p className="aq-operation-note" role="status">{dispositionNote}</p> : null}
    {error || actionError || portfolioError ? <div className="aq-refresh-warning" role="alert"><strong>{actionError || error || `持仓读取失败：${portfolioError}`}</strong><span>当前检查状态待确认，请重试。</span><button type="button" className="aq-btn" disabled={busy} onClick={() => void load()}>重新读取</button></div> : null}
    {loading && !data ? <LoadingState label="正在读取持仓提醒…" /> : null}
    {noHoldings ? <div className="aq-risk-empty"><strong>先把手中的持仓交给工作台跟进</strong><p>录入股票、成交价格和数量后，自动关联风险规则；真实交易仍在你的交易软件完成。</p><button type="button" className="aq-btn aq-btn-primary" onClick={() => requestOperation()}>我操作了 · 录入持仓</button></div> : data && groups.length === 0 && !error && !portfolioError ? <div className="aq-risk-empty"><strong>{checkedToday ? "最近一次检查没有未处理提醒" : "持仓已就绪，今天尚未完成检查"}</strong><p>{checkedToday ? "无提醒不代表不存在风险，可在我的持仓中核对规则。" : "运行一次持仓检查，查看触发依据与 T+1 可执行状态。"}</p></div> : null}
    {renderAlertGroup(actionable, "今天可卖 · 需要复核", "aq-risk-actionable-heading")}
    {renderAlertGroup(watch, "当日新增不可卖 · 先观察", "aq-risk-watch-heading")}
    {data?.last_check?.skipped.length ? <details className="aq-risk-incomplete"><summary>分析依据不完整 · {data.last_check.skipped.length} 项待补</summary>{data.last_check.skipped.map((item, index) => <p key={`${item.symbol}:${item.level}:${index}`}>{portfolio?.holdings.find((row) => row.code === item.symbol)?.name || item.symbol} · {item.level} · {item.reason}</p>)}</details> : null}
    <footer className="aq-risk-footer"><Link to="/my/holdings">持仓与风险规则 <ArrowUpRight aria-hidden="true" /></Link><details className="aq-risk-capability"><summary>能力范围</summary><span>L3 炸板提醒尚未上线</span></details></footer>
    {checkNote ? <p className="aq-operation-note" role="status">{checkNote}</p> : null}
    {history.length ? <details className="aq-alert-history"><summary>已处理与忽略记录（最近 {history.length} 条）</summary>{history.map((event) => <p key={event.id}>{event.symbol} · {event.level} · {event.status === "HANDLED" ? "已处理" : event.status === "IGNORED" ? "已忽略" : "已失效"} · {event.updated_at}</p>)}</details> : null}
    <WatchlistActivity />
  </section>;
}
