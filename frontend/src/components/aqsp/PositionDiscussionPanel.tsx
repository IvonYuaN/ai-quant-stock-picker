import { useEffect, useRef, useState } from "react";
import { api, isAqspAbortError, type ActionWorksheet, type DiscussionAction, type PositionDiscussion, type PositionModelCatalog, type PositionModelConfig, type PlanTarget, type ReviewChoice } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { worksheetGuard, discussionIsCurrent } from "@/lib/position-workspace";
import { PLAN_CHOICES, worksheetExpired } from "@/lib/position-plan";

const actionLabels = { observe: "继续观察", sell_review: "卖出复核", buy_review: "回补复核", pause: "暂停操作" };

export function PositionDiscussionPanel({ eventId, target, choice, worksheet, disabled, disabledReason, onComplete }: {
  eventId?: string; target?: PlanTarget; onComplete?: () => void; choice: ReviewChoice | null; worksheet: ActionWorksheet; disabled: boolean; disabledReason?: string;
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
  const active = useRef(true);
  const inFlight = useRef(false);
  const catalogInFlight = useRef(false);
  const catalogIdentity = useRef("");
  const latestBasis = useRef(worksheet.analysis_basis_id);
  const latestRevision = useRef(worksheet.revision);
  latestRevision.current = worksheet.revision;
  const receivedAgainst = useRef<string | undefined>(undefined);
  latestBasis.current = worksheet.analysis_basis_id;
  useEffect(() => { active.current = true; return () => { active.current = false; controller.current?.abort(); }; }, []);
  useEffect(() => { if (!inFlight.current) setResult(persisted ?? null); }, [persisted]);
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
  const evidenceMatches = !!result && discussionIsCurrent(result, worksheet)
    && !worksheetExpired({ ...worksheet.basis, checked_at: result.generated_at }, Date.now());
  async function discuss() {
    if (inFlight.current || disabled || worksheet.archived || !worksheet.revision || (!custom && (selectedUnavailable || catalogBusy))) return;
    if (custom && Object.values(config).some((value) => !value.trim())) { setError("请填完接口地址、模型名和 API Key。"); return; }
    inFlight.current = true; setBusy(true); setError(""); setStage("preparing");
    controller.current = new AbortController();
    try {
      const guard = worksheetGuard(worksheet);
      const sentRevision = guard.expected_revision;
      const next = await api.positionDiscussionStream(planTarget.id, choice, guard.expected_position,
        (nextStage) => { if (active.current) setStage(nextStage); }, controller.current.signal, custom ? config : undefined,
        !custom && serverModel && serverModel !== catalog?.current_model ? serverModel : undefined, guard.expected_revision, planTarget.kind);
      if (active.current) { receivedAgainst.current = sentRevision === latestRevision.current ? latestBasis.current : undefined; setResult(next); onComplete?.(); if (next.status === "failed") setError("模型未完成有效讨论，保留条件计划，可重试。"); }
    } catch (reason) { if (active.current) setError(isAqspAbortError(reason) ? "已停止等待本次分析，请刷新核对是否已有保存结果。" : reason instanceof Error ? reason.message : "讨论失败，可以重试。"); }
    finally { controller.current = null; inFlight.current = false; if (active.current) setBusy(false); }
  }
  function actions(rows: DiscussionAction[]) {
    return <ol>{rows.map((row, index) => { const reference = result?.price_refs[row.price_ref ?? ""]; return <li key={index}>
      <b>{actionLabels[row.action]}</b>{reference ? <> · {reference.price.toFixed(2)} 元（{reference.label}，{formatEvidenceTime(reference.as_of)}）</> : null}<details><summary>查看操作条件和取消理由</summary><p>{row.condition}</p><p>失效 / 取消条件：{row.invalidation}</p><p className="aq-note">AI 的解释可能有误，请结合上方行情与风险核对。</p></details>
    </li>; })}</ol>;
  }
  return <div className="aq-position-discussion" aria-label="AI 持仓方案讨论">
    <div className="aq-action-plan-heading"><h4>{comparison ? "拿不准？先让 AI 比较三条路" : "帮我看这条路是否合适"}</h4><button type="button" className="aq-btn" disabled={disabled || !!worksheet.archived || !worksheet.revision || busy || (!custom && (selectedUnavailable || catalogBusy))} onClick={() => void discuss()}>{busy ? "正在分析…" : result ? "重新分析" : comparison ? "帮我比较" : "开始分析"}</button></div>
    <p className="aq-note">{comparison ? "无需先选方案或填写交易参数。分析只作参考，完成后由你选择。" : "从趋势、做 T 和风险三个角度核对这条路；缺少依据会说明，不预设必须买卖。"}</p>
    <p className="aq-note">点击分析会将这笔持仓、已保存的个人条件和行情依据发送给所选模型；也可以直接手动选择。</p>
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
    {result && snapshotMatches && !evidenceMatches ? <p role="status">个人条件、持仓或行情依据与生成时不同，或讨论已旧。下方保留生成时分析供核对，不能直接当作当前操作依据。</p> : null}
    {result && snapshotMatches ? <details className="aq-discussion-result" open={evidenceMatches || (!!receivedAgainst.current && receivedAgainst.current === worksheet.analysis_basis_id && result.review_revision === worksheet.revision && !!result.evidence_snapshot && !worksheetExpired({ ...worksheet.basis, checked_at: result.generated_at }))}><summary>{evidenceMatches ? "查看分析结果" : "查看生成时分析与依据"}</summary>
      <p className="aq-note">{result.mode} · {result.model} · {formatEvidenceTime(result.generated_at)} · {result.status === "complete" ? "讨论完成" : result.status === "partial" ? "部分完成" : "未完成"}</p>
      <p className="aq-note">讨论行情快照 {formatEvidenceTime(result.quote_as_of)}；这是生成时意见，后续行情变化需重新核对。</p>
      {result.evidence_snapshot ? <details><summary>分析当时使用的证据</summary><p>当时价格 {result.evidence_snapshot.quote.price ?? "未知"} 元 · {result.evidence_snapshot.quote.source || "来源待核验"} · {formatEvidenceTime(result.evidence_snapshot.quote.as_of)}</p>{(["market", "sector"] as const).map((key) => { const row = result.evidence_snapshot![key]; return <p key={key}>{key === "market" ? "大盘" : "板块"}：{row.summary || "依据不足"} · {row.source || "来源待核验"} · {formatEvidenceTime(row.as_of)}</p>; })}</details> : null}
      {disabled ? <p role="status">输入或行情依据已变化；以下为已保存的历史讨论。</p> : null}
      {result.summary ? <>{comparison ? <><p className="aq-comparison-conclusion">{result.summary.conclusion}</p><p>{result.summary.focus_choice ? `可先核对${PLAN_CHOICES[result.summary.focus_choice].title}：` : "暂不选定路线："}{result.summary.focus_reason}</p><dl className="aq-comparison-routes">{result.summary.routes?.map((row) => <div key={row.choice}><dt>{PLAN_CHOICES[row.choice].title}<small>{({ investigate: "可进一步核对", not_now: "当前不宜", insufficient: "依据不足" })[row.judgment]}</small></dt><dd>{row.reason}{row.needs.length ? <small>还需：{row.needs.join("、")}</small> : null}</dd></div>)}</dl></> : <><p className="aq-note">以下是 AI 提出的观察条件，可能有误，不能代替你的成交确认。</p>{actions(result.summary.actions)}<details><summary>查看 AI 的解释（需自行核对）</summary><p>{result.summary.conclusion}</p></details></>}
        {result.summary.disagreements.length ? <p>仍有分歧：{result.summary.disagreements.join("；")}</p> : null}
        {result.summary.missing_evidence.length ? <p>还缺什么：{result.summary.missing_evidence.join("；")}</p> : null}
        {result.summary.agreements.length ? <details><summary>共同认可的依据</summary><ul>{result.summary.agreements.map((line, i) => <li key={i}>{line}</li>)}</ul></details> : null}
      </> : <p role="status">尚无通过校验的汇总结论，{result.summary_issue || "有效交叉回应不足"}；可展开查看已完成角色。</p>}
      <details><summary>查看各角色观点与相互回应（模型原文，待核对）</summary>{result.rounds.map((round, index) => <div key={index}><h5>{round.stage === "independent" ? "独立分析" : "交叉回应"}</h5>{round.opinions.map((row) => <article key={row.role}><b>{row.label}</b>{row.opinion ? <><p>{row.opinion.summary}</p>{row.opinion.challenge ? <p>回应 {row.opinion.reply_to.map((role) => ({ trend: "趋势分析", t_review: "做 T 评估", risk: "风险复核" })[role] ?? role).join("、")}：{row.opinion.challenge}</p> : null}<p className="aq-note">依据：{row.opinion.evidence_ids.join("、")}</p>{row.opinion.uncertainties.length ? <p>不确定：{row.opinion.uncertainties.join("；")}</p> : null}{actions(row.opinion.actions)}</> : <p>{row.issue}</p>}</article>)}</div>)}</details>
      <p className="aq-note">{result.notice}</p>
    </details> : null}
  </div>;
}
