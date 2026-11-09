// 因子：候选的评分因子、取值与来源。
//
// 2026-11-09 新增模块。此前这些数字被藏在候选卡的"技术指标、评分与来源"折叠里，
// 要逐只点开才看得到；这里一次摊开全部候选的因子表。
import { TableProperties } from "lucide-react";
import { HeroState, LoadingState, SectionHeader } from "@/components/ui/primitives";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";

export function FactorsPage() {
  const { data, error, switching } = useWorkspaceSnapshot();
  const view = data && !switching ? buildDailyView(data) : null;

  if (error && !data) {
    return <div className="aq-page"><HeroState tone="warn" title="读不到候选快照" detail={error} /></div>;
  }
  if (!view) {
    return <div className="aq-page"><LoadingState label={switching ? "正在切换研究日期…" : "正在读取因子数据…"} /></div>;
  }

  // 因子标签 → 出现过的候选集合，用来统计每个因子覆盖了多少只候选。
  const coverage = new Map<string, { count: number; samples: string[] }>();
  for (const row of view.candidates) {
    for (const metric of row.metrics) {
      const entry = coverage.get(metric.label) ?? { count: 0, samples: [] };
      entry.count += 1;
      if (entry.samples.length < 3) entry.samples.push(`${row.name} ${metric.value}`);
      coverage.set(metric.label, entry);
    }
  }
  const factors = [...coverage.entries()].sort((a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0], "zh-CN"));

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><TableProperties aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>因子</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">评分因子、取值与覆盖情况。</p>
        </div>
      </header>

      <section className="aq-section" aria-label="因子覆盖">
        <SectionHeader
          number="01 · 因子覆盖"
          title="因子清单与覆盖"
          description="按覆盖候选数排序；样例取前 3 只候选的实际取值。"
          count={`${factors.length} 个因子`}
        />
        {factors.length === 0 ? (
          <p className="aq-note">当天没有候选，因子表为空。</p>
        ) : (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>因子</th>
                  <th className="aq-num">覆盖候选</th>
                  <th>样例取值</th>
                </tr>
              </thead>
              <tbody>
                {factors.map(([label, entry]) => (
                  <tr key={label}>
                    <td><strong>{label}</strong></td>
                    <td className="aq-num">{entry.count} / {view.candidates.length}</td>
                    <td>{entry.samples.join(" · ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="aq-section" aria-label="逐票评分依据">
        <SectionHeader
          number="02 · 逐票评分依据"
          title="每只候选的评分构成"
          description="快照冻结的评分依据原文；与上表互为对照。"
          count={`${view.candidates.length} 只`}
        />
        <div className="aq-table-wrap">
          <table className="aq-table aq-summary-table">
            <thead>
              <tr>
                <th>候选</th>
                <th className="aq-num">评分</th>
                <th>评分依据</th>
              </tr>
            </thead>
            <tbody>
              {view.candidates.map((row) => (
                <tr key={row.symbol}>
                  <td><strong>{row.name}</strong> <span className="aq-code">{row.symbol}</span></td>
                  <td className="aq-num">{row.scoreText}</td>
                  <td>{row.breakdown.length ? row.breakdown.join(" · ") : "未记录评分构成"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
