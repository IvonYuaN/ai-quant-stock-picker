import { PositionActionChecks } from "./PositionConditionChecks";
import { StrategyReviewContextPanel } from "./StrategyReviewContextPanel";
import { useEffect, useRef, useState } from "react";
import { api, isAqspAbortError, type ActionWorksheet, type DiscussionAction, type PositionDiscussion, type PositionModelCatalog, type PositionModelConfig, type PlanTarget, type ReviewChoice, type WatchComparator } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { worksheetGuard } from "@/lib/position-workspace";
import { PLAN_CHOICES } from "@/lib/position-plan";
import { discussionSourceKey, discussionSynthesisNotice, discussionStrategyCurrent, strategyWatchInput, strategyWatchIssue } from "@/lib/strategy-watch";
import { ALERTS_CHANGED_EVENT } from "@/lib/operation";

const actionLabels = { observe: "继续观察", sell_review: "卖出复核", buy_review: "回补复核", pause: "暂停操作" };

export function PositionDiscussionPanel({ eventId, target, choice, worksheet, disabled, disabledReason, onComplete, onWorksheet }: {
  eventId?: string; target?: PlanTarget; onComplete?: () => void; onWorksheet?: (next: ActionWorksheet) => void; choice: ReviewChoice | null; worksheet: ActionWorksheet; disabled: boolean; disabledReason?: string;
}) {
  const planTarget = target ?? { kind: "alert" as const, id: eventId ?? "" };
  const comparison = choice === null;
  const persisted = comparison ? worksheet.comparison : worksheet.review_plan.discussion;
  const [result, setResult] = useState<PositionDiscussion | null>(persisted ?? null);
  const [config, setConfig] = useState<PositionModelConfig>({ baseURL: "", model: "", apiKey: "" });
  const [custom, setCustom] = useState(false);
  const [catalog, setCatalog] = useState<PositionModelCatalog | null>(null);
  const [serverModel, setServerModel] = useState("");
  const [catalogBusy, setCatalogBusy] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState("preparing");
  const controller = useRef<AbortController | null>(null);
  const [error, setError] = useState("");
  const [selectedAction, setSelectedAction] = useState<number | null>(null);
  const [watchChoice, setWatchChoice] = useState<ReviewChoice | "">(choice ?? "");
  const [comparator, setComparator] = useState<WatchComparator | "">("");
  const [watchConfirmed, setWatchConfirmed] = useState("");
  const sourceKey = discussionSourceKey(result);
  const [adopting, setAdopting] = useState(false);
  const adoptFlight = useRef(false);
  const active = useRef(true);
  const inFlight = useRef(false);
  const catalogInFlight = useRef(false);
  const catalogIdentity = useRef("");
  useEffect(() => { active.current = true; return () => { active.current = false; controller.current?.abort(); }; }, []);
  useEffect(() => { if (!inFlight.current) setResult(persisted ?? null); }, [persisted]);
  useEffect(() => { setSelectedAction(null); setComparator(""); setWatchConfirmed(""); }, [sourceKey]);
  async function loadModels(refresh = false) {
    if (catalogInFlight.current) return;
    catalogInFlight.current = true; setCatalogBusy(true); setCatalogError("");
    try {
      const next = await api.positionModels(refresh);
      if (active.current) {
        const providerChanged = !!catalogIdentity.current && catalogIdentity.current !== next.catalog_id;
        catalogIdentity.current = next.catalog_id;
        setCatalog(next);
        setServerModel((previous) => {
          if (previous && !providerChanged) return previous;
          try { return localStorage.getItem(`aqsp-position-model:${next.catalog_id}`) || next.current_model; }
          catch { return next.current_model; }
        });
      }
    } catch (reason) { if (active.current) setCatalogError(reason instanceof Error ? reason.message : "型号目录读取失败，仍可按当前配置尝试分析。"); }
    finally { catalogInFlight.current = false; if (active.current) setCatalogBusy(false); }
  }
  useEffect(() => { void loadModels(); }, []);
  const selectedUnavailable = !!catalog && (catalog.directory_complete ?? catalog.listed !== null) && !!serverModel && serverModel !== catalog.current_model && !catalog.models.includes(serverModel);
  const snapshotMatches = !!result && result.choice === choice && ["position_id", "cost", "shares", "sellable_shares"].every((key) => result.position_snapshot[key as keyof ActionWorksheet["position_snapshot"]] === worksheet.position_snapshot[key as keyof ActionWorksheet["position_snapshot"]]);
  const evidenceMatches = discussionStrategyCurrent(result, worksheet);
  const synthesisNotice = result?.summary ? discussionSynthesisNotice(result.synthesis_basis, "position") : "";
  async function discuss() {
    if (inFlight.current || disabled || worksheet.archived || !worksheet.revision || (!custom && (selectedUnavailable || catalogBusy))) return;
    if (custom && Object.values(config).some((value) => !value.trim())) { setError("请填完接口地址、模型名和 API Key。"); return; }
    inFlight.current = true; setBusy(true); setError(""); setStage("preparing");
    setSelectedAction(null); setComparator(""); setWatchConfirmed("");
    controller.current = new AbortController();
    try {
      const guard = worksheetGuard(worksheet);
      const next = await api.positionDiscussionStream(planTarget.id, choice, guard.expected_position,
        (nextStage) => { if (active.current) setStage(nextStage); }, controller.current.signal, custom ? config : undefined,
        !custom && serverModel && serverModel !== catalog?.current_model ? serverModel : undefined, guard.expected_revision, planTarget.kind);
      if (active.current) { setResult(next); onComplete?.(); if (next.status === "failed") setError("模型未完成有效讨论，保留条件计划，可重试。"); }
    } catch (reason) { if (active.current) setError(isAqspAbortError(reason) ? "已停止等待本次分析，请刷新核对是否已有保存结果。" : reason instanceof Error ? reason.message : "讨论失败，可以重试。"); }
    finally { controller.current = null; inFlight.current = false; if (active.current) setBusy(false); }
  }
  async function adopt() {
    if (!result || selectedAction === null || !watchChoice || !comparator || watchConfirmed !== sourceKey || adoptFlight.current || disabled || busy) return;
    const id = worksheet.position_id;
    if (!id) return;
    let input;
    try { input = strategyWatchInput(result, worksheet, selectedAction, watchChoice, comparator); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "当前条件不能采用"); return; }
    adoptFlight.current = true; setAdopting(true); setError("");
    try {
      const next = await api.adoptStrategyWatch(id, input);
      if (active.current) { setSelectedAction(null); setWatchConfirmed(""); onWorksheet?.(next); onComplete?.(); }
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) {
      if (active.current) { setError(`${reason instanceof Error ? reason.message : "采用结果尚未确认"}。请更新当前策略后核对，未自动重复提交。`); onComplete?.(); }
    } finally { adoptFlight.current = false; if (active.current) setAdopting(false); }
  }
  function actions(rows: DiscussionAction[], canAdopt = false) {
    return <ol className="aq-strategy-actions">{rows.map((row, index) => {
      const reference = result?.price_refs[row.price_ref ?? ""];
      const trackable = canAdopt && result?.synthesis_basis?.mode !== "partial_initial" && !!reference && row.action !== "pause";
      const issue = canAdopt && result ? strategyWatchIssue(result, worksheet, index, watchChoice, comparator) : "";
      return <li key={index}>
        <div className="aq-strategy-action-title"><b>{actionLabels[row.action]}</b>{reference ? <strong>{reference.price.toFixed(2)} 元</strong> : null}</div>
        <p><span>等什么</span>{row.condition}</p><p><span>何时取消</span>{row.invalidation}</p>
        {reference ? <small>{reference.label} · {reference.source} · {formatEvidenceTime(reference.as_of)}</small> : null}
        {canAdopt && result ? <PositionActionChecks action={row} result={result} /> : null}
        {trackable ? <>
          {selectedAction !== index ? <button type="button" className="aq-btn" disabled={disabled || busy || adopting || !evidenceMatches} onClick={() => { setSelectedAction(index); setComparator(""); setWatchConfirmed(""); setWatchChoice(choice ?? (row.action === "buy_review" ? "t_watch" : "")); }}>采用这项条件并跟踪</button> : <form className="aq-strategy-adopt" onSubmit={(event) => { event.preventDefault(); void adopt(); }}>
            <label>我的处理路线<select aria-label="采用条件的处理路线" className="aq-input" value={watchChoice} disabled={adopting} onChange={(event) => { setWatchChoice(event.target.value as ReviewChoice | ""); setWatchConfirmed(""); }} required><option value="">请选择</option>{(Object.keys(PLAN_CHOICES) as ReviewChoice[]).map((key) => <option key={key} value={key} disabled={row.action === "buy_review" && key !== "t_watch"}>{PLAN_CHOICES[key].title}</option>)}</select></label>
            <label>什么报价变化提醒我<select aria-label="观察价触发方向" className="aq-input" value={comparator} disabled={adopting} onChange={(event) => { setComparator(event.target.value as WatchComparator | ""); setWatchConfirmed(""); }} required><option value="">请核对条件后选择</option><option value="at_or_above">达到或高于 {reference.price.toFixed(2)} 元</option><option value="at_or_below">达到或低于 {reference.price.toFixed(2)} 元</option></select></label>
            <p className="aq-note">后台核对已登记前提、失效条件与报价；其余文字条件到时仍需人工复核。</p>
            <label className="aq-action-plan-confirm"><input type="checkbox" checked={!!sourceKey && watchConfirmed === sourceKey} disabled={adopting} onChange={(event) => setWatchConfirmed(event.target.checked ? sourceKey : "")} />我已核对这条路线、观察方向和取消条件</label>
            {issue ? <p role="status">{issue}</p> : null}
            <div className="aq-row-actions"><button type="submit" className="aq-btn aq-btn-primary" disabled={disabled || busy || adopting || watchConfirmed !== sourceKey || !!issue}>{adopting ? "保存中…" : "确认采用并跟踪"}</button><button type="button" className="aq-btn" disabled={adopting} onClick={() => setSelectedAction(null)}>暂不采用</button></div>
          </form>}
        </> : canAdopt && !reference && row.action !== "pause" ? <small>缺少可核验价位，暂不建立到价跟踪。</small> : null}
      </li>;
    })}</ol>;
  }
  return <div className="aq-position-discussion" aria-label="AI 持仓方案讨论">
    <div className="aq-action-plan-heading"><h4>{comparison ? "AI 帮我比较处理策略" : "AI 分析这条路线的下一步"}</h4><button type="button" className="aq-btn" disabled={disabled || !!worksheet.archived || !worksheet.revision || busy || adopting || (!custom && (selectedUnavailable || catalogBusy))} onClick={() => void discuss()}>{busy ? "正在分析…" : worksheet.review_plan.watch ? "AI 复核当前方案" : result ? "重新分析" : comparison ? "帮我比较" : "开始分析"}</button></div>
    <p className="aq-note">{comparison ? "无需先选方案或填写交易参数。分析只作参考，完成后由你选择。" : "从趋势、做 T 和风险三个角度核对这条路；缺少依据会说明，不预设必须买卖。"}</p>
    {disabled && disabledReason ? <p role="status">{disabledReason}</p> : null}
    {!custom && catalog?.listed === false ? <p role="status">默认型号可能已迭代，请展开模型设置，选择可用型号验证。</p> : null}
    <details onToggle={(event) => { if (event.currentTarget.open && !catalog && !catalogBusy) void loadModels(); }}><summary>模型设置 · {custom ? "本次输入" : serverModel || "使用现有配置"}</summary>
      {!custom ? <div className="aq-model-selection"><label>本设备使用的型号<select aria-label="服务器模型型号" disabled={busy || catalogBusy} value={serverModel} onChange={(event) => { const model = event.target.value; setServerModel(model); if (catalog) { try { localStorage.setItem(`aqsp-position-model:${catalog.catalog_id}`, model); } catch { /* A blocked store keeps a request-only selection. */ } } }}>
        {!catalog ? <option value="">现有配置</option> : [...new Set([catalog.current_model, serverModel, ...catalog.models])].filter(Boolean).map((model) => <option key={model} value={model}>{model}{model === catalog.current_model ? " · 当前默认" : ""}{!catalog.models.includes(model) && (catalog.directory_complete ?? catalog.listed !== null) ? " · 未在目录中" : ""}</option>)}
      </select></label><button type="button" className="aq-btn" disabled={catalogBusy || busy} onClick={() => void loadModels(true)}>{catalogBusy ? "读取中…" : "刷新可用型号"}</button>
      {selectedUnavailable ? <p role="status">此前选择的型号不在当前目录中，请重新选择；没有自动切换。</p> : null}
      {catalog?.issue || catalogError ? <p role="status">{catalogError || catalog?.issue}</p> : null}
      {catalog ? <p className="aq-note">{catalog.notice} 型号偏好仅保存在本设备，不修改服务器全局配置。</p> : null}</div> : null}
      <label className="aq-action-plan-confirm"><input type="checkbox" checked={custom} disabled={busy} onChange={(event) => setCustom(event.target.checked)} />本次使用已有 OpenAI 兼容接口</label>
      {custom ? <div className="aq-action-plan-fields">{(["baseURL", "model", "apiKey"] as const).map((key) => <label key={key}>{({ baseURL: "接口地址", model: "模型名称", apiKey: "API Key" })[key]}<input aria-label={key} type={key === "apiKey" ? "password" : "text"} autoComplete="off" disabled={busy} value={config[key]} onChange={(event) => setConfig({ ...config, [key]: event.target.value })} /></label>)}</div> : null}
      <p className="aq-note">接口凭据仅用于本次请求，不写入持仓记录或浏览器存储；点击分析会将本仓位、风险与行情快照发送给所选模型服务。</p>
    </details>
    {busy ? <div role="status"><p>{({ preparing: "正在准备持仓依据…", independent: "三个角色正在独立分析…", challenge: "正在互相核对观点与缺证…", synthesis: "正在整理三条路的理由与分歧…" } as Record<string, string>)[stage] || "正在分析…"}</p><button type="button" className="aq-btn" onClick={() => controller.current?.abort()}>停止本次分析</button></div> : null}
    {error ? <p role="alert">{error}</p> : null}
    {result && !snapshotMatches ? <p role="status">持仓或选择已变化，旧讨论不再展示，请重新分析。</p> : null}
    {result && snapshotMatches && !evidenceMatches ? <p role="status">风险、环境或个人条件已变化，或分析已旧；保留原分析供核对，重新分析后再采用。</p> : null}
    {result && snapshotMatches ? <section className="aq-discussion-result" aria-label="AI 策略结果"><h4>{evidenceMatches ? "当前策略与下一步条件" : "生成时的策略分析"}</h4>
      <p className="aq-note">{result.mode} · {result.model} · {formatEvidenceTime(result.generated_at)} · {result.status === "complete" ? "讨论完成" : result.status === "partial" ? "部分完成" : "未完成"}</p>
      {synthesisNotice ? <p role="status" className="aq-note">{synthesisNotice}</p> : null}
      <p className="aq-note">分析时 {result.evidence_snapshot?.quote.price ?? "未知"} 元 · 当前 {worksheet.basis.price ?? "待更新"} 元；环境或风险变化需重核。</p>
      {result.evidence_snapshot ? <details><summary>分析当时使用的证据</summary><p>当时价格 {result.evidence_snapshot.quote.price ?? "未知"} 元 · {result.evidence_snapshot.quote.source || "来源待核验"} · {formatEvidenceTime(result.evidence_snapshot.quote.as_of)}</p>{(["market", "sector"] as const).map((key) => { const row = result.evidence_snapshot![key]; return <p key={key}>{key === "market" ? "大盘" : "板块"}：{row.summary || "依据不足"} · {row.source || "来源待核验"} · {formatEvidenceTime(row.as_of)}</p>; })}</details> : null}
      {disabled ? <p role="status">输入或行情依据已变化；以下为已保存的历史讨论。</p> : null}
      {result.summary ? <><StrategyReviewContextPanel context={result.review_context} />{comparison ? <><p className="aq-comparison-conclusion">{result.summary.conclusion}</p><p>{result.summary.focus_choice ? `可先核对${PLAN_CHOICES[result.summary.focus_choice].title}：` : "暂不选定路线："}{result.summary.focus_reason}</p><dl className="aq-comparison-routes">{result.summary.routes?.map((row) => <div key={row.choice}><dt>{PLAN_CHOICES[row.choice].title}<small>{({ investigate: "可进一步核对", not_now: "当前不宜", insufficient: "依据不足" })[row.judgment]}</small></dt><dd>{row.reason}{row.needs.length ? <small>还需：{row.needs.join("、")}</small> : null}</dd></div>)}</dl></> : <><p className="aq-comparison-conclusion">{result.summary.conclusion}</p></>}
        {result.summary.actions.length ? <div className="aq-strategy-next"><h4>下一步等什么，什么时候改变计划</h4>{actions(result.summary.actions, true)}</div> : <p>当前证据还不足以给出具体买卖观察条件。</p>}
        {result.summary.disagreements.length ? <p>仍有分歧：{result.summary.disagreements.join("；")}</p> : null}
        {result.summary.missing_evidence.length ? <p>还缺什么：{result.summary.missing_evidence.join("；")}</p> : null}
        {result.summary.agreements.length ? <details><summary>共同认可的依据</summary><ul>{result.summary.agreements.map((line, i) => <li key={i}>{line}</li>)}</ul></details> : null}
      </> : <p role="status">尚无通过校验的汇总结论，{result.summary_issue || "有效交叉回应不足"}；可展开查看已完成角色。</p>}
      <details><summary>查看各角色观点与相互回应（模型原文，待核对）</summary>{result.rounds.map((round, index) => <div key={index}><h5>{round.stage === "independent" ? "独立分析" : "交叉回应"}</h5>{round.opinions.map((row) => <article key={row.role}><b>{row.label}</b>{row.opinion ? <><p>{row.opinion.summary}</p>{row.opinion.challenge ? <p>回应 {row.opinion.reply_to.map((role) => ({ trend: "趋势分析", t_review: "做 T 评估", risk: "风险复核" })[role] ?? role).join("、")}：{row.opinion.challenge}</p> : null}<p className="aq-note">依据：{row.opinion.evidence_ids.join("、")}</p>{row.opinion.uncertainties.length ? <p>不确定：{row.opinion.uncertainties.join("；")}</p> : null}{actions(row.opinion.actions)}</> : <p>{row.issue}</p>}</article>)}</div>)}</details>
      <details><summary>模型与边界说明</summary><p className="aq-note">{result.notice}</p></details>
    </section> : null}
  </div>;
}
