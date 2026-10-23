import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, BellRing, Check, Eye, RefreshCw, X } from "lucide-react";
import { Badge, LoadingState, Tag } from "@/components/ui/primitives";
import { api, type AlertData, type AlertEvent, type PortfolioData } from "@/lib/api";
import { buildAlertInbox, normalizeAlerts } from "@/lib/alert-view";
import { ALERTS_CHANGED_EVENT, requestOperation, shanghaiDate } from "@/lib/operation";

function AlertRow({ event, busy, onTransition }: { event: AlertEvent; busy: boolean; onTransition: (id: string, action: "handled" | "ignored") => void }) {
  return <article className="aq-alert-row">
    <div className="aq-alert-row-main">
      <div className="aq-alert-row-title"><Tag tone={event.sellable_now ? "warn" : "neutral"}>{event.level}</Tag><Link to={`/stock/${event.symbol}`}><b>{event.symbol}</b></Link><Badge tone={event.sellable_now ? "warn" : "neutral"}>{event.sellable_now ? "复核卖出" : "T+1 观察"}</Badge></div>
      <p className="aq-alert-basis">{event.basis_json.rule || "触发依据待补"}{event.basis_json.actual ? ` · ${event.basis_json.actual}` : ""}</p>
      <p className="aq-alert-basis aq-muted">触发价 {event.trigger_at} · 阈值 {event.threshold_at} · 盈亏 {(event.pnl_pct * 100).toFixed(1)}%{event.t1_note ? ` · ${event.t1_note}` : ""}</p>
      {event.basis_json.evidence ? <details className="aq-alert-evidence"><summary>查看依据</summary><p>{event.basis_json.evidence}</p></details> : null}
    </div>
    <div className="aq-alert-row-actions">
      {event.sellable_now ? <button type="button" className="aq-btn aq-btn-primary" disabled={busy} onClick={() => requestOperation({ direction: "sell", code: event.symbol, alertId: event.id })}><ArrowUpRight aria-hidden="true" />我已卖出</button> : null}
      <button type="button" className="aq-btn aq-btn-icon" disabled={busy} title="标记已处理（不记录成交）" aria-label={`标记 ${event.symbol} 已处理`} onClick={() => onTransition(event.id, "handled")}><Check aria-hidden="true" /></button>
      <button type="button" className="aq-btn aq-btn-icon" disabled={busy} title="忽略此提醒" aria-label={`忽略 ${event.symbol} 提醒`} onClick={() => onTransition(event.id, "ignored")}><X aria-hidden="true" /></button>
    </div>
  </article>;
}

