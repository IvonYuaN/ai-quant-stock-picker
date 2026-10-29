import type { PositionPricePath } from "@/lib/api";
import { formatSignedPct } from "@/lib/format";

/** Completed-session evidence stays separate from live quotes and watch signals. */
export function PositionPricePathEvidence({ path }: { path?: PositionPricePath }) {
  if (!path) return null;
  const windows = path.status === "available" && path.schema === "completed-price-path-v1"
    && path.scope === "completed_daily_history" && path.price_mode === "raw"
    && typeof path.source === "string" && path.source.trim() && typeof path.as_of === "string" && path.as_of
    && Array.isArray(path.windows) ? path.windows.filter((row) =>
      row !== null && typeof row === "object" && [5, 20].includes(row.window)
      && typeof row.start_date === "string" && row.end_date === path.as_of && row.start_date <= row.end_date
      && [row.return_pct, row.max_drawdown_pct, row.volume_ratio, row.high, row.low].every(Number.isFinite)
      && row.low > 0 && row.high >= row.low && row.volume_ratio >= 0) : [];
  const issues = Array.isArray(path.issues) ? path.issues.filter((issue) => typeof issue === "string") : [];
  return <details className="aq-plan-help aq-completed-price-path" aria-label="完成日量价证据">
    <summary>查看这只股票的近期量价依据</summary>
    {windows.length ? <>
      {windows.map((row) => <div key={row.window}>
        <p><b>{row.window} 个已完成交易日</b> · {row.start_date} 至 {row.end_date}</p>
        <p>区间已见价格 {row.low.toFixed(2)}–{row.high.toFixed(2)} 元；收盘变化 {formatSignedPct(row.return_pct)}，期间最大回撤 {formatSignedPct(row.max_drawdown_pct)}。</p>
        <p>最后一天成交量是该窗口此前日均量的 {row.volume_ratio.toFixed(2)} 倍。</p>
      </div>)}
      <small>{path.source} · 不复权日线 · 截至 {path.as_of}</small>
      <p className="aq-note">这些是已完成交易日的事实，不能说明当前盘中已经放量、止跌或突破；区间高低也不等于买卖信号。</p>
    </> : <p>暂缺可核验的完成日量价窗口，不能据此判断反弹、回落或做 T 是否有量价支持。</p>}
    {issues.length ? <p className="aq-note">{issues.join("；")}</p> : null}
  </details>;
}
