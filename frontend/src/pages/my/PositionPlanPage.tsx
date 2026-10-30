import { PageOutline } from "@/components/ui/PageOutline";
import { PositionSaleReview } from "@/components/aqsp/PositionSaleReview";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { api, type ActionWorksheet, type PlanTarget, type ReviewChoice } from "@/lib/api";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { PLAN_CHOICES } from "@/lib/position-plan";
import { planReturnHref, readPlanWorksheet, savePlanChoice } from "@/lib/position-workspace";
import { ALERTS_CHANGED_EVENT, PORTFOLIO_CHANGED_EVENT, requestOperation } from "@/lib/operation";
import { formatEvidenceTime } from "@/lib/format";
import { LoadingState, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { PositionActionPlan } from "@/components/aqsp/PositionActionPlan";
import { PositionRouteComparison } from "@/components/aqsp/PositionRouteReview";
import { PositionConstraintsForm } from "@/components/aqsp/PositionConstraintsForm";
import { PositionFollowupPanel } from "@/components/aqsp/PositionFollowupPanel";
import { TradeJournalPanel } from "@/components/aqsp/TradeJournalPanel";
import { PositionPricePathEvidence } from "@/components/aqsp/PositionPricePathEvidence";

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
  const acceptFollowup = useCallback((next: ActionWorksheet) => {
    if (!active.current) return;
    generation.current++; pendingRead.current = null;
    setWorksheet((previous) => previous && previous.position_id === next.position_id && previous.revision === next.revision ? { ...previous, followup: next.followup } : previous);
    setLoading(false);
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
  async function refreshEvidence() {
    if (mutation.current || worksheet?.archived) return;
    mutation.current = true; generation.current++; setBusy(true); setActionError("");
    try {
      const next = await api.refreshStrategyEvidence(positionId);
      if (next.position_id !== positionId) throw new Error("返回的持仓身份不一致，请重新进入");
      accept(next);
    } catch (reason) { if (active.current) setActionError(`${reason instanceof Error ? reason.message : "策略依据刷新失败"}。保留上次内容，缺失来源仍按未知处理。`); }
    finally { mutation.current = false; if (active.current) { setBusy(false); if (reloadPending.current) { reloadPending.current = false; void load(); } } }
  }
  const choice = worksheet?.review_plan.choice;
  const readonly = !!worksheet?.archived;
  const unavailable = busy || loading || !!error || !worksheet?.revision;
  return <div className="aq-page aq-position-workspace">
    <header className="aq-page-head">
      <div><Link className="aq-research-back" to={back}><ArrowLeft aria-hidden="true" />返回{back.startsWith("/stock/") ? "个股研究" : back.startsWith("/today") ? "今日提醒" : "我的持仓"}</Link><p className="aq-eyebrow">{readonly ? "历史持仓" : "持仓策略 · AI 分析 · 持续跟踪"}</p><h1>{worksheet?.name && worksheet.name !== worksheet.symbol ? worksheet.name : worksheet ? "股票名称待补" : "持仓处理计划"}</h1>{worksheet ? <span className="aq-code">{worksheet.symbol}</span> : null}</div>
      <button className="aq-btn" type="button" disabled={loading || busy} onClick={() => readonly ? void load() : void refreshEvidence()}><RefreshCw aria-hidden="true" />{loading || busy ? "更新中…" : readonly ? "更新历史记录" : "刷新策略依据"}</button>
    </header>
    {loading && !worksheet ? <LoadingState label="正在核对这笔持仓与当前计划…" /> : null}
    {error ? <StatePanel tone="warn">{error}。{worksheet ? "保留上次内容，暂停操作；更新后再核对。" : "请重试，或返回持仓列表核对身份。"}<button className="aq-btn" type="button" disabled={loading} onClick={() => void load()}>重新读取计划</button></StatePanel> : null}
    {actionError ? <p role="alert" className="aq-refresh-warning">{actionError}</p> : null}
    {worksheet ? <>
      <PageOutline items={[{ id: "position-situation", label: readonly ? "历史处境" : "现在怎样" }, ...(!readonly ? [{ id: "position-routes", label: "比较路线与 AI" }, { id: "position-followup", label: "持续跟进" }] : []), { id: "position-records", label: "成交记录" }]} />
      <section id="position-situation" className="aq-section aq-plan-module" aria-label="当前处境与证据">
        <SectionHeader title={readonly ? "这笔持仓的历史处境" : "1. 现在怎样"} description={readonly ? "只读保留当时的事实与选择。" : "先确认成本、数量、可卖份额和大盘 / 板块 / 个股依据，再看路线。"} />
        <dl className="aq-position-workspace-facts">{!readonly ? <div><dt>行情价格</dt><dd>{worksheet.basis.price?.toFixed(2) ?? "待更新"} 元</dd></div> : null}<div><dt>{readonly ? "历史记录成本" : "当前记录成本"}</dt><dd>{worksheet.basis.cost?.toFixed(4) ?? "待核对"} 元</dd></div><div><dt>当前股份</dt><dd>{worksheet.basis.shares ?? "待核对"} 股</dd></div><div><dt>{readonly ? "持仓状态" : "当前可卖"}</dt><dd>{readonly ? "已清仓" : `${worksheet.basis.sellable_shares ?? "待核对"} 股`}</dd></div></dl>
        {!readonly ? <p className="aq-note">当前页行情时点 {formatEvidenceTime(worksheet.basis.quote_as_of) || "待更新"}；后台检查记录单独显示，不把刷新时间当成成交或信号。</p> : null}
        {!readonly && worksheet.route_review ? <div className="aq-strategy-environment" aria-label="当前大盘板块与个股方向">{(["market", "sector", "stock"] as const).map((key) => { const row = worksheet.route_review!.evidence[key]; return <div key={key}><b>{({ market: "大盘方向", sector: "所属板块", stock: "这只股票" })[key]}</b><p>{row.summary}</p><small>{row.source || "来源待补"} · {formatEvidenceTime(row.as_of) || "时点待补"}</small></div>; })}</div> : null}
        {!readonly ? <PositionPricePathEvidence path={worksheet.route_review?.evidence.stock.price_path} /> : null}
      </section>
      {readonly ? <section className="aq-plan-history" aria-label="已清仓的历史计划"><h2>这笔持仓已清仓，只读保留</h2><p>重新买入会产生另一笔持仓，不能继承这份旧计划。</p>{choice ? <p>当时选择：{PLAN_CHOICES[choice].title}</p> : <p>当时没有记录处理方式。</p>}{worksheet.recorded_sale ? <p>{worksheet.recorded_sale.date} 已记录卖出 {worksheet.recorded_sale.shares} 股，成交价 {worksheet.recorded_sale.price.toFixed(2)} 元。</p> : null}{worksheet.constraints ? <details><summary>查看当时个人条件</summary><p>盯盘：{({ available: "能持续看盘", occasional: "只能偶尔看一眼", unavailable: "没有时间盯盘", unknown: "未确定" })[worksheet.constraints.attention]}；新增资金：{worksheet.constraints.extra_cash == null ? "未知" : `${worksheet.constraints.extra_cash} 元`}；新增亏损上限：{worksheet.constraints.loss_limit == null ? "未知" : `${worksheet.constraints.loss_limit} 元`}。</p></details> : null}{worksheet.review_plan.parameters ? <details><summary>查看当时试算参数</summary><p>数量 {worksheet.review_plan.parameters.quantity} 股；卖价 {worksheet.review_plan.parameters.sell_price} 元{worksheet.review_plan.parameters.buy_price == null ? "" : `；回补价 ${worksheet.review_plan.parameters.buy_price} 元`}。这些是假设，不代表实际成交。</p></details> : null}{(worksheet.comparison || worksheet.review_plan.discussion) ? <details><summary>查看当时 AI 分析</summary>{[worksheet.comparison, worksheet.review_plan.discussion].filter(Boolean).map((row, index) => <div key={index}><p>{formatEvidenceTime(row!.generated_at)} · {row!.model}</p><p>{row!.summary?.conclusion || "当时未形成有效汇总"}</p></div>)}</details> : null}</section> : <>
        <section id="position-routes" className="aq-section aq-plan-module" aria-label="处理路线">
          <SectionHeader title="2. 比较处理路线" description="持有观察、减仓退出、做 T 各自有代价；亏损深不自动等于卖出或做 T。" />
          <PositionConstraintsForm worksheet={worksheet} disabled={unavailable} onSaved={accept} />
          {!choice ? <PositionSaleReview worksheet={worksheet} disabled={unavailable} onWorksheet={accept} /> : null}
        {choice ? <section className="aq-plan-current" aria-label="当前处理计划"><div className="aq-position-plan-heading"><h2>已选{PLAN_CHOICES[choice].title}</h2><span>选择不等于成交</span></div><PositionActionPlan key={choice} target={target} choice={choice} worksheet={worksheet} onWorksheet={accept} disabled={unavailable} /><details className="aq-change-route" open={compareOpen} onToggle={(event) => setCompareOpen(event.currentTarget.open)}><summary>重新比较或更换处理方式</summary>{compareOpen ? <PositionRouteComparison worksheet={worksheet} target={target} busy={unavailable} error={error} onChoose={(next, basis) => void choose(next, basis)} showModel={false} /> : null}</details></section> : <section aria-label="了解情况与比较方案"><PositionRouteComparison worksheet={worksheet} target={target} busy={unavailable} error={error} onChoose={(next, basis) => void choose(next, basis)} onModelComplete={() => void load()} onWorksheet={accept} /></section>}
        </section>
        <section id="position-followup" className="aq-section aq-plan-module" aria-label="持续跟进">
          <SectionHeader title="3. 持续跟进" description="只在市场、板块、个股或已采用条件发生有意义变化时提醒。正常刷新不会重复要求选路。" />
          <PositionFollowupPanel key={positionId} worksheet={worksheet} disabled={unavailable} onSaved={acceptFollowup} />
        </section>
        <footer className="aq-position-workspace-actions"><Link className="aq-btn" to={`/stock/${worksheet.symbol}`}>核对选股与消息依据</Link><Link className="aq-btn" to="/today">查看今日候选与变化</Link></footer>
      </>}
      <details id="position-records" className="aq-plan-help aq-strategy-records"><summary>已经操作过？核对成交记录</summary>{!readonly ? <button type="button" className="aq-btn" disabled={unavailable} onClick={() => requestOperation({ direction: "sell", code: worksheet.symbol, positionId: worksheet.position_id })}>我已实际卖出 · 补记成交</button> : null}<TradeJournalPanel key={worksheet.symbol} code={worksheet.symbol} positionId={worksheet.position_id ?? ""} archived={readonly} /></details>
    </> : null}
  </div>;
}
