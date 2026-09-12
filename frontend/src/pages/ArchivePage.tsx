// 结论归档：按交易日回看历史结论，并支持**多日对比**看趋势。
//
// 只做两件事：选一天 → 切到「今日研究」看那天的结论；勾若干天 → 并排对比门禁与候选变化。
// 历史结论不会被修改，也不会用来顶替当天数据。
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Archive, CalendarDays, GitCompareArrows } from "lucide-react";
import { api } from "@/lib/api";
import { Badge, EmptyState, StatePanel, ToneCallout } from "@/components/ui/primitives";
import { useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { useComparison } from "@/components/aqsp/useComparison";
import { MAX_COMPARE, parseCompareDates, toggleCompareDate } from "@/lib/compare-view";
import { cn } from "@/lib/utils";

function CompareTable({ dates }: { dates: readonly string[] }) {
  const { summary, loading, failed } = useComparison(dates);

  if (dates.length === 0) return null;

  return (
    <section className="aq-section">
      <div className="aq-section-head">
        <div className="aq-section-head-main">
          <h2>多日对比</h2>
          <p className="aq-section-desc">
            已选 {dates.length} / {MAX_COMPARE} 天 · 看门禁与候选的变化趋势
          </p>
        </div>
        <div className="aq-section-head-side">
          <span className="aq-section-count">
            <GitCompareArrows className="aq-inline-icon" aria-hidden="true" />
            {dates.length} 天
          </span>
        </div>
      </div>

      {loading ? <StatePanel>正在读取所选日期的快照…</StatePanel> : null}

      {failed.length > 0 ? (
        <ToneCallout
          tone="warn"
          title={`${failed.length} 个日期取不到快照`}
          detail={`${failed.join("、")} 可能已被清理，对比结果中不包含这些日期。`}
        />
      ) : null}

      {summary && summary.rows.length > 0 ? (
        <>
          <div className="aq-table-wrap">
            <table className="aq-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>门禁</th>
                  <th className="aq-num">候选</th>
                  <th className="aq-num">可复核</th>
                  <th>当天评分最高</th>
                </tr>
              </thead>
              <tbody>
                {summary.rows.map((row) => (
                  <tr key={row.date}>
                    <td>
                      <Link className="aq-date-link" to={`/today?date=${row.date}`}>
                        {row.date}
                      </Link>
                    </td>
                    <td>
                      <Badge tone={row.gateTone}>{row.gateLabel}</Badge>
                    </td>
                    <td className="aq-num">{row.candidateCount}</td>
                    <td className="aq-num">{row.readyCount}</td>
                    <td>{row.topSymbols.join("、") || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 「昙花一现」只在 ≥2 天时才有意义：单日里每只都只出现一次 */}
          <p className="aq-note">
            <GitCompareArrows className="aq-inline-icon" aria-hidden="true" />
            覆盖候选 {summary.allSymbols.length} 只
            {summary.totalDates >= 2
              ? ` · 持续在榜 ${summary.persistentSymbols.length} 只 · 只出现一次 ${summary.oneOffCount} 只`
              : " · 多选几天可看出哪些候选持续在榜"}
          </p>

          {summary.totalDates >= 2 && summary.persistentSymbols.length > 0 ? (
            <ToneCallout
              tone="ok"
              title={`${summary.persistentSymbols.length} 只候选在这 ${summary.totalDates} 天里每天都在榜`}
              detail="持续被选中通常比单日冒头更值得跟踪 —— 点日期可回到当天看完整证据链。"
            />
          ) : null}
        </>
      ) : null}

      {!loading && summary === null ? (
        <EmptyState
          title="所选日期都没有可取到的快照"
          detail="换几个日期，或先打开其中一天确认它还能正常访问。"
        />
      ) : null}
    </section>
  );
}

export function ArchivePage() {
  const [dates, setDates] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const { selectedDate } = useAqspSnapshot();
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    let alive = true;
    api
      .aqspDates()
      .then((index) => {
        if (alive) setDates([...index.available_dates].sort().reverse());
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "读取归档失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 对比所选日期放在 URL 上：刷新 / 分享 / 前进后退都能还原（与所选交易日同一套原则）
  const compareDates = useMemo(
    () => parseCompareDates(searchParams.get("dates"), dates),
    [searchParams, dates],
  );

  function setCompareDates(next: string[]) {
    const params = new URLSearchParams(searchParams);
    if (next.length === 0) params.delete("dates");
    else params.set("dates", next.join(","));
    setSearchParams(params, { replace: false });
  }

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Archive aria-hidden="true" />
            AQSP · 结论归档
          </p>
          <div className="aq-title-row">
            <h1>结论归档</h1>
          </div>
          <p className="aq-page-sub">共 {dates.length} 个交易日可选 · 历史结论不修改</p>
        </div>
        <Badge tone="neutral">公共只读</Badge>
      </header>

      {error ? <StatePanel tone="warn">{error}</StatePanel> : null}

      {!error && loading ? <StatePanel>正在读取可回看的交易日…</StatePanel> : null}

      {!error && !loading && dates.length === 0 ? (
        <EmptyState title="还没有可回看的交易日" detail="等任务产出后，这里会按日期列出。" />
      ) : null}

      {dates.length > 0 ? (
        <>
          <div className="aq-date-grid">
            {dates.map((date) => {
              const inCompare = compareDates.includes(date);
              const full = compareDates.length >= MAX_COMPARE && !inCompare;
              return (
                <div className="aq-archive-row" key={date}>
                  <Link
                    className={cn("aq-date-card", date === selectedDate && "aq-date-card-active")}
                    to={`/today?date=${date}`}
                  >
                    <CalendarDays aria-hidden="true" />
                    {date}
                  </Link>
                  <button
                    type="button"
                    className={cn("aq-btn aq-btn-icon", inCompare && "aq-btn-primary")}
                    onClick={() => setCompareDates(toggleCompareDate(compareDates, date))}
                    disabled={full}
                    title={
                      full
                        ? `最多同时对比 ${MAX_COMPARE} 天`
                        : inCompare
                          ? "从对比中移除"
                          : "加入多日对比"
                    }
                    aria-pressed={inCompare}
                  >
                    <GitCompareArrows aria-hidden="true" />
                  </button>
                </div>
              );
            })}
          </div>
          <p className="aq-note">
            <Archive aria-hidden="true" />
            点日期切到「今日研究」查看该日的结论、门禁、候选、证据与分歧；点右侧图标可把多天加进对比。
          </p>

          <CompareTable dates={compareDates} />
        </>
      ) : null}
    </div>
  );
}
