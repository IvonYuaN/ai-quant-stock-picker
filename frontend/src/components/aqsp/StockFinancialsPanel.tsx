import { useEffect, useState } from "react";
import { EmptyState, LoadingState, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { api, type StockFinancials } from "@/lib/api";
import { formatStockFinancialValue, stockFinancialPeriod } from "@/lib/stock-financial-view";

const FINANCIAL_FIELDS = [
  ["营业总收入", "revenue", false],
  ["收入同比", "revenue_yoy", true],
  ["净利润", "net_profit", false],
  ["净利润同比", "net_profit_yoy", true],
  ["基本每股收益", "eps", false],
  ["每股净资产", "bvps", false],
  ["净资产收益率", "roe", true],
  ["销售毛利率", "gross_margin", true],
  ["销售净利率", "net_margin", true],
  ["每股经营现金流", "op_cf_ps", false],
] as const;

export function StockFinancialsPanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<StockFinancials | null>(null);
  const [dataSymbol, setDataSymbol] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setData(null);
    setDataSymbol("");
    setLoading(true);
    setError(false);
    api.financials(symbol).then((result) => {
      if (active) {
        setData(result);
        setDataSymbol(symbol);
      }
    }).catch(() => {
      if (active) setError(true);
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [symbol, reload]);

  const current = dataSymbol === symbol ? data : null;
  const hasMetrics = current
    ? FINANCIAL_FIELDS.some(([, key]) => current[key] != null && current[key] !== "")
    : false;

  return (
    <section className="aq-detail-block aq-stock-financials">
      <SectionHeader
        title="最新财务摘要"
        description="同花顺最新报告期指标；不回填历史研究日，也不作为 PIT 回测证据。"
        count={current ? stockFinancialPeriod(current.period) : undefined}
      />
      {loading || dataSymbol !== symbol ? <LoadingState label="读取最新财务摘要…" /> : null}
      {!loading && error ? (
        <StatePanel tone="warn" action={<button type="button" className="aq-btn" onClick={() => setReload((value) => value + 1)}>重试财务摘要</button>}>
          财务数据暂不可用；本区不以行情或估算值补齐。
        </StatePanel>
      ) : null}
      {!loading && !error && !hasMetrics ? (
        <EmptyState title="暂无报告期财务摘要" detail="数据源没有返回可展示指标；暂无数据不代表指标为零。" />
      ) : null}
      {!loading && !error && hasMetrics && current ? (
        <>
          <p className="aq-detail-muted">来源：同花顺财务摘要 · 报告期 {stockFinancialPeriod(current.period)}</p>
          <dl className="aq-research-kv">
            {FINANCIAL_FIELDS.map(([label, key, percentage]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{formatStockFinancialValue(current[key], percentage)}</dd>
              </div>
            ))}
          </dl>
        </>
      ) : null}
    </section>
  );
}
