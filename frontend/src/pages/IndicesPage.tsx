// 指数：宽基指数与隔夜外围市场。
//
// 2026-11-09 新增模块。指数与外围是判断当天环境的外部坐标，单列一屏比塞进
// 「市场环境」页更直接；两处数据都是同一批接口，保留来源时点。
import { useEffect, useState } from "react";
import { TrendingUp } from "lucide-react";
import { LoadingState, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { api, type GlobalIndex, type IndexQuote } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatEvidenceTime } from "@/lib/format";

export function IndicesPage() {
  const [indices, setIndices] = useState<IndexQuote[] | null>(null);
  const [global, setGlobal] = useState<GlobalIndex[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    Promise.allSettled([api.indices(), api.globalIndices()]).then(([a, b]) => {
      if (!alive) return;
      if (a.status === "fulfilled") setIndices(a.value ?? []);
      if (b.status === "fulfilled") setGlobal(b.value ?? []);
      if (a.status === "rejected" && b.status === "rejected") {
        setError(a.reason instanceof Error ? a.reason.message : "读取失败");
      }
    });
    return () => { alive = false; };
  }, []);

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><TrendingUp aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>指数</h1>
          </div>
          <p className="aq-page-sub">宽基指数与隔夜外围；每行带来源时点，源未给时间就留空。</p>
        </div>
      </header>

      {error ? <StatePanel tone="warn">{error}</StatePanel> : null}

      <section className="aq-section" aria-label="宽基指数">
        <SectionHeader number="01 · 宽基" title="主要指数" description="A股宽基指数的最新点位与涨跌。" count={indices?.length} />
        {!indices ? <LoadingState label="正在读取指数…" /> : null}
        {indices && indices.length === 0 ? <p className="aq-note">指数源未返回数据。</p> : null}
        {indices && indices.length > 0 ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>指数</th>
                  <th className="aq-num">点位</th>
                  <th className="aq-num">涨跌</th>
                  <th className="aq-num">涨跌额</th>
                  <th>行情时点</th>
                </tr>
              </thead>
              <tbody>
                {indices.map((row) => (
                  <tr key={row.name}>
                    <td><strong>{row.name}</strong></td>
                    <td className="aq-num">{row.price.toFixed(2)}</td>
                    <td className={cn("aq-num", row.change_pct >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {row.change_pct >= 0 ? "+" : ""}{row.change_pct.toFixed(2)}%
                    </td>
                    <td className={cn("aq-num", row.change_amt >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {row.change_amt >= 0 ? "+" : ""}{row.change_amt.toFixed(2)}
                    </td>
                    <td>{row.as_of ? formatEvidenceTime(row.as_of) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="aq-section" aria-label="隔夜外围">
        <SectionHeader number="02 · 外围" title="隔夜外围市场" description="美股与港股上一交易日收盘；时间口径与 A 股不同。" count={global?.length} />
        {!global ? <LoadingState label="正在读取外围指数…" /> : null}
        {global && global.length === 0 ? <p className="aq-note">外围指数源未返回数据。</p> : null}
        {global && global.length > 0 ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>市场</th>
                  <th>指数</th>
                  <th className="aq-num">点位</th>
                  <th className="aq-num">涨跌</th>
                  <th>行情时点</th>
                </tr>
              </thead>
              <tbody>
                {global.map((row) => (
                  <tr key={row.key}>
                    <td>{row.region}</td>
                    <td><strong>{row.name}</strong></td>
                    <td className="aq-num">{row.price === null ? "—" : row.price.toFixed(2)}</td>
                    <td className={cn("aq-num", (row.change_pct ?? 0) >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {row.change_pct === null ? "—" : `${row.change_pct >= 0 ? "+" : ""}${row.change_pct.toFixed(2)}%`}
                    </td>
                    <td>{row.as_of ? formatEvidenceTime(row.as_of) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
