// 业务指标监控仪表盘：总览系统运行状态与关键指标。
//
// 设计目标：
// - 快速了解系统整体健康状况
// - 可视化展示累计收益和策略表现
// - 追踪最近信号执行情况
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, RefreshCw, TrendingUp } from "lucide-react";
import { api, type DashboardMetrics } from "@/lib/api";
import {
  Badge,
  EmptyState,
  LoadingState,
  SectionHeader,
  StatePanel,
  Tag,
  ToneCallout,
} from "@/components/ui/primitives";
import { EChart } from "@/components/ui/EChart";
import { cn } from "@/lib/utils";
import { strategyLabel } from "@/lib/display-labels";
import type { EChartsOption } from "echarts";

const EMPTY_METRICS: DashboardMetrics = {
  available: false,
  reason: "",
  overall_stats: null,
  strategy_performance: [],
  time_series: [],
  data_source_health: {
    ledger: "unavailable",
    latest_signal_date: "",
    ledger_updated_at: "",
    trading_days_since_latest: null,
  },
  recent_signals: [],
};

type TimeRange = "7d" | "30d" | "90d" | "all";

const TIME_RANGE_LABELS: Record<TimeRange, string> = {
  "7d": "7天",
  "30d": "30天",
  "90d": "90天",
  all: "全部",
};

