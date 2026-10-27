import { useEffect, useRef, useState } from "react";
import { api, ApiError, loadUserId, type TradeFeeInput, type TradeJournal, type TradeLinkInput, type TradeReceipt } from "@/lib/api";
import { announcePortfolioChange, PORTFOLIO_CHANGED_EVENT, requestOperation } from "@/lib/operation";
import { actualFeeError, allocationError, netDifferenceText, unusedReceiptShares } from "@/lib/trade-journal";

type Pending = { kind: "fees"; id: string; input: TradeFeeInput } | { kind: "link"; id: string; input: TradeLinkInput };
const receiptLabel = (receipt: TradeReceipt) => `${receipt.date} · ${receipt.direction === "sell" ? "卖出" : "买入"} ${receipt.shares} 股 × ${receipt.price} 元 · ${receipt.receipt_id.slice(-8)}`;

export function TradeJournalPanel({ code, positionId, archived = false }: { code: string; positionId: string; archived?: boolean }) {
  const [data, setData] = useState<TradeJournal | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [sellId, setSellId] = useState("");
  const [buyId, setBuyId] = useState("");
  const [shares, setShares] = useState("");
  const [sameDay, setSameDay] = useState(false);
  const [crossPosition, setCrossPosition] = useState(false);
  const [feeId, setFeeId] = useState("");
  const [fees, setFees] = useState("");
  const inFlight = useRef(false);
  const generation = useRef(0);
  const storageKey = `aqsp:trade-edit:${loadUserId()}:${code}`;
  const liveKey = useRef(storageKey); liveKey.current = storageKey;

  async function refresh() {
    const version = ++generation.current;
    try { const next = await api.tradeJournal(code); if (version === generation.current) { setData(next); setError(""); } }
    catch (reason) { if (version === generation.current) setError(reason instanceof Error ? reason.message : "成交读取失败"); }
  }
  useEffect(() => {
    setData(null); setError(""); setPending(null); setSellId(""); setBuyId(""); setShares(""); setFeeId(""); setFees("");
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (raw) {
        const value = JSON.parse(raw) as Pending;
        if ((value.kind === "fees" || value.kind === "link") && typeof value.id === "string" && typeof value.input?.operation_id === "string" && typeof value.input?.expected_revision === "string") setPending(value);
      }
    } catch { /* 下次提交前会检查持久保存能力。 */ }
    void refresh();
    const changed = () => { if (!inFlight.current) void refresh(); };
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, changed);
    return () => { generation.current++; window.removeEventListener(PORTFOLIO_CHANGED_EVENT, changed); };
  }, [code]);

  async function submit(request: Pending) {
    if (inFlight.current) return;
    const key = storageKey;
    try { sessionStorage.setItem(key, JSON.stringify(request)); }
    catch { setError("无法保存防重复记录，尚未提交；请允许站点存储后重试"); return; }
    ++generation.current; // Invalidate reads started before this mutation.
    inFlight.current = true; setBusy(true); setError("");
    try {
      const next = request.kind === "fees" ? await api.updateTradeFees(request.id, request.input) : await api.saveTradeLink(request.id, request.input);
      try { sessionStorage.removeItem(key); } catch { /* 同一编号仍可恢复。 */ }
      if (key === liveKey.current) { setData(next); setPending(null); setShares(""); setSameDay(false); setCrossPosition(false); void refresh(); }
      announcePortfolioChange();
    } catch (reason) {
      const unknown = !(reason instanceof ApiError) || reason.status === 0 || reason.status >= 500;
      if (unknown) {
        if (key === liveKey.current) { setPending(request); setError("提交结果未确认。请原样重试，保留的编号可防止重复核算。"); }
      } else {
        try { sessionStorage.removeItem(key); } catch { /* 明确拒绝后可重新提交。 */ }
        if (key === liveKey.current) { setPending(null); setError(`${reason.message}。请更新记录后重新核对。`); }
      }
    } finally { inFlight.current = false; if (key === liveKey.current) setBusy(false); }
  }
  const locked = busy || !!pending;
  const sell = data?.receipts.find((receipt) => receipt.receipt_id === sellId);
  const buy = data?.receipts.find((receipt) => receipt.receipt_id === buyId);
  function link() {
    if (!data || locked) return;
    const allocation = { sell_receipt_id: sellId, buy_receipt_id: buyId, shares: Number(shares), confirm_same_day_order: sameDay, confirm_cross_position: crossPosition };
    const issue = allocationError(sell, buy, allocation, data);
    if (issue) { setError(issue); return; }
    void submit({ kind: "link", id: crypto.randomUUID(), input: { operation_id: crypto.randomUUID(), expected_revision: data.revision, allocations: [allocation] } });
  }
  function saveFees() {
    if (!data || locked || !feeId) return;
    const issue = actualFeeError(fees);
    if (issue) { setError(issue); return; }
    void submit({ kind: "fees", id: feeId, input: { operation_id: crypto.randomUUID(), expected_revision: data.revision, actual_fees: fees.trim() ? Number(fees) : null } });
  }
  if (data && !data.receipts.length && !pending && !error) return <details className="aq-plan-help" aria-label="实际成交与买回核对"><summary>成交记录 · 暂无逐笔凭据</summary><p className="aq-note">已有持仓的汇总成本不会自动变成买入成交；已记过的数量请勿再次录入。</p><button className="aq-btn" type="button" disabled={busy} onClick={() => void refresh()}>更新记录</button></details>;
  return <section className="aq-trade-journal" aria-label="实际成交与买回核对">
    <div className="aq-trade-title"><h3>实际成交与买回核对</h3>{!archived || data || error ? <button className="aq-btn" type="button" disabled={busy} onClick={() => void refresh()}>更新记录</button> : null}</div>
    {error ? <p role="alert">{error}</p> : null}
    {archived ? <p className="aq-note">这笔持仓已清仓，旧计划只读；仍可核对、更正成交费用与跨清仓重开的买回关系，不改变历史持仓数量。</p> : null}
    {pending ? <div className="aq-refresh-warning" role="status"><span>上次费用或关联提交结果未知，先确认这一次，避免重复操作。</span><button className="aq-btn" type="button" disabled={busy} onClick={() => void submit(pending)}>{busy ? "确认中…" : "原样重试核对"}</button></div> : null}
    {data ? <>
      <div className="aq-trade-progress"><span>已卖 <b>{data.summary.sold_shares}</b> 股</span><span>已关联买回 <b>{data.summary.bought_back_shares}</b> 股</span><span>尚未关联买回 <b>{data.summary.unmatched_sell_shares}</b> 股</span></div>
      <p className="aq-trade-net" role="status">{netDifferenceText(data.summary.net_difference, data.summary.bought_back_shares)}</p>
      <p className="aq-note">这里只核对该代码已记录、且由你明确关联的实际成交，含历史仓位。未买回的卖出款不算盈利；一次价差不代表整仓回本。</p>
      {!archived && data.position.position_id === positionId ? <button className="aq-btn" type="button" disabled={locked} onClick={() => requestOperation({ direction: "buy", code, positionId })}>我已实际买回 · 录入新增成交</button> : null}
      <details className="aq-plan-help"><summary>核对成交、买回关系和实际费用</summary>
        {!data.receipts.length ? <p>这里还没有逐笔成交凭据。已有持仓的汇总成本不会自动变成买入成交；已记过的数量请勿再次录入。</p> : <>
          <ul className="aq-trade-receipts">{data.receipts.map((receipt) => <li key={receipt.receipt_id}><strong>{receiptLabel(receipt)}</strong><span>实际费用：{receipt.actual_fees === null ? "待核对" : `${receipt.actual_fees.toFixed(2)} 元`} · 可再关联 {unusedReceiptShares(receipt, data)} 股</span><span>仓位 {receipt.position_id.slice(-8)}{receipt.position_id === positionId ? " · 当前工作区" : " · 其他持仓时期"}</span></li>)}</ul>
          <form className="aq-trade-form" onSubmit={(event) => { event.preventDefault(); link(); }}>
            <h4>明确这次买回对应哪笔卖出</h4><p className="aq-note">可分笔、部分关联。普通加仓不会自动算作买回。</p>
            <label>卖出成交<select aria-label="卖出成交" className="aq-input" value={sellId} disabled={locked} onChange={(event) => { setSellId(event.target.value); setSameDay(false); setCrossPosition(false); }} required><option value="">选择实际卖出</option>{data.receipts.filter((receipt) => receipt.direction === "sell" && unusedReceiptShares(receipt, data) > 0).map((receipt) => <option key={receipt.receipt_id} value={receipt.receipt_id}>{receiptLabel(receipt)} · 剩 {unusedReceiptShares(receipt, data)} 股</option>)}</select></label>
            <label>买回成交<select aria-label="买回成交" className="aq-input" value={buyId} disabled={locked} onChange={(event) => { setBuyId(event.target.value); setSameDay(false); setCrossPosition(false); }} required><option value="">选择实际买入</option>{data.receipts.filter((receipt) => receipt.direction === "buy" && unusedReceiptShares(receipt, data) > 0).map((receipt) => <option key={receipt.receipt_id} value={receipt.receipt_id}>{receiptLabel(receipt)} · 剩 {unusedReceiptShares(receipt, data)} 股</option>)}</select></label>
            <label>这次关联股数<input className="aq-input" type="number" min="0.0001" step="any" value={shares} disabled={locked} onChange={(event) => setShares(event.target.value)} required /></label>
            {sell && buy && sell.date === buy.date ? <label className="aq-action-plan-confirm"><input type="checkbox" checked={sameDay} disabled={locked} onChange={(event) => setSameDay(event.target.checked)} />我已核对成交时间，确实先卖出、后买回</label> : null}
            {sell && buy && sell.position_id !== buy.position_id ? <label className="aq-action-plan-confirm"><input type="checkbox" checked={crossPosition} disabled={locked} onChange={(event) => setCrossPosition(event.target.checked)} />卖出仓位 {sell.position_id.slice(-8)} 与买入仓位 {buy.position_id.slice(-8)} 不同；我明确关联清仓重开两端，不继承旧计划</label> : null}
            <button className="aq-btn" type="submit" disabled={locked}>保存这次关联</button>
          </form>
          <form className="aq-trade-form" onSubmit={(event) => { event.preventDefault(); saveFees(); }}><h4>补录或更正实际费用</h4><label>成交<select aria-label="成交" className="aq-input" value={feeId} disabled={locked} onChange={(event) => { setFeeId(event.target.value); const receipt = data.receipts.find((row) => row.receipt_id === event.target.value); setFees(receipt?.actual_fees == null ? "" : String(receipt.actual_fees)); }} required><option value="">选择成交</option>{data.receipts.map((receipt) => <option key={receipt.receipt_id} value={receipt.receipt_id}>{receiptLabel(receipt)}</option>)}</select></label><label>本笔实际总费用（元）<input className="aq-input" type="number" min="0" step="any" value={fees} disabled={locked} onChange={(event) => setFees(event.target.value)} placeholder="未知留空；核实免费才填 0" /></label><button className="aq-btn" type="submit" disabled={locked || !feeId}>保存实际费用</button></form>
          {data.links.some((row) => row.allocations.length) ? <div className="aq-trade-links"><h4>已确认的买回关系</h4>{data.links.filter((row) => row.allocations.length).map((row) => <div key={row.link_id}>{row.allocations.map((allocation, index) => <p key={index}>卖出 {allocation.sell_receipt_id.slice(-8)} → 买回 {allocation.buy_receipt_id.slice(-8)} · {allocation.shares} 股</p>)}<button className="aq-btn" type="button" disabled={locked} onClick={() => void submit({ kind: "link", id: row.link_id, input: { operation_id: crypto.randomUUID(), expected_revision: data.revision, allocations: [] } })}>取消这组关联，重新核对</button></div>)}</div> : null}
          <p className="aq-note">费用按本次匹配股数分摊。未匹配卖出款 {data.summary.unmatched_sell_proceeds.toFixed(2)} 元单列，不计入已完成差价。取消关联保留审计，不撤销真实成交。</p>
        </>}
      </details>
    </> : !error ? <p role="status">正在读取逐笔成交…</p> : null}
  </section>;
}
