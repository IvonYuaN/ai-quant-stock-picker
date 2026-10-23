// 持仓与自选的唯一入口：服务端持仓、本地自选和风险事件在同一页关联。
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Plus, RefreshCw, ShieldCheck, Trash2, Wallet } from "lucide-react";
import { toast } from "sonner";
import { api, type AlertEvent, type PortfolioData } from "@/lib/api";
import { normalizePortfolio } from "@/lib/portfolio-view";
import { normalizeAlerts } from "@/lib/alert-view";
import { ALERTS_CHANGED_EVENT, PORTFOLIO_CHANGED_EVENT } from "@/lib/holding-view";
import { Badge, LoadingState, SectionHeader, StatePanel, ToneCallout } from "@/components/ui/primitives";
import { HoldingsWatchlist, openOperation } from "@/components/aqsp/HoldingsWatchlist";
import { useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { cn } from "@/lib/utils";
import { changeClass, formatMoney, formatSignedPct } from "@/lib/format";

export function HoldingsPage({ renderRiskSettings }: { renderRiskSettings?: (code: string) => ReactNode }) {
  const [data, setData] = useState<PortfolioData | null>(null);
  const [alerts, setAlerts] = useState<AlertEvent[]>([]);
  const [error, setError] = useState("");
  const [alertError, setAlertError] = useState("");
  const [loading, setLoading] = useState(true);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const portfolioSequence = useRef(0);
  const alertSequence = useRef(0);
  const snapshotState = useAqspSnapshot();
  const snapshot = snapshotState.data;
  const currentCandidates = snapshot && !snapshot.meta?.historical && !snapshotState.switching ? snapshot.candidates : [];

  const load = useCallback(async () => {
    const sequence = ++portfolioSequence.current;
    setLoading(true);
    try {
      const result = normalizePortfolio(await api.portfolio());
      if (sequence !== portfolioSequence.current) return;
      setData(result);
      setError("");
    } catch (err) {
      if (sequence === portfolioSequence.current) setError(err instanceof Error ? err.message : "持仓读取失败");
    } finally {
      if (sequence === portfolioSequence.current) setLoading(false);
    }
  }, []);

  const loadAlerts = useCallback(async () => {
    const sequence = ++alertSequence.current;
    setAlertsLoading(true);
    try {
      const result = normalizeAlerts(await api.alerts());
      if (sequence !== alertSequence.current) return;
      setAlerts(result.events);
      setAlertError("");
    } catch (err) {
      if (sequence === alertSequence.current) setAlertError(err instanceof Error ? err.message : "提醒读取失败");
    } finally {
      if (sequence === alertSequence.current) setAlertsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void loadAlerts();
    const onPortfolio = () => { void load(); void loadAlerts(); };
    const onAlerts = () => { void loadAlerts(); };
    window.addEventListener(PORTFOLIO_CHANGED_EVENT, onPortfolio);
    window.addEventListener(ALERTS_CHANGED_EVENT, onAlerts);
    return () => {
      portfolioSequence.current += 1;
      alertSequence.current += 1;
      window.removeEventListener(PORTFOLIO_CHANGED_EVENT, onPortfolio);
      window.removeEventListener(ALERTS_CHANGED_EVENT, onAlerts);
    };
  }, [load, loadAlerts]);

  async function run(action: () => Promise<PortfolioData>, successMessage: string): Promise<void> {
    setBusy(true);
    const sequence = ++portfolioSequence.current;
    try {
      setData(normalizePortfolio(await action()));
      setError("");
      toast(successMessage);
      window.dispatchEvent(new Event(PORTFOLIO_CHANGED_EVENT));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败，记录未变更");
    } finally {
      setBusy(false);
      if (portfolioSequence.current === sequence) setLoading(false);
    }
  }

  const totals = data?.totals;
  const candidateStatus = snapshotState.switching || (snapshotState.loading && !snapshot)
    ? "今日候选读取中。"
    : snapshotState.error ? "今日候选暂不可用，不推断命中状态。"
    : snapshot?.meta?.historical ? `当前为 ${snapshot.selected_date} 历史记录，不作为今日命中。`
    : snapshot ? `候选对照 ${snapshot.selected_date}。` : "今日候选暂不可用。";

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><Wallet aria-hidden="true" /> AQSP · 我的持仓与自选</p>
          <div className="aq-title-row"><h1>我的持仓</h1>{data ? <Badge>{data.holdings.length} 只持仓</Badge> : null}</div>
          <p className="aq-page-sub">先处理风险，再看持仓与关注标的。{data ? `已记录 ${data.closed.length} 笔卖出。` : ""}</p>
        </div>
        <div className="aq-row-actions">
          <button type="button" className="aq-btn aq-btn-primary" onClick={() => openOperation("buy")}><Plus aria-hidden="true" /> 我操作了</button>
          <button type="button" className="aq-btn" onClick={() => void run(() => api.refreshPortfolio(), "已刷新行情与盈亏")} disabled={busy || loading}><RefreshCw className={cn((busy || loading) && "aq-spin")} aria-hidden="true" /> 刷新持仓</button>
        </div>
      </header>

      <p className="aq-privacy-note"><ShieldCheck aria-hidden="true" /><span>持仓由你成交后录入，保存在按访客隔离的服务端台账；自选保留在此浏览器。系统不代下单。</span></p>
      {totals ? <div className="aq-total-grid">
        <div className="aq-total-card"><span>持仓市值</span><b className="aq-num">{formatMoney(totals.market_value)}</b></div>
        <div className="aq-total-card"><span>持仓成本</span><b className="aq-num">{formatMoney(totals.cost)}</b></div>
        <div className="aq-total-card"><span>浮动盈亏</span><b className={cn("aq-num", changeClass(totals.pnl))}>{formatMoney(totals.pnl)}</b></div>
        <div className="aq-total-card"><span>浮动收益率</span><b className={cn("aq-num", changeClass(totals.pnl_pct))}>{formatSignedPct(totals.pnl_pct)}</b></div>
        <div className="aq-total-card"><span>已实现盈亏</span><b className={cn("aq-num", changeClass(data?.realized_pnl))}>{formatMoney(data?.realized_pnl)}</b></div>
      </div> : null}
      {error ? <StatePanel tone="warn">持仓读取失败：{error}。{data ? "保留上次读取记录。" : "自选仍可查看；持仓数量暂未知。"}<button type="button" className="aq-btn" disabled={loading} onClick={() => void load()}>重试持仓</button></StatePanel> : null}
      {loading && !data ? <LoadingState label="正在读取持仓…" /> : null}
      {alertError ? <StatePanel tone="warn">风险提醒读取失败：{alertError}，当前分组不能代表最新风险状态。<button type="button" className="aq-btn" disabled={alertsLoading} onClick={() => void loadAlerts()}>重试提醒</button></StatePanel> : alertsLoading ? <p className="aq-note">正在核对风险提醒…</p> : null}
      <HoldingsWatchlist portfolio={data} alerts={alerts} candidates={snapshotState.error ? [] : currentCandidates} candidateDate={snapshot?.selected_date || ""} candidateStatus={candidateStatus} renderRiskSettings={renderRiskSettings} />

      {data && data.closed.length > 0 ? <section className="aq-section">
        <SectionHeader title="历史成交" description="已实现盈亏按录入成交价计算 · 红盈绿亏" count={`${data.closed.length} 笔`} />
        <div className="aq-table-wrap"><table className="aq-table"><thead><tr><th>卖出日</th><th>代码 / 名称</th><th className="aq-num">成交价</th><th className="aq-num">成本价</th><th className="aq-num">股数</th><th className="aq-num">已实现盈亏</th><th className="aq-num">收益率</th><th /></tr></thead><tbody>
          {data.closed.map((row, index) => <tr key={`${row.code}-${row.date}-${index}`}>
            <td className="aq-num">{row.date}</td><td><strong>{row.name}</strong><div className="aq-code">{row.code}</div></td>
            <td className="aq-num">{row.price.toFixed(2)}</td><td className="aq-num">{row.cost.toFixed(4)}</td><td className="aq-num">{row.shares}</td>
            <td className={cn("aq-num", changeClass(row.pnl))}>{formatMoney(row.pnl)}</td><td className={cn("aq-num", changeClass(row.pnl_pct))}>{formatSignedPct(row.pnl_pct)}</td>
            <td><button type="button" className="aq-btn aq-btn-danger aq-btn-icon" title="删除这条成交记录" aria-label={`删除 ${row.code} ${row.date} 成交记录`} disabled={busy} onClick={() => void run(() => api.removeClosed(index), "已删除该条记录")}><Trash2 aria-hidden="true" /></button></td>
          </tr>)}
        </tbody></table></div>
      </section> : null}
      <ToneCallout tone="neutral" title="T+1 与台账口径" detail="当日买入部分当日不可卖；补录时请使用实际成交日。盈亏按录入成本计算，不含未录入的费用。提醒用于复核，实际可卖数量以交易软件为准。" />
    </div>
  );
}