export function DashboardPage() {
  const [metrics, setMetrics] = useState<DashboardMetrics>(EMPTY_METRICS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [timeRange, setTimeRange] = useState<TimeRange>("30d");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setMetrics((await api.dashboardMetrics()) ?? EMPTY_METRICS);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "仪表盘数据读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 根据时间范围过滤数据
  const filteredTimeSeries = useMemo(() => {
    if (timeRange === "all") return metrics.time_series;
    const now = new Date();
    const days = timeRange === "7d" ? 7 : timeRange === "30d" ? 30 : 90;
    const cutoff = new Date(now.getTime() - days * 24 * 60 * 60 * 1000);
    return metrics.time_series.filter((item) => new Date(item.date) >= cutoff);
  }, [metrics.time_series, timeRange]);

  // 累计收益曲线图表配置
  const cumulativeReturnChart = useMemo<EChartsOption>(() => {
    if (filteredTimeSeries.length === 0) {
      return {
        title: { text: "暂无数据", left: "center", top: "center" },
      };
    }

    return {
      grid: { left: 60, right: 20, top: 40, bottom: 60 },
      xAxis: {
        type: "category",
        data: filteredTimeSeries.map((item) => item.date),
        axisLabel: { rotate: 45 },
      },
      yAxis: {
        type: "value",
        axisLabel: {
          formatter: (value: number) => `${(value * 100).toFixed(1)}%`,
        },
      },
      series: [
        {
          name: "累计收益",
          type: "line",
          data: filteredTimeSeries.map((item) => item.cumulative_return),
          smooth: true,
          lineStyle: { width: 2 },
        },
      ],
      tooltip: {
        trigger: "axis",
        formatter: (params: any) => {
          const item = params[0];
          return `${item.axisValue}<br/>累计收益: ${(item.value * 100).toFixed(2)}%`;
        },
      },
    };
  }, [filteredTimeSeries]);

  // 策略胜率对比柱状图
  const strategyWinRateChart = useMemo<EChartsOption>(() => {
    const displayable = metrics.strategy_performance.filter((s) => s.displayable);
    if (displayable.length === 0) {
      return {
        title: { text: "暂无可展示数据", left: "center", top: "center" },
      };
    }

    return {
      grid: { left: 100, right: 20, top: 20, bottom: 60 },
      xAxis: {
        type: "value",
        axisLabel: {
          formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
        },
      },
      yAxis: {
        type: "category",
        data: displayable.map((s) => s.name),
      },
      series: [
        {
          name: "胜率",
          type: "bar",
          data: displayable.map((s) => s.win_rate ?? 0),
        },
      ],
      tooltip: {
        trigger: "axis",
        formatter: (params: any) => {
          const item = params[0];
          return `${item.name}<br/>胜率: ${((item.value ?? 0) * 100).toFixed(1)}%`;
        },
      },
    };
  }, [metrics.strategy_performance]);

  if (loading && !metrics.available) return <LoadingState label="正在读取仪表盘数据…" />;
  if (error) return <StatePanel tone="warn">{error}</StatePanel>;

  const healthStatus = metrics.data_source_health.ledger;
  const isHealthy = healthStatus === "healthy";
  const isStale = healthStatus === "stale";

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Activity aria-hidden="true" />
            AQSP · 业务指标监控
          </p>
          <div className="aq-title-row">
            <h1>仪表盘</h1>
          </div>
          <p className="aq-page-sub">系统运行状态与关键指标总览 · 缓存5分钟</p>
        </div>
        <button type="button" className="aq-btn" onClick={() => void load()} disabled={loading}>
          <RefreshCw className={cn(loading && "aq-spin")} aria-hidden="true" />
          刷新
        </button>
      </header>

      {!metrics.available ? (
        <EmptyState
          title="暂无仪表盘数据"
          detail={metrics.reason || "台账中还没有可统计的数据。"}
        />
      ) : null}

      {metrics.available ? (
        <>
          {/* 数据源健康状态 */}
          {isStale ? (
            <ToneCallout
              tone="warn"
              title={`数据源停滞：最新信号日 ${metrics.data_source_health.latest_signal_date}`}
              detail={`已过去 ${metrics.data_source_health.trading_days_since_latest} 个交易日未更新。请检查数据采集流水线。`}
            />
          ) : null}

          {!isHealthy && !isStale ? (
            <ToneCallout
              tone="neutral"
              title={`数据源状态：${healthStatus}`}
              detail="数据源不可用或尚未初始化。"
            />
          ) : null}

          {/* 总体统计卡片 */}
          {metrics.overall_stats ? (
            <section className="aq-section">
              <SectionHeader icon={TrendingUp} title="总体统计" description="系统整体运行指标" />
              <div className="aq-total-grid">
                <div className="aq-total-card">
                  <span>总信号数</span>
                  <b className="aq-num">{metrics.overall_stats.total_signals}</b>
                </div>
                <div className="aq-total-card">
                  <span>胜率</span>
                  <b className={metrics.overall_stats.displayable ? "aq-tone-up" : undefined}>
                    {metrics.overall_stats.displayable && metrics.overall_stats.win_rate !== null
                      ? `${(metrics.overall_stats.win_rate * 100).toFixed(1)}%`
                      : "—"}
                  </b>
                </div>
                <div className="aq-total-card">
                  <span>平均收益</span>
                  <b className="aq-num">{(metrics.overall_stats.avg_return * 100).toFixed(2)}%</b>
                </div>
                <div className="aq-total-card">
                  <span>夏普率</span>
                  <b className="aq-num">{metrics.overall_stats.sharpe_ratio.toFixed(2)}</b>
                </div>
              </div>
              {!metrics.overall_stats.displayable ? (
                <ToneCallout
                  tone="neutral"
                  title={`冷启动期：${metrics.overall_stats.cold_start_progress}`}
                  detail="样本量不足，胜率暂不展示。"
                />
              ) : null}
            </section>
          ) : null}

          {/* 累计收益曲线 */}
          <section className="aq-section">
            <SectionHeader
              title="累计收益曲线"
              description="按信号日期的累计收益走势"
              count={`${filteredTimeSeries.length} 个观察`}
            >
              <div className="aq-tag-row">
                {(Object.keys(TIME_RANGE_LABELS) as TimeRange[]).map((range) => (
                  <button
                    key={range}
                    type="button"
                    className={cn("aq-tag", timeRange === range && "aq-tag-primary")}
                    onClick={() => setTimeRange(range)}
                  >
                    {TIME_RANGE_LABELS[range]}
                  </button>
                ))}
              </div>
            </SectionHeader>
            {filteredTimeSeries.length === 0 ? (
              <StatePanel>当前时间范围内暂无数据。</StatePanel>
            ) : (
              <EChart option={cumulativeReturnChart} height={300} />
            )}
          </section>

          {/* 策略表现 */}
          <section className="aq-section">
            <SectionHeader
              title="策略胜率对比"
              description="各策略的命中率表现"
              count={`${metrics.strategy_performance.length} 个策略`}
            />
            {metrics.strategy_performance.length === 0 ? (
              <EmptyState title="还没有可统计的策略" detail="等台账积累到有已结算信号后，这里会显示策略表现。" />
            ) : (
              <>
                <EChart option={strategyWinRateChart} height={Math.max(200, metrics.strategy_performance.length * 40)} />
                <div className="aq-table-wrap">
                  <table className="aq-table">
                    <thead>
                      <tr>
                        <th>策略</th>
                        <th className="aq-num">信号数</th>
                        <th className="aq-num">胜率</th>
                        <th className="aq-num">平均收益</th>
                      </tr>
                    </thead>
                    <tbody>
                      {metrics.strategy_performance.map((strategy) => (
                        <tr key={strategy.name}>
                          <td>{strategy.name}</td>
                          <td className="aq-num">{strategy.signal_count}</td>
                          <td className={cn("aq-num", strategy.displayable && "aq-tone-up")}>
                            {strategy.displayable && strategy.win_rate !== null
                              ? `${(strategy.win_rate * 100).toFixed(1)}%`
                              : "—"}
                          </td>
                          <td className="aq-num">{(strategy.avg_return * 100).toFixed(2)}%</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </section>

          {/* 最近信号 */}
          <section className="aq-section">
            <SectionHeader
              title="最近信号"
              description="最近10条已结算信号的执行情况"
              count={`${metrics.recent_signals.length} 条`}
            />
            {metrics.recent_signals.length === 0 ? (
              <StatePanel>暂无已结算信号。</StatePanel>
            ) : (
              <div className="aq-table-wrap">
                <table className="aq-table">
                  <thead>
                    <tr>
                      <th>代码</th>
                      <th>名称</th>
                      <th>信号日</th>
                      <th>退出日</th>
                      <th className="aq-num">收益率</th>
                      <th className="aq-num">超额收益</th>
                      <th>结果</th>
                      <th>策略</th>
                    </tr>
                  </thead>
                  <tbody>
                    {metrics.recent_signals.map((signal, idx) => (
                      <tr key={`${signal.symbol}-${signal.signal_date}-${idx}`}>
                        <td className="aq-code">{signal.symbol}</td>
                        <td>{signal.name}</td>
                        <td>{signal.signal_date}</td>
                        <td>{signal.exit_date || "—"}</td>
                        <td className={cn("aq-num", signal.return_pct !== null && (signal.return_pct > 0 ? "aq-tone-up" : "aq-tone-down"))}>
                          {signal.return_pct !== null ? `${signal.return_pct.toFixed(2)}%` : "—"}
                        </td>
                        <td className="aq-num">
                          {signal.excess_return_pct !== null ? `${signal.excess_return_pct.toFixed(2)}%` : "—"}
                        </td>
                        <td>
                          <Badge tone={signal.win ? "ok" : "neutral"}>{signal.win ? "命中" : "未命中"}</Badge>
                        </td>
                        <td>
                          <div className="aq-tag-row">
                            {signal.strategies.slice(0, 3).map((s) => (
                              <Tag key={s} tone="neutral">
                                {strategyLabel(s)}
                              </Tag>
                            ))}
                            {signal.strategies.length > 3 ? <Tag tone="neutral">+{signal.strategies.length - 3}</Tag> : null}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* 数据源状态 */}
          <section className="aq-section">
            <SectionHeader title="数据源状态" />
            <div className="aq-tag-row">
              <Tag tone={isHealthy ? "ok" : isStale ? "warn" : "neutral"}>
                台账: {metrics.data_source_health.ledger}
              </Tag>
              {metrics.data_source_health.latest_signal_date ? (
                <Tag tone="neutral">最新信号: {metrics.data_source_health.latest_signal_date}</Tag>
              ) : null}
              {metrics.data_source_health.ledger_updated_at ? (
                <Tag tone="neutral">更新于: {metrics.data_source_health.ledger_updated_at}</Tag>
              ) : null}
            </div>
          </section>

          <Badge tone="neutral">缓存 5 分钟 · 只读</Badge>
        </>
      ) : null}
    </div>
  );
}