export function AlertInbox() {
  const [data, setData] = useState<AlertData | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [error, setError] = useState("");
  const [portfolioError, setPortfolioError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [checkNote, setCheckNote] = useState("");
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
    void load(); window.addEventListener(ALERTS_CHANGED_EVENT, load);
    return () => { generation.current++; window.removeEventListener(ALERTS_CHANGED_EVENT, load); };
  }, [load]);

  const transition = async (id: string, action: "handled" | "ignored") => {
    if (mutation.current) return;
    mutation.current = true; setBusy(true);
    try { await api.alertTransition(id, action); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "提醒处置失败"); }
    finally { mutation.current = false; setBusy(false); }
  };
  const refreshRisk = async () => {
    if (mutation.current) return;
    mutation.current = true; setBusy(true); setError(""); setCheckNote("");
    try {
      const result = await api.runLiveRiskCheck(undefined, true);
      setCheckNote(result.checks?.skipped.length ? `本次有 ${result.checks.skipped.length} 项未能检查，请查看缺失依据。` : result.notify_suppressed ? "检查已完成，本次未外发通知" : "检查已完成，通知结果已记录");
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { setError(`检查失败：${reason instanceof Error ? reason.message : "请稍后重试"}`); }
    finally { mutation.current = false; setBusy(false); }
  };

  const view = data ? buildAlertInbox(data) : null;
  const noHoldings = !!portfolio && portfolio.holdings.length === 0;
  const checkedDate = data?.last_checked_at ? new Date(data.last_checked_at) : null;
  const validChecked = checkedDate && Number.isFinite(checkedDate.getTime()) ? checkedDate : null;
  const checkedToday = validChecked ? shanghaiDate(validChecked) === shanghaiDate() : false;
  const history = data?.events.filter((event) => event.status !== "OPEN").slice().reverse().slice(0, 15) ?? [];
  return <section className="aq-risk-home" aria-label="我的持仓风险提醒" id="alerts">
    <header className="aq-risk-heading"><div><p className="aq-eyebrow"><BellRing aria-hidden="true" />我的持仓 · LIVE 风控</p><h2>先处理持仓风险</h2><p>提醒依据当前持仓，独立于下方候选日期。</p></div><button type="button" className="aq-btn" disabled={busy || loading || noHoldings || !!portfolioError} onClick={() => void refreshRisk()}><RefreshCw aria-hidden="true" />{busy ? "处理中…" : "检查持仓并通知"}</button></header>
    <div className="aq-risk-counters"><div><span>需复核</span><strong>{view?.actionable.length ?? "—"}</strong></div><div><span>T+1 观察</span><strong>{view?.watch.length ?? "—"}</strong></div><div><span>跟进持仓</span><strong>{portfolio?.holdings.length ?? "—"}<small>只</small></strong></div><div className="aq-risk-checked"><span>最近检查</span><b>{validChecked ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(validChecked) : "尚未检查"}</b></div></div>
    {error || portfolioError ? <div className="aq-refresh-warning" role="alert"><strong>{error || `持仓读取失败：${portfolioError}`}</strong><span>当前检查状态待确认，请重试。</span><button type="button" className="aq-btn" disabled={busy} onClick={() => void load()}>重新读取</button></div> : null}
    {loading && !data ? <LoadingState label="正在读取持仓提醒…" /> : null}
    {noHoldings ? <div className="aq-risk-empty"><strong>先把手中的持仓交给工作台跟进</strong><p>录入股票、成交价格和数量后，自动关联风险规则；真实交易仍在你的交易软件完成。</p><button type="button" className="aq-btn aq-btn-primary" onClick={() => requestOperation()}>我操作了 · 录入持仓</button></div> : view?.openCount === 0 && !error && !portfolioError ? <div className="aq-risk-empty"><strong>{checkedToday ? "最近一次检查没有未处理提醒" : "持仓已就绪，今天尚未完成检查"}</strong><p>{checkedToday ? "无提醒不代表不存在风险，可在我的持仓中核对规则。" : "运行一次持仓检查，查看触发依据与 T+1 可执行状态。"}</p></div> : null}
    {view?.actionable.length ? <div className="aq-alert-group"><p className="aq-alert-group-label"><Eye aria-hidden="true" />今日可卖 · 复核后自行决定</p>{view.actionable.map((event) => <AlertRow key={event.id} event={event} busy={busy} onTransition={(id, action) => void transition(id, action)} />)}</div> : null}
    {view?.watch.length ? <div className="aq-alert-group"><p className="aq-alert-group-label">T+1 观察 · 当日新增数量不可卖</p>{view.watch.map((event) => <AlertRow key={event.id} event={event} busy={busy} onTransition={(id, action) => void transition(id, action)} />)}</div> : null}
    {data?.last_check?.skipped.length ? <div className="aq-risk-incomplete" role="status"><strong>检查不完整 · {data.last_check.skipped.length} 项待补</strong>{data.last_check.skipped.map((item, index) => <p key={`${item.symbol}:${item.level}:${index}`}>{item.symbol} {item.level} · {item.reason}</p>)}</div> : null}
    <footer className="aq-risk-footer"><Link to="/my/holdings">查看持仓与风险规则 <ArrowUpRight aria-hidden="true" /></Link><span>L1 硬止损 · L2 均线 · L4 MACD · L5 移动止损 · L3 炸板未上线</span></footer>
    {checkNote ? <p className="aq-operation-note" role="status">{checkNote}</p> : null}
    {history.length ? <details className="aq-alert-history"><summary>已处理与忽略记录（最近 {history.length} 条）</summary>{history.map((event) => <p key={event.id}>{event.symbol} · {event.level} · {event.status === "HANDLED" ? "已处理" : event.status === "IGNORED" ? "已忽略" : "已失效"} · {event.updated_at}</p>)}</details> : null}
  </section>;
}
