import { useEffect, useRef, useState } from "react";
import { api, type ActionWorksheet } from "@/lib/api";
import { saleReviewGuard } from "@/lib/position-workspace";
import { ALERTS_CHANGED_EVENT } from "@/lib/operation";

/** Acknowledge remaining exposure without calculating fees or inventing a trade. */
export function PositionSaleReview({ worksheet, disabled = false, onWorksheet }: {
  worksheet: ActionWorksheet; disabled?: boolean; onWorksheet: (next: ActionWorksheet) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(true);
  const inFlight = useRef(false);
  const latest = useRef(worksheet);
  latest.current = worksheet;
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => { setError(""); }, [worksheet.position_id, worksheet.revision]);
  if (worksheet.archived || !worksheet.sale_requires_review) return null;
  const sale = worksheet.recorded_sale;
  async function confirm() {
    if (disabled || inFlight.current || !worksheet.position_id) return;
    inFlight.current = true; setBusy(true); setError("");
    const requestedRevision = worksheet.revision;
    try {
      const next = await api.reviewPositionSale(worksheet.position_id, saleReviewGuard(worksheet));
      if (!active.current || latest.current.position_id !== worksheet.position_id || latest.current.revision !== requestedRevision) return;
      onWorksheet(next);
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) {
      if (active.current && latest.current.revision === requestedRevision) setError(`${reason instanceof Error ? reason.message : "核对结果尚未确认"}。请更新核对，未自动重复提交。`);
    } finally {
      inFlight.current = false;
      if (active.current) setBusy(false);
    }
  }
  return <section className="aq-refresh-warning" aria-label="卖出后的余仓核对">
    <strong>卖出后，先核对剩余仓位</strong>
    {sale ? <p>{sale.date} 已记录卖出 {sale.shares} 股，成交价 {sale.price.toFixed(2)} 元。</p> : null}
    <p>当前剩余 {worksheet.position_snapshot.shares} 股，可卖 {worksheet.position_snapshot.sellable_shares} 股，记录成本 {worksheet.position_snapshot.cost.toFixed(4)} 元。</p>
    <p>原观察条件停止沿用；核对后，按余仓重新分析并采用新条件。费用试算可稍后再做。</p>
    <button className="aq-btn" type="button" disabled={disabled || busy || !sale?.id || !worksheet.revision} onClick={() => void confirm()}>{busy ? "正在核对…" : "已核对余仓，重新分析策略"}</button>
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}
