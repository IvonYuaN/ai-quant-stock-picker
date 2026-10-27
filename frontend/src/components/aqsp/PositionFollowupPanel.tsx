import { useEffect, useRef, useState } from "react";
import { type ActionWorksheet, type FollowupMode, type FollowupObservation } from "@/lib/api";
import { FOLLOWUP_MODES, followupResponseIsCurrent, followupView, saveFollowupMode } from "@/lib/position-followup";
import { formatEvidenceTime } from "@/lib/format";
import { ALERTS_CHANGED_EVENT } from "@/lib/operation";

export function FollowupStatus({ observation, compact = false }: { observation: FollowupObservation; compact?: boolean }) {
  return <div className={`aq-followup-status${compact ? " aq-followup-status-compact" : ""}`}><strong>{observation.title}</strong>{!compact ? <p>{observation.detail}</p> : null}<small>后台检查 {formatEvidenceTime(observation.observed_at)}{!compact ? ` · 行情时点 ${formatEvidenceTime(observation.quote_as_of) || "尚缺依据"}` : ""}</small></div>;
}

/** Background facts stay separate from the page quote; setting a mode is not a delivery receipt. */
export function PositionFollowupPanel({ worksheet, disabled, onSaved }: { worksheet: ActionWorksheet; disabled: boolean; onSaved: (next: ActionWorksheet) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const active = useRef(true), inFlight = useRef(false);
  const current = useRef(worksheet); current.current = worksheet;
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const { observation, stale, issue } = followupView(worksheet);
  async function save(mode: FollowupMode) {
    if (disabled || inFlight.current || worksheet.archived) return;
    const basis = worksheet;
    inFlight.current = true; setBusy(true); setError(""); setSaved(false);
    try {
      const next = await saveFollowupMode(basis, mode);
      if (!active.current) return;
      if (!followupResponseIsCurrent(basis, current.current, next)) throw new Error("持仓或计划已变化，请更新后核对通知设置。");
      onSaved(next); setSaved(true);
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { if (active.current) setError(`${reason instanceof Error ? reason.message : "通知设置保存失败"}。请更新核对，原设置以服务器为准。`); }
    finally { inFlight.current = false; if (active.current) setBusy(false); }
  }
  return <section className="aq-followup-panel" aria-label="后台计划跟进">
    <h2>上次后台检查</h2>
    {observation ? <FollowupStatus observation={observation} /> : <p>尚无这笔持仓的后台检查记录，不能据此判断计划状态。</p>}
    {stale && !worksheet.archived ? <p className="aq-action-plan-warning" role="status">这条记录已旧，或方案、仓位、条件与规则已变化，需等待新的后台检查；当前页行情更新不会改写它。</p> : null}
    {issue ? <p className="aq-note" role="status">{issue}</p> : null}
    {!worksheet.archived ? <details className="aq-followup-settings"><summary>通知设置 · {worksheet.followup ? ({ off: "仅站内", changes: "变化时提醒", daily: "每天最多一次" })[worksheet.followup.mode] : "待读取"}</summary>
      <label>需要外发通知吗？<select aria-label="计划通知模式" value={worksheet.followup?.mode ?? "off"} disabled={disabled || busy || !worksheet.followup} onChange={(event) => void save(event.target.value as FollowupMode)}>{Object.entries(FOLLOWUP_MODES).map(([mode, label]) => <option key={mode} value={mode}>{label}</option>)}</select></label>
      <p className="aq-note">正常等待不外发；开启后使用现有通知渠道。保存设置只表示配置成功，不代表通知已送达。</p>
      {busy ? <p role="status">正在保存通知设置…</p> : null}{saved ? <p role="status">通知设置已保存，未验证外发是否送达。</p> : null}{error ? <p role="alert">{error}</p> : null}
    </details> : null}
    <p className="aq-note">{worksheet.archived ? "清仓后停止跟进，这里只保留当时的后台检查记录。" : "后台按现有定时检查入口运行，数据源失败时可能延后。页面可见时每分钟读取一次，不代表实时逐笔监听。"}</p>
  </section>;
}
