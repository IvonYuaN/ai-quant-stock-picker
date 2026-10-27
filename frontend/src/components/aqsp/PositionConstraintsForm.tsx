import { useEffect, useRef, useState } from "react";
import { api, type ActionWorksheet, type PositionConstraints } from "@/lib/api";
import { constraintsDraft, draftNeedsReview, parseConstraints, worksheetGuard } from "@/lib/position-workspace";
import { formatEvidenceTime } from "@/lib/format";
import { ALERTS_CHANGED_EVENT } from "@/lib/operation";

const attentionLabels: Record<PositionConstraints["attention"], string> = { unknown: "还不确定", available: "能持续看盘", occasional: "只能偶尔看一眼", unavailable: "没有时间盯盘" };

/** Optional constraints refine feasibility; unknown is never silently zero. */
export function PositionConstraintsForm({ worksheet, disabled, onSaved }: { worksheet: ActionWorksheet; disabled: boolean; onSaved: (next: ActionWorksheet) => void }) {
  const [draft, setDraft] = useState(() => constraintsDraft(worksheet));
  const [baseRevision, setBaseRevision] = useState(worksheet.revision);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [confirmedQuote, setConfirmedQuote] = useState<{ price: number | null; as_of: string | null } | null>(null);
  const [reset, setReset] = useState(false);
  const [skipped, setSkipped] = useState(false);
  const dirty = useRef(false);
  const active = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (!dirty.current && !inFlight.current) { setDraft(constraintsDraft(worksheet)); setBaseRevision(worksheet.revision); setConfirmedQuote(null); setReset(false); }
  }, [worksheet.revision]);
  useEffect(() => { setConfirmedQuote(null); }, [worksheet.basis.price, worksheet.basis.quote_as_of]);
  const stale = draftNeedsReview(baseRevision, worksheet);
  const budgetChanged = draft.loss_limit.trim() !== (worksheet.constraints?.loss_limit == null ? "" : String(worksheet.constraints.loss_limit));
  const needsConfirmation = !!draft.loss_limit.trim() && (budgetChanged || reset || !!worksheet.risk_baseline_requires_confirmation || !worksheet.constraints?.risk_baseline);
  function change(key: keyof typeof draft, value: string) {
    dirty.current = true; setSaved(false); setConfirmedQuote(null); if (key === "loss_limit" && !value.trim()) setReset(false); setDraft({ ...draft, [key]: value });
  }
  async function save() {
    if (inFlight.current || disabled || stale || worksheet.archived) return;
    try {
      const value = parseConstraints(draft);
      if (needsConfirmation && !confirmedQuote) throw new Error("请先确认新增亏损金额的起算基准，或将金额留空。其他条件仍可使用。");
      const guard = worksheetGuard(worksheet);
      inFlight.current = true; setBusy(true); setError("");
      const next = await api.savePositionConstraints(worksheet.position_id ?? worksheet.position_snapshot.position_id!, value, guard.expected_position, guard.expected_revision, reset, needsConfirmation ? confirmedQuote! : undefined);
      if (!active.current) return;
      dirty.current = false; setDraft(constraintsDraft(next)); setBaseRevision(next.revision); setSaved(true); setConfirmedQuote(null); setReset(false); onSaved(next);
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { if (active.current) setError(reason instanceof Error ? reason.message : "保存失败，填写内容已保留"); }
    finally { inFlight.current = false; if (active.current) setBusy(false); }
  }
  const baseline = worksheet.constraints?.risk_baseline;
  return <details className="aq-personal-conditions" aria-label="我的个人条件">
    <summary>让计划更符合我 <span>可跳过 · 不填也能比较方案</span></summary>
    <p className="aq-note">只影响方案的限制和解释，不会替你决定买卖。空白表示不确定，填 0 表示明确不追加或不再承受新增损失。</p>
    <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <div className="aq-constraints-fields">
        <label>你有时间盯盘吗？<select aria-label="盯盘时间" value={draft.attention} disabled={busy || disabled} onChange={(event) => change("attention", event.target.value)}>{Object.entries(attentionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>最多再拿出多少钱？（元）<input aria-label="新增资金上限（元）" type="number" min="0" step="any" placeholder="不确定可留空；不追加填 0" value={draft.extra_cash} disabled={busy || disabled} onChange={(event) => change("extra_cash", event.target.value)} /><small className="aq-note">不追加，也可核对实际卖出所得现金内的回补。</small></label>
        <label>从确认时起，最多还能承受多少亏损？（元）<input aria-label="新增亏损上限（元）" type="number" min="0" step="any" placeholder="不确定可留空" value={draft.loss_limit} disabled={busy || disabled} onChange={(event) => change("loss_limit", event.target.value)} /></label>
      </div>
      {baseline ? <p className="aq-note">已确认基准：{baseline.price.toFixed(2)} 元 × {baseline.shares} 股，市值 {baseline.market_value.toFixed(2)} 元 · {formatEvidenceTime(baseline.as_of)}。刷新行情不会重新开始计算损失。</p> : null}
      {baseline && draft.loss_limit.trim() ? <label className="aq-action-plan-confirm"><input type="checkbox" checked={reset} disabled={busy || disabled} onChange={(event) => { dirty.current = true; setReset(event.target.checked); setConfirmedQuote(null); setSaved(false); }} />我明确要按当前持仓和价格重新起算风险金额</label> : null}
      {needsConfirmation ? <label className="aq-action-plan-confirm"><input type="checkbox" checked={!!confirmedQuote} disabled={busy || disabled || !!worksheet.basis.quote_issue || !worksheet.basis.price} onChange={(event) => setConfirmedQuote(event.target.checked ? { price: worksheet.basis.price, as_of: worksheet.basis.quote_as_of } : null)} />我确认以 {worksheet.basis.price?.toFixed(2) ?? "待更新"} 元 × {worksheet.basis.shares ?? "待核对"} 股（{formatEvidenceTime(worksheet.basis.quote_as_of)}）作为起算基准；它不取消已有风险规则。</label> : null}
      {needsConfirmation && (worksheet.basis.quote_issue || !worksheet.basis.price) ? <p role="status">先更新有效行情，才能确认风险金额的起算基准。</p> : null}
      {worksheet.constraints_issue ? <p role="status" className="aq-action-plan-warning">{worksheet.constraints_issue}</p> : null}
      {stale ? <div className="aq-refresh-warning" role="status"><p>当前情况已变化，填写内容已保留。先核对最新持仓和条件，再保存。</p><button type="button" className="aq-btn" disabled={busy || disabled} onClick={() => { setBaseRevision(worksheet.revision); setConfirmedQuote(null); }}>已核对最新情况，继续填写</button></div> : null}
      {error ? <p role="alert">{error}。填写内容已保留；可更新后核对是否已保存。</p> : null}
      <div className="aq-row-actions"><button className="aq-btn" type="submit" disabled={busy || disabled || stale}>{busy ? "保存中…" : "保存个人条件"}</button><button className="aq-btn" type="button" onClick={() => setSkipped(true)}>先跳过，直接比较</button></div>
      {saved ? <p role="status">个人条件已保存。旧试算和分析需按新条件重新核对。</p> : skipped ? <p role="status">可以直接看下方方案；未填写的条件会保留为未知。</p> : null}
    </form>
  </details>;
}
