// 回测：研究变体台账与研究链覆盖。
//
// 2026-11-09 新增模块。此前变体数据只作为候选表里的一列出现，看不到收益、
// 成交与阻断原因；这里把台账摊平成一张可排序对照的表。
import { BarChart3 } from "lucide-react";
import { HeroState, LoadingState, SectionHeader, ToneCallout } from "@/components/ui/primitives";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";
import { cn } from "@/lib/utils";

export function BacktestPage() {
  const { data, error, switching } = useWorkspaceSnapshot();
  const view = data && !switching ? buildDailyView(data) : null;

  if (error && !data) {
    return <div className="aq-page"><HeroState tone="warn" title="读不到研究快照" detail={error} /></div>;
  }
  if (!view || !data) {
    return <div className="aq-page"><LoadingState label={switching ? "正在切换研究日期…" : "正在读取回测台账…"} /></div>;
  }

  const variants = [...(data.variants ?? [])].sort(
    (a, b) => (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER),
  );
  const suite = data.variant_suite;
  const chain = data.research_chain;
  const gate = data.recommendation_gate;

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><BarChart3 aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>回测</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">研究变体的收益、成交与阻断原因；台账是研究记录，不等于实盘结果。</p>
        </div>
      </header>

      {gate ? (
        <ToneCallout
          tone={gate.recommendation_allowed ? "ok" : "warn"}
          title={gate.recommendation_allowed ? "门禁放行" : "门禁未放行"}
          detail={gate.reasons.length ? gate.reasons.join(" · ") : "未记录阻断原因"}
        />
      ) : null}

      <section className="aq-section" aria-label="回测台账">
        <SectionHeader
          number="01 · 变体台账"
          title="研究变体对照"
          description={suite ? `生成于 ${suite.generated_at} · 数据模式 ${suite.data_mode} · 变体 ${suite.variant_count} 个` : "变体台账"}
          count={variants.length}
        />
        {variants.length === 0 ? (
          <p className="aq-note">当前快照没有变体记录。</p>
        ) : (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th className="aq-num">#</th>
                  <th>变体</th>
                  <th className="aq-num">收益</th>
                  <th className="aq-num">期末权益</th>
                  <th className="aq-num">成交/拒单</th>
                  <th>区间</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {variants.map((variant) => (
                  <tr key={variant.variant_id}>
                    <td className="aq-num">{variant.rank ?? "—"}</td>
                    <td><strong>{variant.label}</strong></td>
                    <td className={cn("aq-num", variant.return_pct >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                      {variant.return_pct >= 0 ? "+" : ""}{variant.return_pct.toFixed(2)}%
                    </td>
                    <td className="aq-num">{variant.final_equity.toFixed(0)}</td>
                    <td className="aq-num">{variant.filled_orders} / {variant.rejected_orders}</td>
                    <td>{variant.start_date} → {variant.end_date}</td>
                    <td>{variant.lifecycle_status ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="aq-section" aria-label="研究链覆盖">
        <SectionHeader
          number="02 · 研究链"
          title="候选到复核的链路覆盖"
          description="当天候选有多少只走到了讨论复核、多少只被历史实验覆盖。"
          count={chain ? chain.status : "未记录"}
        />
        {chain ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>环节</th>
                  <th className="aq-num">只数</th>
                  <th>标的</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["当天候选", chain.candidate_symbols],
                  ["讨论复核", chain.debated_symbols],
                  ["待复核", chain.pending_review_symbols],
                  ["历史实验覆盖", chain.variant_candidate_symbols],
                  ["实验持仓复核", chain.variant_holding_candidate_symbols],
                ].map(([label, symbols]) => (
                  <tr key={String(label)}>
                    <td><strong>{label}</strong></td>
                    <td className="aq-num">{(symbols as readonly string[]).length}</td>
                    <td>{(symbols as readonly string[]).join(" · ") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="aq-note">当前快照没有研究链记录。</p>
        )}
        {chain?.blocker ? <p className="aq-note">阻断原因：{chain.blocker}</p> : null}
      </section>
    </div>
  );
}
