import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Check, RefreshCw, ShieldCheck } from "lucide-react";
import { api, type AlertFrequency, type PositionAlertRule } from "@/lib/api";
import { ALERTS_CHANGED_EVENT, PORTFOLIO_CHANGED_EVENT } from "@/lib/operation";

const labels = { L1: "成本硬止损", L2: "均线跌破", L4: "MACD 死叉", L5: "高水位移动止损" };

function RuleForm({ rule, onSave }: { rule: PositionAlertRule; onSave: (value: PositionAlertRule) => void }) {
  const [enabled, setEnabled] = useState(rule.enabled);
  const [value, setValue] = useState(String(rule.level === "L1" || rule.level === "L5" ? rule.param_value * 100 : rule.param_value));
  const [frequency, setFrequency] = useState<AlertFrequency>(rule.freq_mode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const percentage = rule.level === "L1" || rule.level === "L5";
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      const updated = await api.updateRiskRule(rule.id, {
        enabled, freq_mode: frequency,
        ...(rule.level === "L4" ? {} : { param_value: percentage ? Number(value) / 100 : Number(value) }),
      });
      onSave(updated); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存规则失败，设置已保留"); }
    finally { inFlight.current = false; setBusy(false); }
  };
  return <form className="aq-rule-form" onSubmit={(event) => void save(event)}>
    <div className="aq-rule-title"><label><input type="checkbox" checked={enabled} disabled={busy} onChange={(event) => setEnabled(event.target.checked)} />{rule.level} {labels[rule.level]}</label><small>{rule.origin === "AUTO_DEFAULT" ? "自动规则" : "自定义"}</small></div>
    <div className="aq-rule-fields">
      {rule.level === "L2" ? <label>均线周期<select className="aq-input" value={value} disabled={busy} onChange={(event) => setValue(event.target.value)}>{[5, 10, 20].map((days) => <option key={days} value={days}>MA{days}</option>)}</select></label> : percentage ? <label>{rule.level === "L1" ? "成本回撤（%）" : "高水位回撤（%）"}<input type="number" className="aq-input" min="0.01" max="99.99" step="any" value={value} disabled={busy} onChange={(event) => setValue(event.target.value)} required /></label> : <span className="aq-operation-note">固定 MACD（12,26,9）</span>}
      <label>提醒频率<select className="aq-input" value={frequency} disabled={busy} onChange={(event) => setFrequency(event.target.value as AlertFrequency)}><option value="once">本次触发仅一次</option><option value="daily">每日一次</option><option value="always">每次检查</option></select></label>
      <button type="submit" className="aq-btn" disabled={busy}><Check aria-hidden="true" />{busy ? "保存中…" : "保存规则"}</button>
    </div>
    <p className="aq-operation-note">{rule.trigger_price != null ? `当前触发位 ${rule.trigger_price.toFixed(4)} 元 · ` : ""}{rule.effective_from ? `生效日期 ${rule.effective_from} · ` : ""}{rule.frequency_note || "频率作用于持续触发与通知；关闭后不生成该层提醒。"}</p>
    {error ? <p className="aq-operation-error" role="alert">{error}</p> : null}
  </form>;
}

export function RiskRuleEditor({ code }: { code: string }) {
  const [open, setOpen] = useState(true);
  const [rules, setRules] = useState<PositionAlertRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const version = useRef(0);
  const load = useCallback(async () => {
    const current = ++version.current; setLoading(true); setError("");
    try { const data = await api.riskRules(); if (current === version.current) setRules(data.rules.filter((rule) => rule.symbol === code)); }
    catch (reason) { if (current === version.current) setError(reason instanceof Error ? reason.message : "读取风险规则失败"); }
    finally { if (current === version.current) setLoading(false); }
  }, [code]);
  useEffect(() => {
    if (!open) return;
    void load(); window.addEventListener(PORTFOLIO_CHANGED_EVENT, load);
    return () => { version.current++; window.removeEventListener(PORTFOLIO_CHANGED_EVENT, load); };
  }, [open, load]);
  return <details className="aq-rule-editor" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary><ShieldCheck aria-hidden="true" />风险规则与频率 <span>L3 炸板未上线</span></summary>
    {loading ? <p role="status">读取规则中…</p> : error ? <p className="aq-operation-error" role="alert">{error}<button type="button" className="aq-btn" onClick={() => void load()}><RefreshCw aria-hidden="true" />重试</button></p> : rules.length ? rules.map((rule) => <RuleForm key={`${rule.id}:${rule.updated_at}`} rule={rule} onSave={(updated) => { setRules((prior) => prior.map((item) => item.id === updated.id ? updated : item)); setSaved(`${updated.level} 规则已保存`); }} />) : <p>暂无已关联规则，请刷新后重试。</p>}
    {saved ? <p role="status">{saved}</p> : null}
  </details>;
}
