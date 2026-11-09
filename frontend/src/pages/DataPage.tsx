// 数据：来源健康、漏斗覆盖与快照时点。
//
// 2026-11-09 新增模块。判断结论之前先能回答"这些数是哪来的、新不新、漏了多少"。
import { useEffect, useState } from "react";
import { Database } from "lucide-react";
import { HeroState, LoadingState, SectionHeader, Tag } from "@/components/ui/primitives";
import { api, type HealthPayload } from "@/lib/api";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";
import { formatAqspTime } from "@/components/aqsp/useAqspSnapshot";

export function DataPage() {
  const { data, error, switching } = useWorkspaceSnapshot();
  const view = data && !switching ? buildDailyView(data) : null;
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [healthError, setHealthError] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .health()
      .then((value) => { if (alive) setHealth(value); })
      .catch((reason: unknown) => {
        if (alive) setHealthError(reason instanceof Error ? reason.message : "读取失败");
      });
    return () => { alive = false; };
  }, []);

  if (error && !data) {
    return <div className="aq-page"><HeroState tone="warn" title="读不到研究快照" detail={error} /></div>;
  }
  if (!view || !data) {
    return <div className="aq-page"><LoadingState label={switching ? "正在切换研究日期…" : "正在读取数据状态…"} /></div>;
  }

  const source = data.source;
  const universe = data.universe;
  const funnel = universe
    ? [
        ["全池", universe.total],
        ["已解析", universe.resolved],
        ["已筛选", universe.screened],
        ["入选", universe.final],
      ]
    : [];

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><Database aria-hidden="true" /> AQSP · 研究模块</p>
          <div className="aq-title-row">
            <h1>数据</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">来源健康、漏斗覆盖与快照时点。</p>
        </div>
      </header>

      <section className="aq-section" aria-label="来源健康">
        <SectionHeader
          number="01 · 来源健康"
          title="行情来源与时点"
          description="源给出的最新交易日与滞后天数，不是取数时刻。"
          count={source ? source.status : "未记录"}
        />
        <div className="aq-tag-row">
          {source ? (
            <>
              <Tag tone={source.lag_days > 0 ? "warn" : "ok"}>最新交易日 {source.latest_trade_date}</Tag>
              <Tag tone={source.lag_days > 0 ? "warn" : "neutral"}>滞后 {source.lag_days} 天</Tag>
              <Tag>生效口径 {source.effective}</Tag>
              {source.task_id ? <Tag>任务 {source.task_id}</Tag> : null}
            </>
          ) : (
            <p className="aq-note">当前快照没有来源健康记录。</p>
          )}
        </div>
        {source?.run_detail ? <p className="aq-note">运行说明：{source.run_detail}</p> : null}
      </section>

      <section className="aq-section" aria-label="漏斗覆盖">
        <SectionHeader
          number="02 · 漏斗覆盖"
          title="从全池到入选"
          description={universe?.source ? `口径 ${universe.source} · 上限 ${universe.max_universe}` : "候选漏斗"}
          count={universe?.coverage_pct !== undefined ? `${universe.coverage_pct}%` : undefined}
        />
        {funnel.length === 0 ? (
          <p className="aq-note">当前快照没有漏斗记录。</p>
        ) : (
          <>
            <div className="aq-table-wrap">
              <table className="aq-table aq-summary-table">
                <thead>
                  <tr>
                    <th>环节</th>
                    <th className="aq-num">数量</th>
                  </tr>
                </thead>
                <tbody>
                  {funnel.map(([label, value]) => (
                    <tr key={String(label)}>
                      <td><strong>{label}</strong></td>
                      <td className="aq-num">{value as number}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {universe?.batch_active ? <p className="aq-note">分批运行中：批次 {universe.batch_id ?? "—"} · 单批 {universe.batch_size ?? "—"} 只</p> : null}
            {universe?.last_error ? <p className="aq-note">最近错误：{universe.last_error}</p> : null}
          </>
        )}
      </section>

      <section className="aq-section" aria-label="服务状态">
        <SectionHeader
          number="03 · 服务状态"
          title="后端与各数据源"
          description={health ? `版本 ${health.version} · 发布 ${health.release_sha.slice(0, 8)}` : "读取中"}
          count={health?.sources.length}
        />
        {healthError ? <p className="aq-note">健康检查读取失败：{healthError}</p> : null}
        {health?.sources.length ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>数据源</th>
                  <th>状态</th>
                  <th>最近成功</th>
                </tr>
              </thead>
              <tbody>
                {health.sources.map((row) => (
                  <tr key={row.source}>
                    <td><strong>{row.source}</strong></td>
                    <td>{row.healthy ? "正常" : `异常（连续 ${row.consecutive_errors} 次）`}</td>
                    <td>{row.last_ok_at ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        <p className="aq-note">快照生成于 {formatAqspTime(data.generated_at)}</p>
      </section>
    </div>
  );
}
