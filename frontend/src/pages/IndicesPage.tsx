// 指数：宽基指数与隔夜外围市场。
//
// 2026-11-09 新增模块。指数与外围是判断当天环境的外部坐标，单列一屏比塞进
// 「市场环境」页更直接；两处数据都是同一批接口，保留来源时点。
//
// 🔴 2026-11-25 修正（数据诚实性）：原先两段共用一个 `error`，且只在「两段都失败」
// 时才置位。单段失败时那一段的 state 停在 null ⇒ 页面渲染成「正在读取…」永远转圈，
// 原因还被吞掉。现在每段各自结算（`settleSection`），失败段立刻显示原因，
// 与「源正常但今天没数据」严格区分。
import { useEffect, useState } from "react";
import { TrendingUp } from "lucide-react";
import { LoadingState, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { api, type GlobalIndex, type IndexQuote } from "@/lib/api";
import { cn } from "@/lib/utils";
import { changeClass, formatEvidenceTime } from "@/lib/format";
import { settleSection, type SectionResult } from "@/lib/safe";

export function IndicesPage() {
  // null = 还没结算（正在读取）；结算后一定是 ready 或 failed。
  const [indices, setIndices] = useState<SectionResult<IndexQuote> | null>(null);
  const [global, setGlobal] = useState<SectionResult<GlobalIndex> | null>(null);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([api.indices(), api.globalIndices()]).then(([a, b]) => {
      if (!alive) return;
      // 两段独立结算：一段失败不得连坐另一段，也不得让失败段停在「正在读取」。
      setIndices(settleSection(a, "宽基指数"));
      setGlobal(settleSection(b, "外围指数"));
    });
    return () => { alive = false; };
  }, []);

  const indexRows = indices?.state === "ready" ? indices.rows : null;
  const indexError = indices?.state === "failed" ? indices.message : "";
  const globalRows = global?.state === "ready" ? global.rows : null;
  const globalError = global?.state === "failed" ? global.message : "";

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

      <section className="aq-section" aria-label="宽基指数">
        <SectionHeader number="01 · 宽基" title="主要指数" description="A股宽基指数的最新点位与涨跌。" count={indexRows?.length} />
        {!indices ? <LoadingState label="正在读取指数…" /> : null}
        {indexError ? <StatePanel tone="warn">{indexError}</StatePanel> : null}
        {indexRows && indexRows.length === 0 ? <p className="aq-note">指数源未返回数据。</p> : null}
        {indexRows && indexRows.length > 0 ? (
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
                {indexRows.map((row) => (
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
        <SectionHeader number="02 · 外围" title="隔夜外围市场" description="美股与港股上一交易日收盘；时间口径与 A 股不同。" count={globalRows?.length} />
        {!global ? <LoadingState label="正在读取外围指数…" /> : null}
        {globalError ? <StatePanel tone="warn">{globalError}</StatePanel> : null}
        {globalRows && globalRows.length === 0 ? <p className="aq-note">外围指数源未返回数据。</p> : null}
        {globalRows && globalRows.length > 0 ? (
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
                {globalRows.map((row) => (
                  <tr key={row.key}>
                    <td>{row.region}</td>
                    <td><strong>{row.name}</strong></td>
                    <td className="aq-num">{row.price === null ? "—" : row.price.toFixed(2)}</td>
                    {/* 涨跌幅缺失显示「—」，样式也必须为空：不能拿 ?? 0 把它染成「涨」。
                        统一走 changeClass(null)→""（A股口径 涨红跌绿，见 format.ts）。 */}
                    <td className={cn("aq-num", changeClass(row.change_pct))}>
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
