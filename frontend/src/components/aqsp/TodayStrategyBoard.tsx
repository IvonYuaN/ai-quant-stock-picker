import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, type PortfolioData } from "@/lib/api";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { CANDIDATE_OBSERVATIONS_CHANGED_EVENT } from "@/lib/candidate-observation";
import { PORTFOLIO_CHANGED_EVENT } from "@/lib/operation";
import { formatEvidenceTime } from "@/lib/format";
import { scheduleHashTargetScroll } from "@/lib/hash-navigation";
import { buildTodayStrategy, readTodayStrategySources, type TodayStrategySources, type StrategyAttention } from "@/lib/today-strategy";
import { AlertInbox } from "./AlertInbox";
import { CandidateObservationList } from "./CandidateObservationList";

export function TodayStrategyBoard({ onPortfolio }: { onPortfolio?: (portfolio: PortfolioData | null) => void }) {
  const [sources, setSources] = useState<TodayStrategySources | null>(null);
  const [loading, setLoading] = useState(true);
  const active = useRef(false), inFlight = useRef(false);
  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true; setLoading(true);
    try { const next = await readTodayStrategySources(api); if (active.current) { setSources(next); onPortfolio?.(next.portfolio); } }
    finally { inFlight.current = false; if (active.current) setLoading(false); }
  }, [onPortfolio]);
  useEffect(() => {
    active.current = true;
    const stop = subscribeAlertRefresh(() => void load(), browserAlertRefreshSource());
    const sync = () => void load();
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, sync);
    window.addEventListener(CANDIDATE_OBSERVATIONS_CHANGED_EVENT, sync);
    return () => { active.current = false; stop(); window.removeEventListener(PORTFOLIO_CHANGED_EVENT, sync); window.removeEventListener(CANDIDATE_OBSERVATIONS_CHANGED_EVENT, sync); };
  }, [load]);
  const view = sources ? buildTodayStrategy(sources) : null;
  return <section id="strategy-followups" className="aq-section aq-today-strategy" aria-label="持续策略跟进">
    <header className="aq-today-strategy-head"><div><p className="aq-eyebrow">已有方案 · 持续跟进</p><h2>已有风险和方案，有什么需要重核</h2><p>这里只汇总已保存的观察和变化；到价表示核验报价，不代表买卖或成交。</p></div><button type="button" className="aq-btn" disabled={loading} onClick={() => void load()}>{loading ? "读取中…" : "更新跟进状态"}</button></header>
    {!view ? <p role="status">正在核对持仓、已选方案与候选观察…</p> : <>
      {view.issues.length ? <div className="aq-refresh-warning" role="status"><strong>部分跟进状态待确认</strong>{view.issues.map((issue) => <span key={issue}>{issue}</span>)}</div> : null}
      {view.items.length ? <div className="aq-strategy-attention-list">{view.items.map((item, index) => <StrategyAttentionItem item={item} index={index} key={item.key} />)}</div> : <p>{view.noHoldings ? "还没有持仓需要跟进。先从候选中选一只，让 AI 比较参与条件，再保存观察。" : "本次没有读到需优先展示的跟进事项；不代表没有风险。"}</p>}
      {view.total > 3 ? <p className="aq-note">优先展示 3 项，其余 {view.total - 3} 项可在下方完整记录中查看。</p> : null}
      {view.noHoldings ? <a className="aq-btn" href="#candidates">看候选，分析参与条件</a> : null}
      <p className="aq-note">新方案从候选研究进入；持仓策略可从“我的持仓”打开。这里不启动新的风险检查或 AI 分析。</p>
    </>}
  </section>;
}

/** The original controls only mount after disclosure; the default home has one read batch. */
export function StrategyRecordDetails() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  useEffect(() => { if (["#alerts", "#strategy-records"].includes(location.hash)) setOpen(true); }, [location.hash]);
  useEffect(() => {
    if (open && ["#alerts", "#strategy-records"].includes(location.hash)) return scheduleHashTargetScroll(location.hash);
  }, [open, location.hash]);
  return <details id="strategy-records" className="aq-workspace-fold aq-strategy-records" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>完整风险检查、处置与候选观察记录<span>需要时展开</span></summary>
    {open ? <div className="aq-workspace-fold-body"><AlertInbox /><CandidateObservationList /></div> : null}
  </details>;
}

export function StrategyAttentionItem({ item, index }: { item: StrategyAttention; index: number }) {
  return <article className="aq-strategy-attention" data-strategy-symbol={item.symbol} key={item.key}>
        <div className="aq-strategy-attention-number" aria-hidden="true">{String(index + 1).padStart(2, "0")}</div><div className="aq-strategy-attention-body">
          <div className="aq-strategy-attention-name"><h3>{item.name}</h3><span className="aq-code">{item.symbol}</span><span className="aq-note">{item.kind === "position" ? "我的持仓" : "候选观察"}</span></div>
          <strong>{item.title}</strong><p>{item.reason}</p><p className="aq-strategy-attention-next"><b>下一步</b>{item.next}</p>
          <small>{item.observedAt ? `记录时点 ${formatEvidenceTime(item.observedAt)}${item.quoteAsOf ? ` · 报价 ${formatEvidenceTime(item.quoteAsOf)}` : ""}；当前是否仍成立需重核。` : "尚无可核验的后台记录时点，进入后核对当前依据。"}</small>
        </div><Link className="aq-btn aq-btn-primary" to={item.href}>复核当前方案</Link>
      </article>;
}
