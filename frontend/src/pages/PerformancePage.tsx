// 选股绩效：回答"选得准不准"。
//
// 这是复盘真正的核心 —— 之前前端只能回答"今天选了什么"，
// 而"选得准不准"（命中率、策略权重、衰减告警）一直在 Python 侧，界面上看不见。
//
// ⚠️ 宪法 §5.4：冷启动期（独立信号日 < 30）**不展示胜率**，只显示积累进度。
// 样本不足时给出数字会制造虚假信心，所以这一页在此情况下必须显示"—"。
import { useCallback, useEffect, useState } from "react";
import { Gauge, RefreshCw, TrendingDown } from "lucide-react";
import { api, type PerformancePayload } from "@/lib/api";
import {
  Badge,
  EmptyState,
  LoadingState,
  SectionHeader,
  StatePanel,
  Tag,
  ToneCallout,
} from "@/components/ui/primitives";
import { normalizePerformance, performanceHeadline } from "@/lib/performance-view";
import { cn } from "@/lib/utils";

const EMPTY_PAYLOAD: PerformancePayload = {
  schema_version: "v1",
  generated_at: "",
  available: false,
  reason: "",
  cold_start: {
    is_cold_start: true,
    min_independent_signal_days: 30,
    independent_signal_days: 0,
    max_strategy_signal_days: 0,
  },
  overall: null,
  strategies: [],
  decay_alerts: [],
  status_counts: {},
  notes: [],
};

const STATUS_LABELS: Readonly<Record<string, string>> = {
  validated: "已结算",
  pending: "待结算",
  watch_only: "仅观察",
  not_executable: "不可执行",
  blocked_by_circuit_breaker: "风控拦截",
  run_completed_no_picks: "无候选",
};

