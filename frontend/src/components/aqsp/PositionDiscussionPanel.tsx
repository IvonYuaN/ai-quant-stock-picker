import { useEffect, useRef, useState } from "react";
import { api, type ActionWorksheet, type DiscussionAction, type PositionDiscussion, type PositionModelConfig, type ReviewChoice } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";

const actionLabels = { observe: "继续观察", sell_review: "卖出复核", buy_review: "回补复核", pause: "暂停操作" };

export function PositionDiscussionPanel({ eventId, choice, worksheet, disabled }: {
  eventId: string; choice: ReviewChoice; worksheet: ActionWorksheet; disabled: boolean;
}) {
  const [result, setResult] = useState<PositionDiscussion | null>(worksheet.review_plan.discussion ?? null);
  const [config, setConfig] = useState<PositionModelConfig>({ baseURL: "", model: "", apiKey: "" });
  const [custom, setCustom] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { if (!inFlight.current) setResult(worksheet.review_plan.discussion ?? null); }, [worksheet.review_plan.discussion]);
  const snapshotMatches = !!result && result.choice === choice && ["position_id", "cost", "shares", "sellable_shares"].every((key) => result.position_snapshot[key as keyof ActionWorksheet["position_snapshot"]] === worksheet.position_snapshot[key as keyof ActionWorksheet["position_snapshot"]]);
  async function discuss() {
    if (inFlight.current || disabled) return;
    if (custom && Object.values(config).some((value) => !value.trim())) { setError("请填完接口地址、模型名和 API Key。"); return; }
    inFlight.current = true; setBusy(true); setError("");
    try {
      const next = await api.positionDiscussion(eventId, choice, worksheet.position_snapshot, custom ? config : undefined);
      if (active.current) { setResult(next); if (next.status === "failed") setError("模型未完成有效讨论，保留条件计划，可重试。"); }
    } catch (reason) { if (active.current) setError(reason instanceof Error ? reason.message : "讨论失败，可以重试。"); }
    finally { inFlight.current = false; if (active.current) setBusy(false); }
  }
  function actions(rows: DiscussionAction[]) {
    return <ol>{rows.map((row, index) => { const reference = result?.price_refs[row.price_ref ?? ""]; return <li key={index}>
      <b>{actionLabels[row.action]}</b>{reference ? <> · {reference.price.toFixed(2)} 元（{reference.label}，{formatEvidenceTime(reference.as_of)}）</> : null}<details><summary>模型提出的条件与失效理由（原文待核对）</summary><p>{row.condition}</p><p>失效 / 取消条件：{row.invalidation}</p><p className="aq-note">自由文字未经交易语义验证，不是成交指令；有效动作与价位以上方服务端校验字段为准。</p></details>
    </li>; })}</ol>;
  }
  return <div className="aq-position-discussion" aria-label="AI 持仓方案讨论">
    <div className="aq-action-plan-heading"><h4>AI 方案讨论</h4><button type="button" className="aq-btn" disabled={disabled || busy} onClick={() => void discuss()}>{busy ? "三角色分析与交叉复核中…" : result ? "重新讨论当前方案" : "分析当前方案"}</button></div>
    <p className="aq-note">趋势、做 T、风控分别分析，再回应彼此意见。最多 7 次模型调用，逐阶段限时；不预设必须买卖。</p>
    <details><summary>模型接口设置 · {custom ? "本次输入" : "使用服务器已有配置"}</summary>
      <label className="aq-action-plan-confirm"><input type="checkbox" checked={custom} disabled={busy} onChange={(event) => setCustom(event.target.checked)} />本次使用已有 OpenAI 兼容接口</label>
      {custom ? <div className="aq-action-plan-fields">{(["baseURL", "model", "apiKey"] as const).map((key) => <label key={key}>{({ baseURL: "接口地址", model: "模型名称", apiKey: "API Key" })[key]}<input aria-label={key} type={key === "apiKey" ? "password" : "text"} autoComplete="off" disabled={busy} value={config[key]} onChange={(event) => setConfig({ ...config, [key]: event.target.value })} /></label>)}</div> : null}
      <p className="aq-note">接口凭据仅用于本次请求，不写入持仓记录或浏览器存储；点击分析会将本仓位、风险与行情快照发送给所选模型服务。</p>
    </details>
    {busy ? <p role="status">正在进行独立分析、交叉回应和汇总；你可以继续查看其他信息。</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {result && !snapshotMatches ? <p role="status">持仓或选择已变化，旧讨论不再展示，请重新分析。</p> : null}
    {result && snapshotMatches ? <div className="aq-discussion-result">
      <p className="aq-note">{result.mode} · {result.model} · {formatEvidenceTime(result.generated_at)} · {result.status === "complete" ? "讨论完成" : result.status === "partial" ? "部分完成" : "未完成"}</p>
      <p className="aq-note">讨论行情快照 {formatEvidenceTime(result.quote_as_of)}；这是生成时意见，后续行情变化需重新核对。</p>
      {disabled ? <p role="status">输入或行情依据已变化；以下为已保存的历史讨论。</p> : null}
      {result.summary ? <><p className="aq-note">以下为模型提出的复核观察；自由文字未作交易语义验证，当前风险约束仍以持仓检查为准。</p>{actions(result.summary.actions)}<details><summary>模型汇总观点（未经交易语义验证）</summary><p>{result.summary.conclusion}</p></details>
        {result.summary.disagreements.length ? <p>仍有分歧：{result.summary.disagreements.join("；")}</p> : null}
        {result.summary.missing_evidence.length ? <p>还缺什么：{result.summary.missing_evidence.join("；")}</p> : null}
        {result.summary.agreements.length ? <details><summary>共同认可的依据</summary><ul>{result.summary.agreements.map((line, i) => <li key={i}>{line}</li>)}</ul></details> : null}
      </> : <p role="status">尚无通过校验的汇总结论，请查看已完成角色；不会把失败当作共识。</p>}
      <details><summary>查看各角色观点与相互回应（模型原文，待核对）</summary>{result.rounds.map((round, index) => <div key={index}><h5>{round.stage === "independent" ? "独立分析" : "交叉回应"}</h5>{round.opinions.map((row) => <article key={row.role}><b>{row.label}</b>{row.opinion ? <><p>{row.opinion.summary}</p>{row.opinion.challenge ? <p>回应 {row.opinion.reply_to.map((role) => ({ trend: "趋势分析", t_review: "做 T 评估", risk: "风险复核" })[role] ?? role).join("、")}：{row.opinion.challenge}</p> : null}<p className="aq-note">依据：{row.opinion.evidence_ids.join("、")}</p>{row.opinion.uncertainties.length ? <p>不确定：{row.opinion.uncertainties.join("；")}</p> : null}{actions(row.opinion.actions)}</> : <p>{row.issue}</p>}</article>)}</div>)}</details>
      <p className="aq-note">{result.notice}</p>
    </div> : null}
  </div>;
}
