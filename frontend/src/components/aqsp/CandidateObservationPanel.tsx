import { StrategyReviewContextPanel } from "./StrategyReviewContextPanel";
import { CandidateConditionChecks, CandidateActionChecks } from "./CandidateConditionChecks";
import { candidateChecksView } from "@/lib/candidate-conditions";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, isAqspAbortError, type CandidateChoice, type CandidateDiscussion, type CandidateWorkspace, type PositionModelCatalog, type WatchComparator } from "@/lib/api";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { CANDIDATE_CHOICES, CANDIDATE_OBSERVATIONS_CHANGED_EVENT, candidateAdoptionInput, candidateAdoptionIssue, candidateDiscussionIssue, candidateDiscussionKey, candidateEvidenceRows, candidateObservationView, candidateTechnicalView, candidateWorkspaceIssue } from "@/lib/candidate-observation";
import { readLocalPreference, writeLocalPreference } from "@/lib/browser-session";
import { formatEvidenceTime } from "@/lib/format";
import { PORTFOLIO_CHANGED_EVENT } from "@/lib/operation";
import { positionPlanHref } from "@/lib/position-workspace";
import { WATCH_COMPARATORS } from "@/lib/strategy-watch";

const stages: Record<string, string> = { preparing: "正在核对当前候选与行情…", independent: "三个角色正在分别评估入场条件…", challenge: "正在相互核对机会、风险与缺证…", synthesis: "正在整理三条路和下一步条件…" };
const judgments = { investigate: "可以进一步核对", not_now: "当前不宜", insufficient: "依据待补" };