export function PerformancePage() {
  const [payload, setPayload] = useState<PerformancePayload>(EMPTY_PAYLOAD);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPayload((await api.performance()) ?? EMPTY_PAYLOAD);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "绩效读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const view = normalizePerformance(payload);

  if (loading && !payload.available) return <LoadingState label="正在读取选股绩效…" />;
  if (error) return <StatePanel tone="warn">{error}</StatePanel>;

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Gauge aria-hidden="true" />
            AQSP · 选股绩效
          </p>
          <div className="aq-title-row">
            <h1>选股绩效</h1>
            {view.generatedAt ? <strong>{view.generatedAt}</strong> : null}
          </div>
          <p className="aq-page-sub">
            {performanceHeadline(view)} · 口径复用台账学习器，只读不回写
          </p>
        </div>
        <button type="button" className="aq-btn" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={cn(loading && "aq-spin")} aria-hidden="true" />
          刷新
        </button>
      </header>

      {!view.available ? (
        <EmptyState title="暂无绩效数据" detail={view.reason || "台账中还没有可统计的数据。"} />
      ) : null}

      {view.available ? (
        <>
          {view.coldStart ? (
            <ToneCallout
              tone="warn"
              title={`冷启动期：已积累 ${view.independentSignalDays}/${view.minSignalDays} 个独立信号日`}
              detail="样本量不足以支撑统计推断，按宪法 §5.4 暂不展示胜率。攒够独立信号日后会自动显示。"
            />
          ) : null}

          <div className="aq-progress" aria-label="信号日积累进度">
            <div className="aq-progress-bar" style={{ width: `${Math.round(view.progress * 100)}%` }} />
          </div>

          {/* 整体命中率 —— 冷启动期只显示占位，不显示数字 */}
          <section className="aq-section">
            <SectionHeader
              title="整体命中率"
              description="按 signal_date 合成观察（同日多只合并为 1 个观察）"
              count={view.overallHitRate !== null ? `${view.overallHitRate > 0 ? "" : ""}${view.independentSignalDays} 个观察` : undefined}
            />
            <div className="aq-total-grid">
              <div className="aq-total-card">
                <span>命中率（主指标）</span>
                <b className={view.canShowOverallHitRate ? "aq-tone-up" : undefined}>
                  {view.canShowOverallHitRate && view.overallHitRate !== null
                    ? `${(view.overallHitRate * 100).toFixed(1)}%`
                    : "—"}
                </b>
              </div>
              <div className="aq-total-card">
                <span>独立信号日</span>
                <b className="aq-num">
                  {view.independentSignalDays} / {view.minSignalDays}
                </b>
              </div>
            </div>
            {!view.canShowOverallHitRate ? (
              <p className="aq-note">样本不足，命中率不予展示（§5.4）。</p>
            ) : null}
          </section>

          {/* 策略表现 */}
          <section className="aq-section">
            <SectionHeader
              title="按策略"
              description="命中率为主指标；平均收益为 PnL 派生，仅作观测（§8）"
              count={`${view.strategies.length} 个策略`}
            />
            {view.strategies.length === 0 ? (
              <EmptyState title="还没有可统计的策略" detail="等台账积累到有已结算信号后，这里会按策略列出命中率。" />
            ) : (
              <div className="aq-table-wrap">
                <table className="aq-table">
                  <thead>
                    <tr>
                      <th>策略</th>
                      <th className="aq-num">独立信号日</th>
                      <th className="aq-num">样本</th>
                      <th className="aq-num">命中率</th>
                      <th className="aq-num">权重</th>
                      <th className="aq-num">平均收益（仅观测）</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.strategies.map((row) => (
                      <tr key={row.name}>
                        <td>{row.name}</td>
                        <td className="aq-num">{row.independentSignalDays}</td>
                        <td className="aq-num">{row.totalPicks}</td>
                        <td className={cn("aq-num", row.canShowHitRate && "aq-tone-up")}>
                          {row.canShowHitRate ? `${(row.hitRate * 100).toFixed(1)}%` : "—"}
                        </td>
                        <td className="aq-num">{row.weightBase.toFixed(2)}</td>
                        <td className="aq-num">{row.avgReturnPct.toFixed(2)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 衰减告警 —— PnL 派生指标只允许出现在这里（观测/告警） */}
          <section className="aq-section">
            <SectionHeader
              icon={TrendingDown}
              title="策略衰减告警"
              description="近期命中率显著下滑时的提示（基于 PnL 派生指标，仅作告警）"
              count={`${view.decayAlerts.length} 条`}
            />
            {view.coldStart && view.decayAlerts.length > 0 ? (
              <ToneCallout
                tone="neutral"
                title="冷启动期：以下仅作观测提示"
                detail={`近期样本不足，按 §5.4 不展示具体胜率；且冷启动期权重固定为 1.0，暂不据此调权。`}
              />
            ) : null}

            {view.decayAlerts.length === 0 ? (
              <StatePanel>暂无衰减告警。</StatePanel>
            ) : (
              <div className="aq-tag-row">
                {view.decayAlerts.map((alert) => (
                  <ToneCallout
                    key={alert.strategy}
                    tone={alert.tone}
                    // 冷启动期只说"近期走弱"，不给具体数字（§5.4）
                    title={
                      view.coldStart
                        ? `${alert.strategy}：近期${alert.severity === "critical" ? "明显" : ""}走弱（近 ${alert.lookbackDays} 天样本不足，不展示胜率）`
                        : `${alert.strategy}：近 ${alert.lookbackDays} 天命中率 ${(alert.recentWinRate * 100).toFixed(1)}%`
                    }
                    detail={view.coldStart ? `后续观察方向：${alert.recommendation}（冷启动期不执行）` : alert.recommendation}
                  />
                ))}
              </div>
            )}
          </section>

          {/* 台账状态分布 */}
          {view.statusCounts.length > 0 ? (
            <section className="aq-section">
              <SectionHeader title="台账状态分布" description="只有 validated（已结算）进入统计" />
              <div className="aq-tag-row">
                {view.statusCounts.map(([status, count]) => (
                  <Tag key={status} tone={status === "validated" ? "ok" : "neutral"}>
                    {STATUS_LABELS[status] ?? status} {count}
                  </Tag>
                ))}
              </div>
            </section>
          ) : null}

          {view.notes.length > 0 ? (
            <section className="aq-section">
              <SectionHeader title="口径说明" />
              <ul className="aq-bullet-list">
                {view.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </section>
          ) : null}

          <Badge tone="neutral">只读：不写台账、不触发权重落盘</Badge>
        </>
      ) : null}
    </div>
  );
}
