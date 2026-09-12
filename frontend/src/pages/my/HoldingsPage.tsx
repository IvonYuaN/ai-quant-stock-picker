// 我的持仓：本机持仓台账（加权成本 + 浮动盈亏 + 已清仓已实现盈亏）。
//
// 取代原来的「我的测试」——那只是个临时对照便签，价值低。
// 对照所需的额外列（换手率 / 市值）已补进「我的自选」，能力没有丢。
//
// 数据存后端本地目录（按访客隔离），不上传、不进仓库。盈亏按 A 股口径：盈利红、亏损绿。
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Plus, RefreshCw, ShieldCheck, Trash2, Wallet } from "lucide-react";
import { toast } from "sonner";
import { api, type PortfolioData } from "@/lib/api";
import { normalizePortfolio } from "@/lib/portfolio-view";
import {
  Badge,
  EmptyState,
  LoadingState,
  SectionHeader,
  StatePanel,
  ToneCallout,
} from "@/components/ui/primitives";
import { useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { cn } from "@/lib/utils";
import { changeClass, formatMoney, formatSignedPct } from "@/lib/format";

function today(): string {
  return new Date().toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" }).replace(/\//g, "-");
}

function normalizeDate(value: string): string {
  const [year, month, day] = value.split(/[-/]/);
  if (!year || !month || !day) return value;
  return `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
}

export function HoldingsPage() {
  const [data, setData] = useState<PortfolioData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [code, setCode] = useState("");
  const [shares, setShares] = useState("");
  const [cost, setCost] = useState("");
  const [closing, setClosing] = useState<{ code: string; date: string; price: string; shares: string; cost: string } | null>(
    null,
  );

  // 反向联动：持仓里哪些还在今天的候选名单上（掉出名单的也如实标出，便于复盘去留）
  const { data: snapshot } = useAqspSnapshot();
  const candidateByCode = useMemo(() => {
    const map = new Map<string, { score: number; status: string }>();
    for (const candidate of snapshot?.candidates ?? []) {
      map.set(candidate.symbol, { score: candidate.score, status: candidate.research_status });
    }
    return map;
  }, [snapshot]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(normalizePortfolio(await api.portfolio()));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "持仓读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(action: () => Promise<PortfolioData>, successMessage: string) {
    setBusy(true);
    try {
      setData(normalizePortfolio(await action()));
      toast(successMessage);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  const totals = data?.totals;

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Wallet aria-hidden="true" />
            AQSP · 我的线
          </p>
          <div className="aq-title-row">
            <h1>我的持仓</h1>
            {data?.updated ? <strong>{data.updated}</strong> : null}
          </div>
          <p className="aq-page-sub">
            共 {data?.holdings.length ?? 0} 笔持仓 · 已清仓 {data?.closed.length ?? 0} 笔
            {data?.last_refresh ? ` · 后台刷新 ${data.last_refresh}` : ""}
          </p>
        </div>
        <div className="aq-row-actions">
          <button
            type="button"
            className="aq-btn"
            onClick={() => void run(() => api.refreshPortfolio(), "已刷新行情与盈亏")}
            disabled={busy || loading}
          >
            <RefreshCw className={cn((busy || loading) && "aq-spin")} aria-hidden="true" />
            刷新行情
          </button>
        </div>
      </header>

      <p className="aq-privacy-note">
        <ShieldCheck aria-hidden="true" />
        <span>
          持仓由你手动录入，存本机后端数据目录（按访客隔离），不上传、不进仓库。
          盈亏按 A 股口径：<b className="aq-tone-up">盈利红</b> / <b className="aq-tone-down">亏损绿</b>。
        </span>
      </p>

      {totals ? (
        <div className="aq-total-grid">
          <div className="aq-total-card">
            <span>持仓市值</span>
            <b className="aq-num">{formatMoney(totals.market_value)}</b>
          </div>
          <div className="aq-total-card">
            <span>持仓成本</span>
            <b className="aq-num">{formatMoney(totals.cost)}</b>
          </div>
          <div className="aq-total-card">
            <span>浮动盈亏</span>
            <b className={cn("aq-num", changeClass(totals.pnl))}>{formatMoney(totals.pnl)}</b>
          </div>
          <div className="aq-total-card">
            <span>浮动收益率</span>
            <b className={cn("aq-num", changeClass(totals.pnl_pct))}>{formatSignedPct(totals.pnl_pct)}</b>
          </div>
          <div className="aq-total-card">
            <span>已实现盈亏</span>
            <b className={cn("aq-num", changeClass(data?.realized_pnl))}>{formatMoney(data?.realized_pnl)}</b>
          </div>
        </div>
      ) : null}

      <section className="aq-section">
        <SectionHeader title="加一笔持仓" description="同代码按加权平均成本合并（加仓）" />
        <div className="aq-toolbar">
          <input
            className="aq-input"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            placeholder="6 位代码，如 600519"
            inputMode="numeric"
          />
          <input
            className="aq-input"
            value={shares}
            onChange={(event) => setShares(event.target.value)}
            placeholder="股数"
            inputMode="decimal"
          />
          <input
            className="aq-input"
            value={cost}
            onChange={(event) => setCost(event.target.value)}
            placeholder="成本价"
            inputMode="decimal"
          />
          <button
            type="button"
            className="aq-btn aq-btn-primary"
            disabled={busy}
            onClick={() => {
              const parsedCode = code.trim();
              const parsedShares = Number(shares);
              const parsedCost = Number(cost);
              if (!/^\d{6}$/.test(parsedCode)) return toast.error("代码必须是 6 位数字");
              if (!Number.isFinite(parsedShares) || parsedShares <= 0) return toast.error("股数必须大于 0");
              if (!Number.isFinite(parsedCost)) return toast.error("成本价必须是数字");
              void run(() => api.addHolding(parsedCode, parsedShares, parsedCost), `已录入 ${parsedCode}`).then(() => {
                setCode("");
                setShares("");
                setCost("");
              });
            }}
          >
            <Plus aria-hidden="true" />
            录入
          </button>
        </div>
      </section>

      {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
      {loading && !data ? <LoadingState label="正在读取持仓…" /> : null}

      {data && data.holdings.length === 0 ? (
        <EmptyState title="还没有持仓" detail="在上方录入代码、股数与成本价即可建立台账。" icon={Wallet} />
      ) : null}

      {data && data.holdings.length > 0 ? (
        <div className="aq-table-wrap">
          <table className="aq-table">
            <thead>
              <tr>
                <th>代码 / 名称</th>
                <th className="aq-num">股数</th>
                <th className="aq-num">成本价</th>
                <th className="aq-num">现价</th>
                <th className="aq-num">市值</th>
                <th className="aq-num">浮动盈亏</th>
                <th className="aq-num">收益率</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.holdings.map((row) => (
                <Fragment key={row.code}>
                  <tr>
                    <td>
                      <strong>{row.name}</strong>
                      <div className="aq-code">{row.code}</div>
                      {candidateByCode.has(row.code) ? (
                        <Badge tone="ok">
                          今日候选 {candidateByCode.get(row.code)?.score.toFixed(1)}
                        </Badge>
                      ) : (
                        <Badge tone="neutral">今日不在候选</Badge>
                      )}
                    </td>
                    <td className="aq-num">{row.shares}</td>
                    <td className="aq-num">{row.cost.toFixed(4)}</td>
                    <td className="aq-num">{row.price.toFixed(2)}</td>
                    <td className="aq-num">{formatMoney(row.market_value)}</td>
                    <td className={cn("aq-num", changeClass(row.pnl))}>{formatMoney(row.pnl)}</td>
                    <td className={cn("aq-num", changeClass(row.pnl_pct))}>{formatSignedPct(row.pnl_pct)}</td>
                    <td className="aq-num">
                      <div className="aq-row-actions">
                        <button
                          type="button"
                          className="aq-btn"
                          title="记一笔清仓"
                          onClick={() =>
                            setClosing({
                              code: row.code,
                              date: today(),
                              price: row.price ? String(row.price) : "",
                              shares: String(row.shares),
                              cost: String(row.cost),
                            })
                          }
                        >
                          清仓
                        </button>
                        <button
                          type="button"
                          className="aq-btn aq-btn-danger aq-btn-icon"
                          title="移除持仓（不记盈亏）"
                          disabled={busy}
                          onClick={() => void run(() => api.removeHolding(row.code), `已移除 ${row.code}`)}
                        >
                          <Trash2 aria-hidden="true" />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {closing?.code === row.code ? (
                    <tr>
                      <td colSpan={8}>
                        <div className="aq-closing-form">
                          <p className="aq-label">记一笔清仓（计入已实现盈亏）</p>
                          <div className="aq-toolbar">
                            <input
                              className="aq-input"
                              value={closing.date}
                              onChange={(event) => setClosing({ ...closing, date: event.target.value })}
                              placeholder="清仓日期 YYYY-MM-DD"
                            />
                            <input
                              className="aq-input"
                              value={closing.price}
                              onChange={(event) => setClosing({ ...closing, price: event.target.value })}
                              placeholder="清仓价"
                            />
                            <input
                              className="aq-input"
                              value={closing.shares}
                              onChange={(event) => setClosing({ ...closing, shares: event.target.value })}
                              placeholder="股数"
                            />
                            <input
                              className="aq-input"
                              value={closing.cost}
                              onChange={(event) => setClosing({ ...closing, cost: event.target.value })}
                              placeholder="成本价"
                            />
                            <button
                              type="button"
                              className="aq-btn aq-btn-primary"
                              disabled={busy}
                              onClick={() => {
                                const price = Number(closing.price);
                                const qty = Number(closing.shares);
                                const unitCost = Number(closing.cost);
                                const date = normalizeDate(closing.date.trim());
                                if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return toast.error("日期格式应为 YYYY-MM-DD");
                                if (!Number.isFinite(price) || price <= 0) return toast.error("清仓价必须大于 0");
                                if (!Number.isFinite(qty) || qty <= 0) return toast.error("股数必须大于 0");
                                if (!Number.isFinite(unitCost)) return toast.error("成本价必须是数字");
                                void run(
                                  () => api.closePosition(row.code, date, price, qty, unitCost),
                                  `已记录 ${row.code} 清仓`,
                                ).then(() => setClosing(null));
                              }}
                            >
                              确认清仓
                            </button>
                            <button type="button" className="aq-btn" onClick={() => setClosing(null)}>
                              取消
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {data && data.closed.length > 0 ? (
        <section className="aq-section">
          <SectionHeader
            title="已清仓"
            description="已实现盈亏 · 按 A 股口径红盈绿亏"
            count={`${data.closed.length} 笔`}
          />
          <div className="aq-table-wrap">
            <table className="aq-table">
              <thead>
                <tr>
                  <th>清仓日</th>
                  <th>代码 / 名称</th>
                  <th className="aq-num">清仓价</th>
                  <th className="aq-num">成本价</th>
                  <th className="aq-num">股数</th>
                  <th className="aq-num">已实现盈亏</th>
                  <th className="aq-num">收益率</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.closed.map((row, index) => (
                  <tr key={`${row.code}-${row.date}-${index}`}>
                    <td className="aq-num">{row.date}</td>
                    <td>
                      <strong>{row.name}</strong>
                      <div className="aq-code">{row.code}</div>
                    </td>
                    <td className="aq-num">{row.price.toFixed(2)}</td>
                    <td className="aq-num">{row.cost.toFixed(4)}</td>
                    <td className="aq-num">{row.shares}</td>
                    <td className={cn("aq-num", changeClass(row.pnl))}>{formatMoney(row.pnl)}</td>
                    <td className={cn("aq-num", changeClass(row.pnl_pct))}>{formatSignedPct(row.pnl_pct)}</td>
                    <td className="aq-num">
                      <button
                        type="button"
                        className="aq-btn aq-btn-danger aq-btn-icon"
                        title="删除这条记录"
                        disabled={busy}
                        onClick={() => void run(() => api.removeClosed(index), "已删除该条记录")}
                      >
                        <Trash2 aria-hidden="true" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <ToneCallout
        tone="neutral"
        title="台账口径说明"
        detail="浮动盈亏按录入成本与最新行情计算；已实现盈亏按清仓价与录入成本计算。系统不代下单、不接券商接口，这里只做记录与统计。"
      />
    </div>
  );
}
