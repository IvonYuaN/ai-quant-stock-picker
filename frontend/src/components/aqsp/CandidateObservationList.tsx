import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type CandidateWorkspace } from "@/lib/api";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { CANDIDATE_CHOICES, CANDIDATE_OBSERVATIONS_CHANGED_EVENT, candidateObservationView, candidateResearchHref, currentCandidateObservations } from "@/lib/candidate-observation";
import { formatEvidenceTime } from "@/lib/format";
import { PORTFOLIO_CHANGED_EVENT } from "@/lib/operation";
import { WATCH_COMPARATORS } from "@/lib/strategy-watch";

/** Current server observations stay independent of the selected research archive. */
export function CandidateObservationList() {
  const [items, setItems] = useState<CandidateWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const active = useRef(true), inFlight = useRef(false);
  const load = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true; setLoading(true);
    try { const next = await api.candidateObservations(); if (active.current) { setItems(next.items); setError(""); } }
    catch (reason) { if (active.current) setError(reason instanceof Error ? reason.message : "候选观察读取失败"); }
    finally { inFlight.current = false; if (active.current) setLoading(false); }
  }, []);
  useEffect(() => {
    active.current = true;
    const stop = subscribeAlertRefresh(() => void load(), browserAlertRefreshSource());
    const sync = () => void load();
    window.addEventListener(CANDIDATE_OBSERVATIONS_CHANGED_EVENT, sync);
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, sync);
    return () => { active.current = false; stop(); window.removeEventListener(CANDIDATE_OBSERVATIONS_CHANGED_EVENT, sync); window.removeEventListener(PORTFOLIO_CHANGED_EVENT, sync); };
  }, [load]);
  const current = currentCandidateObservations(items);
  function row(item: CandidateWorkspace) {
    const view = candidateObservationView(item), watch = item.watch!;
    return <article key={item.symbol} className={`aq-candidate-observation-row${view.tone === "warn" ? " aq-candidate-watch-warn" : ""}`}>
      <div className="aq-candidate-observation-row-head"><Link to={candidateResearchHref(item.symbol)}><strong>{item.name || item.symbol}</strong><span className="aq-code">{item.symbol}</span></Link><span>{CANDIDATE_CHOICES[watch.choice].title}</span></div>
      <h3>{view.title}</h3><p>{view.detail}</p>
      {watch.price != null && watch.comparator ? <p><b>{WATCH_COMPARATORS[watch.comparator]} {watch.price.toFixed(2)} 元</b> · 到价后仍需核对前提</p> : null}
      <small>{view.checkedAt ? `上次后台检查 ${formatEvidenceTime(view.checkedAt)}` : "尚待后台检查"} · 研究日期 {item.research_date || "待核对"}</small>
      <Link className="aq-btn" to={candidateResearchHref(item.symbol)}>核对条件与变化</Link>
    </article>;
  }
  return <section className="aq-candidate-observation-list" aria-label="我的候选观察">
    <div className="aq-candidate-observation-head"><div><p className="aq-eyebrow">离开页面也持续跟进</p><h2>我的候选观察{current.length ? <small> {current.length}</small> : null}</h2></div><button type="button" className="aq-btn" disabled={loading} onClick={() => void load()}>{loading ? "读取中…" : "更新观察"}</button></div>
    {error ? <p role="alert">{error}。保留上次记录；当前状态待重新核对。</p> : null}
    {!current.length ? <p className="aq-note">{loading ? "正在读取已保存的候选条件…" : "还没有正在观察的入场条件。从下方候选进入个股研究，让 AI 比较，再采用一项条件。"}</p> : <><p className="aq-note">这里是上次后台检查记录。到价只提示复核；进入个股研究可读取当前依据。</p><div className="aq-candidate-observation-rows">{current.slice(0, 5).map(row)}</div>{current.length > 5 ? <details><summary>其余 {current.length - 5} 项观察</summary><div className="aq-candidate-observation-rows">{current.slice(5).map(row)}</div></details> : null}</>}
  </section>;
}
