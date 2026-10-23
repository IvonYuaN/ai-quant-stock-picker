import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowDownLeft, ArrowUpRight, Check, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, loadUserId, type PortfolioData } from "@/lib/api";
import { announcePortfolioChange, operationError, RECORD_OPERATION_EVENT, shanghaiDate, type OperationInput, type OperationTarget } from "@/lib/operation";

export function OperationRecorder() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [target, setTarget] = useState<OperationTarget | null>(null);
  const [form, setForm] = useState<OperationInput>({ direction: "buy", code: "", price: "", shares: "", date: shanghaiDate() });
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const submission = useRef({ fingerprint: "", id: "" });
  const inFlight = useRef(false);
  const pendingDraft = useRef(false);
  const readVersion = useRef(0);
  const draftKey = useRef(`aqsp:operation-draft:${loadUserId()}`);

  const loadPortfolio = async () => {
    const version = ++readVersion.current;
    setLoadError("");
    try {
      const result = await api.portfolio();
      if (version === readVersion.current) setPortfolio(result);
    } catch (reason) {
      if (version === readVersion.current) setLoadError(reason instanceof Error ? reason.message : "读取持仓失败");
    }
  };

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(draftKey.current);
      if (raw) {
        const draft: unknown = JSON.parse(raw);
        if (draft && typeof draft === "object" && "form" in draft && "target" in draft && "id" in draft && typeof draft.id === "string") {
          const input = draft.form as OperationInput;
          if (input && (input.direction === "buy" || input.direction === "sell") && [input.code, input.date, input.price, input.shares].every((value) => typeof value === "string") && !operationError(input, shanghaiDate())) {
            setForm(input); setTarget(draft.target as OperationTarget); setUncertain(true);
            submission.current = { fingerprint: JSON.stringify(input), id: draft.id };
            pendingDraft.current = true;
            setError("上次提交被中断，结果尚未确认。请原样重试，后端会防止重复记账。");
            void loadPortfolio();
          }
        }
      }
    } catch { /* 存储不可用时，提交前会明确阻止。 */ }
    const open = (event: Event) => {
      if (inFlight.current) return;
      if (pendingDraft.current) { dialog.current?.showModal(); return; }
      const detail = (event as CustomEvent<OperationTarget>).detail ?? {};
      setTarget(detail);
      setForm({ direction: detail.direction ?? "buy", code: detail.code ?? "", price: "", shares: "", date: shanghaiDate() });
      setError(""); setUncertain(false); setPortfolio(null);
      submission.current = { fingerprint: "", id: "" };
      dialog.current?.showModal();
      void loadPortfolio();
    };
    window.addEventListener(RECORD_OPERATION_EVENT, open);
    return () => { window.removeEventListener(RECORD_OPERATION_EVENT, open); readVersion.current++; };
  }, []);

  const holding = portfolio?.holdings.find((row) => row.code === form.code.trim());
  const close = () => { if (!inFlight.current) dialog.current?.close(); };
  const change = (patch: Partial<OperationInput>) => {
    setForm((value) => ({ ...value, ...patch })); setError("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (inFlight.current) return;
    const validation = operationError(form, shanghaiDate());
    if (validation) { setError(validation); return; }
    if (!uncertain && form.direction === "sell" && !holding) { setError("请先读取并确认要卖出的持仓"); return; }
    if (!uncertain && form.direction === "sell" && holding && Number(form.shares) > (holding.sellable_shares ?? holding.shares)) { setError("数量超过当前可卖持仓，请核对 T+1 限制"); return; }
    const fingerprint = JSON.stringify(form);
    if (submission.current.fingerprint !== fingerprint) submission.current = { fingerprint, id: crypto.randomUUID() };
    try { sessionStorage.setItem(draftKey.current, JSON.stringify({ form, target, id: submission.current.id })); }
    catch { setError("浏览器无法保存本次防重复记录，尚未提交。请允许站点存储后重试。"); return; }
    inFlight.current = true; setBusy(true); setError("");
    try {
      if (form.direction === "buy") await api.addHolding(form.code.trim(), Number(form.shares), Number(form.price), form.date, submission.current.id);
      else if (target?.alertId) await api.closeAlertPosition(target.alertId, form.date, Number(form.price), Number(form.shares));
      else await api.closePosition(form.code.trim(), form.date, Number(form.price), Number(form.shares), holding?.cost ?? 0, submission.current.id);
      try { sessionStorage.removeItem(draftKey.current); } catch { /* 保留旧编号重试仍幂等。 */ }
      pendingDraft.current = false;
      setUncertain(false);
      announcePortfolioChange();
      dialog.current?.close(); setTarget(null);
      inFlight.current = false; setBusy(false);
      toast.success(form.direction === "buy" ? "买入回录已保存，风险规则已关联" : "卖出回录已保存，持仓已更新");
      void api.runLiveRiskCheck(undefined, false).then(announcePortfolioChange).catch(() => toast.warning("成交已保存；风险检查失败，请在今日页重新检查"));
    } catch (reason) {
      const unknown = !(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500;
      if (!unknown) { try { sessionStorage.removeItem(draftKey.current); } catch { /* 可用旧编号重试。 */ } }
      pendingDraft.current = unknown; setUncertain(unknown);
      setError(unknown ? "提交结果尚未确认，已保留本次记录编号。请原样重试，后端会防止重复记账。" : `${reason.message}。输入已保留，请核对后重试。`);
    } finally { inFlight.current = false; setBusy(false); }
  };

  return (
    <dialog ref={dialog} className="aq-operation-dialog" onCancel={(event) => { if (inFlight.current) event.preventDefault(); }}>
      <form onSubmit={(event) => void submit(event)}>
        <header className="aq-operation-head"><div><h2>我操作了</h2><p>记录在交易软件中已完成的成交</p></div><button type="button" className="aq-icon-btn" aria-label="关闭操作回录" disabled={busy} onClick={close}><X aria-hidden="true" /></button></header>
        <div className="aq-operation-direction" role="group" aria-label="成交方向">
          <button type="button" className="aq-btn" aria-pressed={form.direction === "buy"} disabled={busy || uncertain || !!target?.alertId} onClick={() => change({ direction: "buy" })}><ArrowDownLeft aria-hidden="true" />买入</button>
          <button type="button" className="aq-btn" aria-pressed={form.direction === "sell"} disabled={busy || uncertain} onClick={() => change({ direction: "sell" })}><ArrowUpRight aria-hidden="true" />卖出</button>
        </div>
        <div className="aq-operation-fields">
          <label>股票代码<input className="aq-input" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoFocus value={form.code} disabled={busy || uncertain || !!target?.alertId} onChange={(event) => change({ code: event.target.value })} required placeholder="6 位代码" /></label>
          <label>成交日期<input type="date" className="aq-input" max={shanghaiDate()} value={form.date} disabled={busy || uncertain} onChange={(event) => change({ date: event.target.value })} required /></label>
          <label>成交价格（元）<input type="number" className="aq-input" inputMode="decimal" min="0.0001" step="any" value={form.price} disabled={busy || uncertain} onChange={(event) => change({ price: event.target.value })} required /></label>
          <label>成交数量（股）<input type="number" className="aq-input" inputMode="decimal" min="0.0001" step="any" value={form.shares} disabled={busy || uncertain} onChange={(event) => change({ shares: event.target.value })} required /></label>
        </div>
        {form.direction === "sell" ? <div className="aq-operation-note">{loadError ? <><span role="alert">读取持仓失败：{loadError}</span><button type="button" className="aq-btn" onClick={() => void loadPortfolio()}><RefreshCw aria-hidden="true" />重试</button></> : !portfolio ? "正在读取当前持仓…" : holding ? `${holding.name || holding.code} · 持有 ${holding.shares} 股 · 当前可卖 ${holding.sellable_shares ?? holding.shares} 股` : "未找到这只持仓，请核对代码"}</div> : <p className="aq-operation-note">同代码买入会合并数量与成本；当日新增数量受 T+1 限制。</p>}
        {error ? <p className="aq-operation-error" role="alert">{error}</p> : null}
        <footer className="aq-operation-foot"><span>仅记录成交，不代下单</span><button className="aq-btn aq-btn-primary" type="submit" disabled={busy || (!uncertain && form.direction === "sell" && !holding)}><Check aria-hidden="true" />{busy ? "正在保存…" : uncertain ? "原样重试成交" : "保存成交"}</button></footer>
      </form>
    </dialog>
  );
}
