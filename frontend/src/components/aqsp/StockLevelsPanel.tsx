import { useEffect, useState } from "react";
import { EmptyState, LoadingState, SectionHeader, StatePanel, Tag } from "@/components/ui/primitives";
import { api, type StockLevelsPayload } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { availableStockLevels, STOCK_LEVEL_LABELS, unavailableStockLevels } from "@/lib/stock-level-view";

export function StockLevelsPanel({ symbol }: { symbol: string }) {
  const [payload, setPayload] = useState<StockLevelsPayload | null>(null);
  const [loadedSymbol, setLoadedSymbol] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let current = true;
    setPayload(null);
    setLoadedSymbol("");
    setLoading(true);
    setError("");
    api.stockLevels(symbol).then((result) => {
      if (current) {
        setPayload(result);
        setLoadedSymbol(symbol);
      }
    }).catch((reason: unknown) => {
      if (current) setError(reason instanceof Error ? reason.message : "结构价位读取失败");
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [symbol]);

  const current = loadedSymbol === symbol ? payload : null;
  const available = current ? availableStockLevels(current.levels) : [];
  const unavailable = current ? unavailableStockLevels(current.levels) : [];

  return (
    <section className="aq-detail-block aq-stock-levels">
      <SectionHeader
        title="结构价位"
        description="仅展示数据可审计的日线结构，不作为买卖信号。"
        count={current ? available.length + " / " + current.levels.length + " 类可计算" : undefined}
      />
      {loading || loadedSymbol !== symbol ? <LoadingState label="读取不复权结构价位…" /> : null}
      {!loading && error ? <StatePanel tone="warn">结构价位暂不可用：{error}</StatePanel> : null}
      {!loading && !error && current ? <>
        <StatePanel tone={current.price_basis === "pit_as_of" ? "neutral" : "warn"}>{current.warning} 价位不叠加到展示 K 线上。</StatePanel>
        {available.length ? <div className="aq-level-list">{available.map((item) => (
          <article className="aq-level-row" key={item.type}>
            <div className="aq-level-heading">
              <h3>{STOCK_LEVEL_LABELS[item.type] || item.type}</h3>
              <Tag>{item.as_of || current.as_of || "时点未知"} · {item.price_basis}</Tag>
            </div>
            <div className="aq-level-values">
              {item.levels.map((level) => <div key={level.label}><span>{level.label}</span><b>{level.price.toFixed(2)}</b>{level.date ? <small>{level.date}</small> : null}</div>)}
            </div>
            <p className="aq-detail-muted">{item.formula} · {item.source}</p>
          </article>
        ))}</div> : <EmptyState title="没有可计算的结构价位" detail="raw 日线缺失、过期或样本不足时，不用展示 K 线推造价位。" />}
        {unavailable.length ? <details className="aq-level-unavailable">
          <summary>其他价位类型不可用 <span>{unavailable.length}</span></summary>
          <ul>{unavailable.map((item) => <li key={item.type}><b>{STOCK_LEVEL_LABELS[item.type] || item.type}</b><span>{item.reason}</span></li>)}</ul>
        </details> : null}
        <p className="aq-detail-muted">来源 {current.source || "未标注"} · 日线截至 {current.as_of || "未标注"} · 取数 {current.fetched_at ? formatEvidenceTime(current.fetched_at) : "未标注"}{current.adjustment_factor_source ? ` · PIT 因子 ${current.adjustment_factor_source} 截至 ${current.adjustment_factor_as_of || "未标注"}` : " · PIT 因子不可用"}</p>
      </> : null}
    </section>
  );
}
