// 策略：当前候选命中了哪些策略假设、每条策略覆盖多少只。
//
// 2026-11-09 新增模块。做法参考 investment-news：左栏是模块切换器，点进这一屏
// 就要能一次看完这个问题的全部数据，而不是再点一层。
import { ListFilter } from "lucide-react";
import { HeroState, LoadingState, SectionHeader } from "@/components/ui/primitives";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";
import { strategyCounts } from "@/lib/candidate-view";
import { statusLabel } from "@/lib/display-labels";

export function ScreenerPage() {
  const { data, error, switching } = useWorkspaceSnapshot();
  const view = data && !switching ? buildDailyView(data) : null;

  if (error && !data) {
    return <div className="aq-page"><HeroState tone="warn" title="读不到候选快照" detail={error} /></div>;
  }
  if (!view) {
    return <div className="aq-page"><LoadingState label={switching ? "正在切换研究日期…" : "正在读取策略命中…"} /></div>;
  }

  const counts = strategyCounts(view.candidates);
  const hits = counts.map((entry) => ({
    ...entry,
    rows: view.candidates.filter((row) =>
      (row.strategies ?? "").split(/[·、,，]/).some((part) => part.trim() === entry.name),
    ),
  }));

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><ListFilter aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>策略</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">当前候选命中了哪些策略假设。</p>
        </div>
      </header>

      <section className="aq-section" aria-label="策略命中分布">
        <SectionHeader
          number="01 · 命中分布"
          title="策略命中概览"
          description="一只候选可同时命中多条策略，因此各策略命中数之和会大于候选总数。"
          count={`${counts.length} 条策略`}
        />
        {hits.length === 0 ? (
          <p className="aq-note">当天没有候选，策略命中为空。</p>
        ) : (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>策略</th>
                  <th className="aq-num">命中</th>
                  <th>命中候选</th>
                </tr>
              </thead>
              <tbody>
                {hits.map((hit) => (
                  <tr key={hit.name}>
                    <td><strong>{hit.name}</strong></td>
                    <td className="aq-num">{hit.count}</td>
                    <td>
                      {hit.rows.map((row) => (
                        <span key={row.symbol} className="aq-chip" title={`${row.name} ${row.symbol} · ${statusLabel(row.status)}`}>
                          {row.name} {row.scoreText}
                        </span>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
