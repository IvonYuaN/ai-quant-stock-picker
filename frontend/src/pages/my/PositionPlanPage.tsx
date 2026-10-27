import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { type ActionWorksheet, type PlanTarget, type ReviewChoice } from "@/lib/api";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { PLAN_CHOICES } from "@/lib/position-plan";
import { planReturnHref, readPlanWorksheet, savePlanChoice } from "@/lib/position-workspace";
import { ALERTS_CHANGED_EVENT, PORTFOLIO_CHANGED_EVENT, requestOperation } from "@/lib/operation";
import { formatEvidenceTime } from "@/lib/format";
import { LoadingState, StatePanel } from "@/components/ui/primitives";
import { PositionActionPlan } from "@/components/aqsp/PositionActionPlan";
import { PositionRouteComparison } from "@/components/aqsp/PositionRouteReview";
import { PositionConstraintsForm } from "@/components/aqsp/PositionConstraintsForm";

export function PositionPlanPage() {
  const { positionId = "" } = useParams<{ positionId: string }>();
  return <PositionPlanWorkspace key={positionId} positionId={positionId} />;
}

function PositionPlanWorkspace({ positionId }: { positionId: string }) {
  const [query] = useSearchParams();
  const back = planReturnHref(query.get("from"));
  const [worksheet, setWorksheet] = useState<ActionWorksheet | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [compareOpen, setCompareOpen] = useState(false);
  const generation = useRef(0);
  const pendingRead = useRef<number | null>(null);
  const mutation = useRef(false);
  const reloadPending = useRef(false);
  const active = useRef(true);
  const target: PlanTarget = { kind: "position", id: positionId };
  const accept = useCallback((next: ActionWorksheet) => {
    if (!active.current) return;
    generation.current++; pendingRead.current = null; setWorksheet(next); setLoading(false); setError("");
  }, []);
  const load = useCallback(async (): Promise<void> => {
    if (!positionId) return;
    if (pendingRead.current !== null || mutation.current) { reloadPending.current = true; return; }
    setLoading(true);
    const version = ++generation.current; pendingRead.current = version;
    try {
      const next = await readPlanWorksheet({ kind: "position", id: positionId });
      if (next.position_id !== positionId || next.position_snapshot.position_id !== positionId) throw new Error("返回的持仓身份与当前入口不一致，请返回持仓列表重新进入。");
      if (active.current && version === generation.current) { setWorksheet(next); setError(""); }
    } catch (reason) { if (active.current && version === generation.current) setError(reason instanceof Error ? reason.message : "处理计划读取失败"); }
    finally { if (pendingRead.current === version) pendingRead.current = null; if (active.current && version === generation.current) setLoading(false); if (active.current && reloadPending.current && pendingRead.current === null && !mutation.current) { reloadPending.current = false; void load(); } }
  }, [positionId]);
  useEffect(() => {
    active.current = true;
    const stop = subscribeAlertRefresh(() => void load(), browserAlertRefreshSource());
    const onPortfolio = () => void load();
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, onPortfolio);
    return () => { active.current = false; generation.current++; pendingRead.current = null; reloadPending.current = false; stop(); window.removeEventListener(PORTFOLIO_CHANGED_EVENT, onPortfolio); };
  }, [load]);
  async function choose(choice: ReviewChoice, basis: ActionWorksheet) {
    if (mutation.current || worksheet?.archived || loading || error) return;
    mutation.current = true; setBusy(true); setActionError("");
    generation.current++;
    try {
      const next = await savePlanChoice(target, basis, choice);
      accept(next); setCompareOpen(false);
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { if (active.current) setActionError(`${reason instanceof Error ? reason.message : "选择未能保存"}。请更新核对，原记录仍以服务器为准。`); }
    finally { mutation.current = false; if (active.current) { setBusy(false); if (reloadPending.current) { reloadPending.current = false; void load(); } } }
  }
  const choice = worksheet?.review_plan.choice;
  const readonly = !!worksheet?.archived;
  const unavailable = busy || loading || !!error || !worksheet?.revision;
  return <div className="aq-page aq-position-workspace">
    <header className="aq-page-head">
      <div><Link className="aq-research-back" to={back}><ArrowLeft aria-hidden="true" />返回{back.startsWith("/stock/") ? "个股研究" : back.startsWith("/today") ? "今日提醒" : "我的持仓"}</Link><p className="aq-eyebrow">{readonly ? "历史持仓" : "我的处理计划"}</p><h1>{worksheet?.name && worksheet.name !== worksheet.symbol ? worksheet.name : worksheet ? "股票名称待补" : "持仓处理计划"}</h1>{worksheet ? <span className="aq-code">{worksheet.symbol}</span> : null}</div>
      <button className="aq-btn" type="button" disabled={loading || busy} onClick={() => void load()}><RefreshCw aria-hidden="true" />{loading ? "更新中…" : "更新当前情况"}</button>
    </header>
    {loading && !worksheet ? <LoadingState label="正在核对这笔持仓与当前计划…" /> : null}
    {error ? <StatePanel tone="warn">{error}。{worksheet ? "保留上次内容，暂停操作；更新后再核对。" : "请重试，或返回持仓列表核对身份。"}<button className="aq-btn" type="button" disabled={loading} onClick={() => void load()}>重新读取计划</button></StatePanel> : null}
    {actionError ? <p role="alert" className="aq-refresh-warning">{actionError}</p> : null}
    {worksheet ? <>
      <dl className="aq-position-workspace-facts">{!readonly ? <div><dt>行情价格</dt><dd>{worksheet.basis.price?.toFixed(2) ?? "待更新"} 元</dd></div> : null}<div><dt>{readonly ? "历史记录成本" : "当前记录成本"}</dt><dd>{worksheet.basis.cost?.toFixed(4) ?? "待核对"} 元</dd></div><div><dt>当前股份</dt><dd>{worksheet.basis.shares ?? "待核对"} 股</dd></div><div><dt>{readonly ? "持仓状态" : "当前可卖"}</dt><dd>{readonly ? "已清仓" : `${worksheet.basis.sellable_shares ?? "待核对"} 股`}</dd></div></dl>
      {readonly ? <section className="aq-plan-history" aria-label="已清仓的历史计划"><h2>这笔持仓已清仓，只读保留</h2><p>重新买入会产生另一笔持仓，不能继承这份旧计划。</p>{choice ? <p>当时选择：{PLAN_CHOICES[choice].title}</p> : <p>当时没有记录处理方式。</p>}{worksheet.recorded_sale ? <p>{worksheet.recorded_sale.date} 已记录卖出 {worksheet.recorded_sale.shares} 股，成交价 {worksheet.recorded_sale.price.toFixed(2)} 元。</p> : null}{worksheet.constraints ? <details><summary>查看当时个人条件</summary><p>盯盘：{({ available: "能持续看盘", occasional: "只能偶尔看一眼", unavailable: "没有时间盯盘", unknown: "未确定" })[worksheet.constraints.attention]}；新增资金：{worksheet.constraints.extra_cash == null ? "未知" : `${worksheet.constraints.extra_cash} 元`}；新增亏损上限：{worksheet.constraints.loss_limit == null ? "未知" : `${worksheet.constraints.loss_limit} 元`}。</p></details> : null}{worksheet.review_plan.parameters ? <details><summary>查看当时试算参数</summary><p>数量 {worksheet.review_plan.parameters.quantity} 股；卖价 {worksheet.review_plan.parameters.sell_price} 元{worksheet.review_plan.parameters.buy_price == null ? "" : `；回补价 ${worksheet.review_plan.parameters.buy_price} 元`}。这些是假设，不代表实际成交。</p></details> : null}{(worksheet.comparison || worksheet.review_plan.discussion) ? <details><summary>查看当时 AI 分析</summary>{[worksheet.comparison, worksheet.review_plan.discussion].filter(Boolean).map((row, index) => <div key={index}><p>{formatEvidenceTime(row!.generated_at)} · {row!.model}</p><p>{row!.summary?.conclusion || "当时未形成有效汇总"}</p></div>)}</details> : null}</section> : <>
        <PositionConstraintsForm worksheet={worksheet} disabled={unavailable} onSaved={accept} />
        {choice ? <section className="aq-plan-current" aria-label="当前处理计划"><div className="aq-position-plan-heading"><h2>已选{PLAN_CHOICES[choice].title}</h2><span>选择不等于成交</span></div><PositionActionPlan key={choice} target={target} choice={choice} worksheet={worksheet} onWorksheet={accept} disabled={unavailable} /><details className="aq-change-route" open={compareOpen} onToggle={(event) => setCompareOpen(event.currentTarget.open)}><summary>重新比较或更换处理方式</summary>{compareOpen ? <PositionRouteComparison worksheet={worksheet} target={target} busy={unavailable} error={error} onChoose={(next, basis) => void choose(next, basis)} showModel={false} /> : null}</details></section> : <section aria-label="了解情况与比较方案"><PositionRouteComparison worksheet={worksheet} target={target} busy={unavailable} error={error} onChoose={(next, basis) => void choose(next, basis)} onModelComplete={() => void load()} /></section>}
        <footer className="aq-position-workspace-actions"><button type="button" className="aq-btn" disabled={unavailable} onClick={() => requestOperation({ direction: "sell", code: worksheet.symbol })}>我已实际卖出 · 录入成交</button><Link className="aq-btn" to={`/stock/${worksheet.symbol}`}>查看个股依据</Link><p className="aq-note">这里只跟进人工方案，真实交易仍在你的交易软件完成。</p></footer>
      </>}
    </> : null}
  </div>;
}