function TechnicalEvidence({ evidence }: { evidence: Record<string, unknown> }) {
  const view = candidateTechnicalView(evidence);
  const sourceTime = view.asOf ? `${formatEvidenceTime(view.asOf)}${view.precision === "day" ? "（日线）" : ""}` : "";
  return <details className="aq-detail-muted"><summary>{view.title}{sourceTime ? ` · 原行情 ${sourceTime}` : " · 待核验"}</summary>
    {view.facts.length ? <dl className="aq-research-kv">{view.facts.map((fact) => <div key={fact.key}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl> : null}
    <p>{view.detail}</p>{sourceTime ? <small>{view.source} · {sourceTime}</small> : null}
  </details>;
}

export function CandidateObservationPanel({ symbol, researchDate, historical = false }: { symbol: string; researchDate?: string | null; historical?: boolean }) {
  const location = useLocation();
  useEffect(() => { if (location.hash === "#candidate-observation") document.getElementById("candidate-observation")?.scrollIntoView({ block: "start" }); }, [symbol, location.hash]);
  return <CandidateObservationWorkspace key={symbol} symbol={symbol} researchDate={researchDate} historical={historical} />;
}

function CandidateObservationWorkspace({ symbol, researchDate, historical }: { symbol: string; researchDate?: string | null; historical: boolean }) {
  const [data, setData] = useState<CandidateWorkspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [refreshMessage, setRefreshMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [discussing, setDiscussing] = useState(false);
  const [stage, setStage] = useState("preparing");
  const [catalog, setCatalog] = useState<PositionModelCatalog | null>(null);
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [model, setModel] = useState("");
  const [selection, setSelection] = useState<{ choice: CandidateChoice; index: number | null } | null>(null);
  const [comparator, setComparator] = useState<WatchComparator | "">("");
  const [confirmedSource, setConfirmedSource] = useState("");
  const active = useRef(true), readFlight = useRef(false), mutation = useRef(false), catalogFlight = useRef(false);
  const generation = useRef(0), catalogIdentity = useRef("");
  const controller = useRef<AbortController | null>(null);
  const adoptionForm = useRef<HTMLFormElement>(null);
  const result = data?.discussion ?? null;
  const sourceKey = candidateDiscussionKey(result);
  const clearConfirmation = () => { setSelection(null); setComparator(""); setConfirmedSource(""); };
  useEffect(clearConfirmation, [sourceKey]);
  useEffect(() => { if (selection && adoptionForm.current) { adoptionForm.current.focus({ preventScroll: true }); adoptionForm.current.scrollIntoView({ block: "nearest" }); } }, [selection]);
  const accept = useCallback((next: CandidateWorkspace) => {
    if (next.symbol !== symbol) throw new Error("返回的候选身份不一致，请重新进入。");
    if (active.current) { generation.current++; setData(next); setError(""); setRefreshMessage(""); }
  }, [symbol]);
  const load = useCallback(async () => {
    if (readFlight.current || mutation.current) return;
    readFlight.current = true; setLoading(true);
    const version = ++generation.current;
    try { const next = await api.candidateObservation(symbol); if (active.current && generation.current === version) accept(next); }
    catch (reason) { if (active.current && generation.current === version) setError(reason instanceof Error ? reason.message : "候选观察读取失败"); }
    finally { readFlight.current = false; if (active.current) setLoading(false); }
  }, [symbol, accept]);
  async function loadModels(refresh = false) {
    if (catalogFlight.current) return;
    catalogFlight.current = true; setCatalogBusy(true); setCatalogError("");
    try {
      const next = await api.positionModels(refresh);
      if (active.current) {
        const changed = !!catalogIdentity.current && catalogIdentity.current !== next.catalog_id;
        catalogIdentity.current = next.catalog_id; setCatalog(next);
        setModel((previous) => previous && !changed ? previous : readLocalPreference(`aqsp-candidate-model:${next.catalog_id}`) || next.current_model);
      }
    } catch (reason) { if (active.current) setCatalogError(reason instanceof Error ? reason.message : "型号目录读取失败"); }
    finally { catalogFlight.current = false; if (active.current) setCatalogBusy(false); }
  }
  useEffect(() => {
    active.current = true;
    const stop = subscribeAlertRefresh(() => void load(), browserAlertRefreshSource());
    const sync = () => void load();
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, sync);
    void loadModels();
    return () => { active.current = false; generation.current++; controller.current?.abort(); stop(); window.removeEventListener(PORTFOLIO_CHANGED_EVENT, sync); };
  }, [load]);
  const workspaceIssue = data ? candidateWorkspaceIssue(data, researchDate, historical) : "";
  const analyzing = discussing || data?.analysis.status === "running";
  const selectedUnavailable = !!catalog && (catalog.directory_complete ?? catalog.listed !== null) && !!model && model !== catalog.current_model && !catalog.models.includes(model);
  const locked = busy || loading || !!error;
  const canRefreshSources = data?.ownership.status === "unowned" && !historical && !error;
  const currentIssue = data ? candidateDiscussionIssue(result, data) : "";
  const selectedAction = selection?.index != null ? result?.summary?.actions[selection.index] : null;
  const selectedReference = selectedAction?.price_ref ? result?.price_refs[selectedAction.price_ref] : null;
  const adoptionIssue = selection && data ? candidateAdoptionIssue(result, data, selection.choice, selection.index, comparator) : "";
  async function recover() {
    const version = ++generation.current;
    try { const next = await api.candidateObservation(symbol); if (active.current && generation.current === version) accept(next); }
    catch (reason) { if (active.current && generation.current === version) setError(reason instanceof Error ? reason.message : "尚不能读取保存结果，请手动刷新核对。"); }
  }
  async function refreshEvidence() {
    if (mutation.current || busy || loading || analyzing || historical) return;
    mutation.current = true; generation.current++; setBusy(true); setActionError(""); setRefreshMessage("");
    try {
      const next = await api.refreshCandidateEvidence(symbol);
      if (active.current) {
        accept(next); clearConfirmation();
        const state = next.source_refresh;
        setRefreshMessage(state?.issue || (state?.state === "updated" ? "已更新可取得的大盘和板块依据；缺失项仍为未知。" : state?.state === "cached" ? "当前使用有效缓存，已重新核对行情与来源时间。" : "本次来源未全部更新，缺失项仍为未知。"));
      }
    } catch (reason) { if (active.current) { setActionError(reason instanceof Error ? reason.message : "依据更新失败，请稍后重试"); await recover(); } }
    finally { mutation.current = false; if (active.current) setBusy(false); }
  }
  async function discuss() {
    if (!data || mutation.current || locked || workspaceIssue || analyzing || selectedUnavailable || catalogBusy) return;
    mutation.current = true; generation.current++; setBusy(true); setDiscussing(true); setActionError(""); clearConfirmation(); setStage("preparing");
    controller.current = new AbortController();
    try {
      const next = await api.candidateDiscussionStream(symbol, { expected_version: data.version, expected_evidence_version: data.evidence_version,
        ...(model ? { server_model: model } : {}) }, (nextStage) => { if (active.current) setStage(nextStage); }, controller.current.signal);
      if (active.current) {
        setData((previous) => previous ? { ...previous, discussion: next, analysis: { status: next.status, stage: "", run_id: null } } : previous);
        if (!next.summary) setActionError(next.summary_issue || "尚未形成有效汇总；保留已完成角色，可核对后重试。");
      }
    } catch (reason) { if (active.current) setActionError(isAqspAbortError(reason) ? "已停止等待；正在核对服务端最后状态。" : `${reason instanceof Error ? reason.message : "分析未完成"}。未自动重试，先核对已保存结果。`); }
    finally { controller.current = null; if (active.current) { setDiscussing(false); await recover(); } mutation.current = false; if (active.current) setBusy(false); }
  }
  async function adopt() {
    if (!data || !result || !selection || mutation.current || locked || workspaceIssue || analyzing || confirmedSource !== sourceKey) return;
    let input;
    try { input = candidateAdoptionInput(result, data, selection.choice, selection.index, comparator, crypto.randomUUID()); }
    catch (reason) { setActionError(reason instanceof Error ? reason.message : "当前条件不能采用"); return; }
    mutation.current = true; generation.current++; setBusy(true); setActionError("");
    try {
      const next = await api.adoptCandidateObservation(symbol, input);
      if (active.current) { accept(next); clearConfirmation(); window.dispatchEvent(new Event(CANDIDATE_OBSERVATIONS_CHANGED_EVENT)); }
    } catch (reason) { if (active.current) { setActionError(`${reason instanceof Error ? reason.message : "采用结果尚未确认"}。请核对刷新结果，未自动重复提交。`); clearConfirmation(); await recover(); } }
    finally { mutation.current = false; if (active.current) setBusy(false); }
  }
  async function stopWatch() {
    if (!data?.watch || data.watch.status !== "active" || mutation.current || locked || analyzing) return;
    mutation.current = true; generation.current++; setBusy(true); setActionError("");
    try {
      const next = await api.stopCandidateObservation(symbol, data.version, crypto.randomUUID());
      if (active.current) { accept(next); clearConfirmation(); window.dispatchEvent(new Event(CANDIDATE_OBSERVATIONS_CHANGED_EVENT)); }
    } catch (reason) { if (active.current) { setActionError(`${reason instanceof Error ? reason.message : "停止结果尚未确认"}。请刷新核对，未自动重复提交。`); await recover(); } }
    finally { mutation.current = false; if (active.current) setBusy(false); }
  }
  const planHref = data?.ownership.status === "owned" ? positionPlanHref(data.ownership.position_id, `/stock/${symbol}`) : null;
  const observation = data ? candidateObservationView(data) : null;
  const checks = data ? candidateChecksView(data) : null;
  const showObservationDetail = checks?.mode !== "registered" || !checks.items.length || ["needs_review", "evidence_unavailable"].includes(data?.observation?.state ?? "");
  const adoptedResult = !!result && data?.watch?.discussion_id === result.id;
  const evidence = data && !workspaceIssue ? <><div className="aq-candidate-current-evidence" aria-label="当前候选三维依据">{candidateEvidenceRows(data).map((row) => <div key={row.key}><b>{row.label}</b><p>{row.summary}</p><small>{row.source} · {formatEvidenceTime(row.asOf) || "时点待核对"}</small></div>)}</div><TechnicalEvidence evidence={data.evidence} /></> : null;
  function choose(choice: CandidateChoice, index: number | null) { setSelection({ choice, index }); setComparator(""); setConfirmedSource(""); setActionError(""); }
  function condition(row: NonNullable<CandidateDiscussion["summary"]>["actions"][number], index: number) {
    const reference = result?.price_refs[row.price_ref ?? ""];
    const routeReady = result?.summary?.routes.some((route) => route.choice === row.choice && route.judgment === "investigate");
    return <div key={index} className="aq-candidate-condition"><div className="aq-candidate-condition-price">{reference ? <strong>{reference.price.toFixed(2)} 元</strong> : <span>{row.choice === "defer" ? "不建立到价条件" : "价位依据待补"}</span>}</div>
      <p><b>先满足什么</b>{row.condition}</p><p><b>何时取消或重核</b>{row.invalidation}</p>
      {data && result ? <CandidateActionChecks action={row} result={result} workspace={data} /> : null}
      {reference ? <small>{reference.label} · {reference.source} · {formatEvidenceTime(reference.as_of)}</small> : null}
      {row.choice !== "defer" ? <button type="button" className="aq-btn" disabled={locked || analyzing || !!workspaceIssue || !!currentIssue || data?.new_entry_blocked || !reference || !routeReady} onClick={() => choose(row.choice, index)}>采用这项条件</button> : null}
      {row.choice !== "defer" && !routeReady ? <p className="aq-note">这条路线当前不宜或依据待补，补齐依据后重新分析再采用。</p> : null}
    </div>;
  }
  return <section id="candidate-observation" className="aq-candidate-observation" aria-label="候选入场条件与持续观察">
    <div className="aq-candidate-observation-head"><div><p className="aq-eyebrow">未持仓 · 先看参与条件</p><h2>还没买，先等什么？</h2></div><button type="button" className="aq-btn" disabled={busy || loading || analyzing} onClick={() => void (canRefreshSources ? refreshEvidence() : load())}>{busy ? "更新中…" : loading ? "读取中…" : error || !data ? "重试读取" : canRefreshSources ? "更新判断依据" : "更新当前状态"}</button></div>
    {refreshMessage ? <p role="status" className="aq-note">{refreshMessage}</p> : null}
    {!data && loading ? <p role="status">正在核对当前候选、行情与持仓身份…</p> : null}
    {error ? <p role="alert">{error}。{data ? "保留上次记录，先更新核对后再采用。" : "可以重试读取，研究摘要仍可查看。"}</p> : null}
    {data?.ownership.status === "owned" ? <div className="aq-candidate-owned"><h3>这只股票已有持仓</h3><p>从当前仓位出发分析持有、减仓或做 T。</p>{planHref ? <Link className="aq-btn aq-btn-primary" to={planHref}>查看我的持仓策略</Link> : <Link className="aq-btn" to="/my/holdings">返回持仓核对身份</Link>}</div> : data ? <>
      {workspaceIssue ? <p role="status" className="aq-refresh-warning">{workspaceIssue}{historical ? <Link className="aq-btn" to={`/stock/${symbol}#candidate-observation`}>切回当前研究</Link> : null}</p> : null}
      {data.watch && observation ? <article className={`aq-candidate-watch${observation.tone === "warn" ? " aq-candidate-watch-warn" : ""}`} aria-label="当前候选观察"><div className="aq-candidate-watch-head"><h3>{observation.title}</h3><span>{CANDIDATE_CHOICES[data.watch.choice].title}</span></div>{showObservationDetail ? <p>{observation.detail}</p> : null}
        {data.watch.price != null && data.watch.comparator ? <strong className="aq-candidate-watch-price">{WATCH_COMPARATORS[data.watch.comparator]} {data.watch.price.toFixed(2)} 元时复核</strong> : null}
        <CandidateConditionChecks workspace={data} />
        <details><summary>查看采用时的完整前提与取消依据</summary><p><b>核对前提：</b>{data.watch.condition}</p><p><b>取消依据：</b>{data.watch.invalidation}</p></details>
        {data.watch.source_ref ? <small>{data.watch.source_ref.label} · {data.watch.source_ref.source} · {formatEvidenceTime(data.watch.source_ref.as_of)}</small> : null}
        {observation.checkedAt ? <small>本次依据时点 {formatEvidenceTime(observation.checkedAt)} · 报价 {formatEvidenceTime(data.observation?.quote_as_of)}</small> : null}
        {data.watch.status === "active" ? <><p className="aq-note">离开页面后仍继续检查已登记项目，变化时提醒复核。</p><button type="button" className="aq-btn" disabled={locked || analyzing} onClick={() => void stopWatch()}>停止候选观察</button></> : null}
      </article> : null}
      {data.watch ? <details><summary>查看当前大盘、板块和个股依据</summary>{evidence}</details> : evidence}
      {!data.watch ? <p className="aq-note">比较等待回落、等待确认和暂缓三条路。无需填写股数、费用或成交。</p> : null}
      <div className="aq-candidate-analysis-tools"><button type="button" className="aq-btn aq-btn-primary" disabled={locked || !!workspaceIssue || analyzing || catalogBusy || selectedUnavailable} onClick={() => void discuss()}>{analyzing ? "正在分析入场条件…" : data.watch?.status === "active" ? "AI 复核当前方案" : result ? "重新比较入场条件" : "AI 比较入场条件"}</button>
        <details className="aq-candidate-model"><summary>模型 · {model || "当前配置"}</summary><label>本设备使用的型号<select aria-label="候选分析模型型号" value={model} disabled={busy || catalogBusy} onChange={(event) => { setModel(event.target.value); if (catalog) writeLocalPreference(`aqsp-candidate-model:${catalog.catalog_id}`, event.target.value); }}>
          {!catalog ? <option value="">当前配置</option> : [...new Set([catalog.current_model, model, ...catalog.models])].filter(Boolean).map((name) => <option key={name} value={name}>{name}{name === catalog.current_model ? " · 当前默认" : ""}</option>)}
        </select></label><button type="button" className="aq-btn" disabled={busy || catalogBusy} onClick={() => void loadModels(true)}>{catalogBusy ? "读取中…" : "刷新可用型号"}</button>{selectedUnavailable ? <p role="status">此前型号已不在当前目录，请重新选择；未自动切换。</p> : catalog?.listed === false ? <p role="status">默认型号未在当前目录中，请核对型号设置。</p> : null}{catalogError || catalog?.issue ? <p role="status">{catalogError || catalog?.issue}</p> : null}<p className="aq-note">型号可能迭代；本次分析使用所选接口配置，不改变确定性评分。</p></details>
      </div>
      {data.entry_blocked ? <p role="status" className="aq-note">当前研究门禁阻塞。可以分析为什么等待或暂缓，暂不建立入场到价条件。</p> : null}
      {!data.entry_blocked && data.new_entry_blocked ? <p role="status" className="aq-note">{data.new_entry_block_reason || data.coverage_note || "当前依据不能建立新的入场条件。已有观察继续按原依据核对变化。"}</p> : null}
      {analyzing ? <div role="status"><p>{stages[discussing ? stage : data.analysis.stage] || "后台正在分析；刷新可恢复当前进度。"}</p>{discussing ? <button type="button" className="aq-btn" onClick={() => controller.current?.abort()}>停止等待本次分析</button> : null}</div> : null}
      {actionError ? <p role="alert">{actionError}</p> : null}
      {result ? <section className="aq-candidate-discussion" aria-label="AI 入场条件比较"><details key={`${result.id}:${adoptedResult}`} open={!adoptedResult}><summary>{adoptedResult ? "查看采用时的 AI 比较" : "查看 AI 入场条件比较"}</summary><p className="aq-note">{result.mode} · {result.model} · {formatEvidenceTime(result.generated_at)} · 研究日期 {result.research_date || "待核对"}</p>
        {currentIssue ? <p role="status">{data.watch?.discussion_id === result.id && data.watch.status === "active" && result.evidence_version === data.evidence_version ? "这份分析已用于当前观察；改变条件前请重新分析。" : `${currentIssue} 以下保留原分析供核对。`}</p> : null}
        {result.summary ? <><StrategyReviewContextPanel context={result.review_context} /><h3>为什么等，等到什么再考虑</h3><p className="aq-candidate-conclusion">{result.summary.conclusion}</p>{result.summary.focus_reason ? <p>{result.summary.focus_choice ? `可先核对${CANDIDATE_CHOICES[result.summary.focus_choice].title}：` : "暂不选定路线："}{result.summary.focus_reason}</p> : null}
          <div className="aq-candidate-route-grid">{result.summary.routes.map((route) => <article key={route.choice} className="aq-candidate-route"><div className="aq-candidate-route-title"><h4>{CANDIDATE_CHOICES[route.choice].title}</h4><small>{judgments[route.judgment]}</small></div><p>{route.reason}</p>{route.needs.length ? <p className="aq-note">还需：{route.needs.join("、")}</p> : null}
            {result.summary!.actions.map((row, index) => row.choice === route.choice ? condition(row, index) : null)}
            {route.choice === "defer" ? <button type="button" className="aq-btn" disabled={locked || analyzing || !!workspaceIssue || !!currentIssue} onClick={() => choose("defer", null)}>采用暂缓关注</button> : !result.summary!.actions.some((row) => row.choice === route.choice) ? <p className="aq-note">尚无可采用的价位条件，不用现价凑买点。</p> : null}
          </article>)}</div>
          {selection ? <form ref={adoptionForm} tabIndex={-1} className="aq-candidate-adopt" aria-label="确认候选观察条件" onSubmit={(event) => { event.preventDefault(); void adopt(); }}><h4>确认采用 · {CANDIDATE_CHOICES[selection.choice].title}</h4>{selectedAction && selectedReference ? <><p><b>{selectedReference.price.toFixed(2)} 元</b> · {selectedAction.condition}</p><p>取消或重核：{selectedAction.invalidation}</p><small>{selectedReference.label} · {selectedReference.source} · {formatEvidenceTime(selectedReference.as_of)}</small></> : null}
            {selection.choice !== "defer" ? <label>什么报价变化提醒我<select aria-label="候选观察价触发方向" value={comparator} disabled={busy} onChange={(event) => { setComparator(event.target.value as WatchComparator | ""); setConfirmedSource(""); }} required><option value="">请核对条件后选择</option><option value="at_or_above">达到或高于 {selectedReference?.price.toFixed(2)} 元</option><option value="at_or_below">达到或低于 {selectedReference?.price.toFixed(2)} 元</option></select></label> : <p>不建立到价提醒；保留暂缓的原因，等依据变化后再分析。</p>}
            <label className="aq-action-plan-confirm"><input type="checkbox" checked={!!sourceKey && confirmedSource === sourceKey} disabled={busy} onChange={(event) => setConfirmedSource(event.target.checked ? sourceKey : "")} />{selection.choice === "defer" ? "我确认暂缓，不建立到价提醒" : "我已核对这项条件、报价方向与取消依据"}</label>
            {adoptionIssue ? <p role="status">{adoptionIssue}</p> : null}<div className="aq-row-actions"><button type="submit" className="aq-btn aq-btn-primary" disabled={locked || analyzing || !!workspaceIssue || !!adoptionIssue || !sourceKey || confirmedSource !== sourceKey}>确认采用候选观察</button><button type="button" className="aq-btn" disabled={busy} onClick={clearConfirmation}>暂不采用</button></div>
          </form> : null}
          {result.summary.disagreements.length ? <p>仍有分歧：{result.summary.disagreements.join("；")}</p> : null}{result.summary.missing_evidence.length ? <p>还缺什么：{result.summary.missing_evidence.join("；")}</p> : null}
        </> : <p role="status">尚无通过核验的汇总条件。{result.summary_issue || "可查看已完成角色，核对缺证后重试。"}</p>}
        <details><summary>查看三角色讨论与证据</summary><TechnicalEvidence evidence={result.evidence_snapshot} />{result.rounds.map((round, index) => <div key={index}><h4>{round.stage === "independent" ? "独立分析" : "相互回应"}</h4>{round.opinions.map((row) => <article key={row.role}><b>{row.label}</b>{row.opinion ? <><p>{row.opinion.summary}</p>{row.opinion.challenge ? <p>回应：{row.opinion.challenge}</p> : null}{row.opinion.uncertainties.length ? <p>待核实：{row.opinion.uncertainties.join("；")}</p> : null}<small>引用：{row.opinion.evidence_ids.join("、")}</small></> : <p>{row.issue || "本角色未完成"}</p>}</article>)}</div>)}<p className="aq-note">{result.notice}</p></details>
      </details></section> : null}
    </> : null}
  </section>;
}
