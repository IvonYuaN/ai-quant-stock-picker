import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowDownLeft, ArrowUpRight, Check, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, loadUserId, type PortfolioData, type TradeInput, type TradeJournal } from "@/lib/api";
import { announcePortfolioChange, operationError, RECORD_OPERATION_EVENT, shanghaiDate, type OperationInput, type OperationTarget } from "@/lib/operation";

export function OperationRecorder() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [target, setTarget] = useState<OperationTarget | null>(null);
  const [form, setForm] = useState<OperationInput>({ direction: "buy", code: "", price: "", shares: "", date: shanghaiDate(), actualFees: "" });
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [journal, setJournal] = useState<TradeJournal | null>(null);
  const [loadError, setLoadError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const pendingRequest = useRef<TradeInput | null>(null);
  const legacyRetryId = useRef<string | null>(null);
  const inFlight = useRef(false);
  const pendingDraft = useRef(false);
  const readVersion = useRef(0);
  const draftKey = useRef(`aqsp:operation-draft:${loadUserId()}`);

  async function load(code: string) {
    const version = ++readVersion.current;
    setLoadError(""); setJournal(null);
    if (!/^\d{6}$/.test(code)) return;
    try {
      const current = await api.portfolio();
      if (version !== readVersion.current) return;
      const trades = await api.tradeJournal(code);
      if (version === readVersion.current) { setPortfolio(current); setJournal(trades); }
    } catch (reason) {
      if (version === readVersion.current) setLoadError(reason instanceof Error ? reason.message : "读取持仓失败");
    }
  }

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(draftKey.current);
      if (raw) {
        const draft: unknown = JSON.parse(raw);
        if (draft && typeof draft === "object" && "form" in draft && "target" in draft && "id" in draft && typeof draft.id === "string") {
          const input = draft.form as OperationInput;
          if (input && (input.direction === "buy" || input.direction === "sell") && [input.code, input.date, input.price, input.shares].every((value) => typeof value === "string") && !operationError(input, shanghaiDate())) {
            setForm(input); setTarget(draft.target as OperationTarget); setUncertain(true);
            pendingRequest.current = "request" in draft && draft.request ? draft.request as TradeInput : null;
            legacyRetryId.current = pendingRequest.current ? null : draft.id;
            pendingDraft.current = true;
            setError("上次提交被中断，结果尚未确认。请原样重试，后端会防止重复记账。");
          }
        }
      }
    } catch { /* 存储不可用时，提交前会明确阻止。 */ }
    const open = (event: Event) => {
      if (inFlight.current) return;
      if (pendingDraft.current) { dialog.current?.showModal(); return; }
      const detail = (event as CustomEvent<OperationTarget>).detail ?? {};
      setTarget(detail);
      setForm({ direction: detail.direction ?? "buy", code: detail.code?.trim() ?? "", price: "", shares: "", date: shanghaiDate(), actualFees: "" });
      setError(""); setUncertain(false); setPortfolio(null); setJournal(null);
      pendingRequest.current = null; legacyRetryId.current = null;
      dialog.current?.showModal();
    };
    window.addEventListener(RECORD_OPERATION_EVENT, open);
    return () => { window.removeEventListener(RECORD_OPERATION_EVENT, open); readVersion.current++; };
  }, []);
  useEffect(() => { void load(form.code.trim()); }, [form.code, target]);

  const holding = portfolio?.holdings.find((row) => row.code === form.code.trim());
  const identityChanged = !!target?.positionId && journal?.position.position_id !== target.positionId;
  const close = () => { if (!inFlight.current) dialog.current?.close(); };
  const change = (patch: Partial<OperationInput>) => {
    setForm((value) => ({ ...value, ...patch })); setError("");
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (inFlight.current) return;
    const validation = operationError(form, shanghaiDate());
    if (validation) { setError(validation); return; }
    if (!uncertain && (!journal || identityChanged)) { setError(identityChanged ? "原持仓已退出或重开，请关闭旧页面并从当前持仓重新录入" : "请先读取并核对当前持仓"); return; }
    if (!uncertain && form.direction === "sell" && !journal?.position.position_id) { setError("请先读取并确认要卖出的持仓"); return; }
    if (!uncertain && form.direction === "sell" && journal && Number(form.shares) > journal.position.sellable_shares) { setError("数量超过当前可卖持仓，请核对 T+1 限制"); return; }
    if (!uncertain) {
      pendingRequest.current = {
        operation_id: crypto.randomUUID(), direction: form.direction, code: form.code.trim(), date: form.date,
        price: Number(form.price), shares: Number(form.shares), actual_fees: form.actualFees?.trim() ? Number(form.actualFees) : null,
        expected_position_id: journal!.position.position_id, expected_position_revision: journal!.position.revision,
        ...(target?.alertId ? { alert_id: target.alertId } : {}),
      };
    }
    const id = pendingRequest.current?.operation_id ?? legacyRetryId.current;
    if (!id) { setError("防重复记录不完整，请关闭后重新读取"); return; }
    try { sessionStorage.setItem(draftKey.current, JSON.stringify({ form, target, id, request: pendingRequest.current })); }
    catch { setError("浏览器无法保存本次防重复记录，尚未提交。请允许站点存储后重试。"); return; }
    inFlight.current = true; setBusy(true); setError("");
    try {
      if (pendingRequest.current) await api.recordTrade(pendingRequest.current);
      // 已发布旧客户端留下的未知提交只能原样走旧端点，不能再生成一笔新成交。
      else if (form.direction === "buy") await api.addHolding(form.code.trim(), Number(form.shares), Number(form.price), form.date, id);
      else if (target?.alertId) await api.closeAlertPosition(target.alertId, form.date, Number(form.price), Number(form.shares));
      else await api.closePosition(form.code.trim(), form.date, Number(form.price), Number(form.shares), holding?.cost ?? 0, id);
      try { sessionStorage.removeItem(draftKey.current); } catch { /* 原编号重试仍幂等。 */ }
      pendingDraft.current = false; pendingRequest.current = null; legacyRetryId.current = null;
      setUncertain(false); announcePortfolioChange();
      dialog.current?.close(); setTarget(null);
      toast.success(form.direction === "buy" ? "买入成交已保存；数量与成本已更新，当日新增数量受 T+1 限制" : "卖出成交已保存；请核对余仓和后续计划");
      void api.runLiveRiskCheck(undefined, false).then(announcePortfolioChange).catch(() => toast.warning("成交已保存；风险检查失败，请在今日页重新检查"));
    } catch (reason) {
      const unknown = !(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500;
      if (!unknown) { try { sessionStorage.removeItem(draftKey.current); } catch { /* 可用旧编号重试。 */ } pendingRequest.current = null; legacyRetryId.current = null; }
      pendingDraft.current = unknown; setUncertain(unknown);
      setError(unknown ? "提交结果尚未确认，已保留原请求和记录编号。请原样重试，后端会防止重复记账。" : `${reason instanceof Error ? reason.message : "保存失败"}。输入已保留，请更新持仓后核对重试。`);
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
          <label>股票代码<input className="aq-input" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoFocus value={form.code} disabled={busy || uncertain || !!target?.alertId || !!target?.positionId} onChange={(event) => change({ code: event.target.value })} required placeholder="6 位代码" /></label>
          <label>成交日期<input type="date" className="aq-input" max={shanghaiDate()} value={form.date} disabled={busy || uncertain} onChange={(event) => change({ date: event.target.value })} required /></label>
          <label>实际成交价格（元）<input type="number" className="aq-input" inputMode="decimal" min="0.0001" step="any" value={form.price} disabled={busy || uncertain} onChange={(event) => change({ price: event.target.value })} required placeholder="按券商成交填写" /></label>
          <label>成交数量（股）<input type="number" className="aq-input" inputMode="decimal" min="0.0001" step="any" value={form.shares} disabled={busy || uncertain} onChange={(event) => change({ shares: event.target.value })} required /></label>
          <label>本笔实际总费用（元，可后补）<input type="number" className="aq-input" inputMode="decimal" min="0" step="any" value={form.actualFees ?? ""} disabled={busy || uncertain} onChange={(event) => change({ actualFees: event.target.value })} placeholder="未知留空；核实免费才填 0" /></label>
        </div>
        <div className="aq-operation-note">{loadError ? <><span role="alert">读取持仓失败：{loadError}</span><button type="button" className="aq-btn" disabled={busy} onClick={() => void load(form.code.trim())}><RefreshCw aria-hidden="true" />重试读取</button></> : !/^\d{6}$/.test(form.code.trim()) ? "填写代码后核对当前持仓" : !journal ? "正在读取当前持仓…" : identityChanged ? <span role="alert">原持仓已退出或重开，不能从旧页面录入新仓</span> : journal.position.position_id ? `${holding?.name || form.code.trim()} · 持有 ${journal.position.shares} 股 · 当前可卖 ${journal.position.sellable_shares} 股` : "当前没有这只持仓；实际买入将建立新仓"}</div>
        {form.direction === "buy" ? <p className="aq-operation-note">这是新增成交，会增加持仓。已在持仓中记过的数量不要重复录入；普通买入不会自动算作做 T 买回。</p> : null}
        {error ? <p className="aq-operation-error" role="alert">{error}</p> : null}
        <footer className="aq-operation-foot"><span>仅记录成交，不代下单</span><button className="aq-btn aq-btn-primary" type="submit" disabled={busy || (!uncertain && (!journal || identityChanged || (form.direction === "sell" && !journal.position.position_id)))}><Check aria-hidden="true" />{busy ? "正在保存…" : uncertain ? "原样重试成交" : "保存成交"}</button></footer>
      </form>
    </dialog>
  );
}
