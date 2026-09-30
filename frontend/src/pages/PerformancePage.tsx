// 选股绩效：回答"选得准不准"。
//
// 这是复盘真正的核心 —— 之前前端只能回答"今天选了什么"，
// 而"选得准不准"（命中率、策略权重、衰减告警）一直在 Python 侧，界面上看不见。
//
// ⚠️ 宪法 §5.4：冷启动期（独立信号日 < 30）**不展示胜率**，只显示积累进度。
// 样本不足时给出数字会制造虚假信心，所以这一页在此情况下必须显示"—"。
import { useCallback, useEffect, useState } from "react";
import { Gauge, RefreshCw, TrendingDown } from "lucide-react";
import { Link } from "react-router-dom";
import {
  api,
  type IcDualVerdictPayload,
  type IcHistoryPayload,
  type PerformancePayload,
} from "@/lib/api";
import { icTrendOption } from "@/lib/chart-options";
import { useThemeMode } from "@/lib/theme-mode";
import { EChart } from "@/components/ui/EChart";
import {
  Badge,
  EmptyState,
  LoadingState,
  SectionHeader,
  StatePanel,
  Tag,
  ToneCallout,
} from "@/components/ui/primitives";
import {
  factorLabel,
  ledgerStatusLabel,
  strategyLabel,
  strategyListLabel,
} from "@/lib/display-labels";
import {
  exitReasonLabel,
  formatReturnPct,
  normalizePerformance,
  performanceHeadline,
  recentPicksSummary,
  sortRecentPicks,
  type RecentPickView,
  type RecentPicksSortKey,
  type SortDir,
  stalenessMessage,
} from "@/lib/performance-view";
import { dualVerdictSummary, type DualFactorView } from "@/lib/dual-verdict-view";
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
  freshness: {
    latest_signal_date: "",
    ledger_updated_at: "",
    trading_days_since_latest: null,
    stale: false,
    stale_after_trading_days: 5,
  },
  overall: null,
  strategies: [],
  decay_alerts: [],
  status_counts: {},
  notes: [],
};

// 台账状态分布与策略名的中文映射统一走 lib/display-labels（未知码原样透传）。

export function PerformancePage() {
  const [payload, setPayload] = useState<PerformancePayload>(EMPTY_PAYLOAD);
  const [icHistory, setIcHistory] = useState<IcHistoryPayload | null>(null);
  const [icDual, setIcDual] = useState<IcDualVerdictPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const mode = useThemeMode();

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
    // 因子 IC 趋势：独立加载，失败不影响绩效主视图（fail-soft）
    try {
      setIcHistory(await api.icHistory());
    } catch {
      setIcHistory(null);
    }
    // 双窗因子 IC 判决（proposal-only 监控面）：独立加载，fail-soft
    try {
      setIcDual(await api.icDualVerdict());
    } catch {
      setIcDual(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const view = normalizePerformance(payload);

  // 票级复盘明细：仅有已结算（validated）逐笔时挂载；空数据整块隐藏，不编数字。
  const picksSummary = recentPicksSummary(view.recentPicks);

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
          {/* 停滞优先于冷启动提示：流水线停了，进度会永远卡住，必须说清 */}
          {view.stale ? (
            <ToneCallout
              tone="warn"
              title={`台账已停滞：${stalenessMessage(view)}`}
              detail={`超过 ${view.staleAfterTradingDays} 个交易日未更新即视为停滞（按交易日历，长假不算）。冷启动进度不会再推进 —— 先检查产出流水线，而不是等它自己攒够样本。`}
            />
          ) : null}

          {view.coldStart ? (
            <ToneCallout
              tone={view.stale ? "neutral" : "warn"}
              title={`冷启动期：已积累 ${view.independentSignalDays}/${view.minSignalDays} 个独立信号日`}
              detail="样本量不足以支撑统计推断，按宪法 §5.4 暂不展示胜率。攒够独立信号日后会自动显示。"
            />
          ) : null}

          {!view.stale && view.latestSignalDate ? (
            <p className="aq-note">
              {stalenessMessage(view)}（台账写入于 {view.ledgerUpdatedAt || "未知"}）
            </p>
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

          {/* 因子 IC 趋势（每日滚动诊断回流；换族决策的监控面） */}
          <section className="aq-section">
            <SectionHeader
              title="因子 IC 趋势"
              description="每日滚动诊断回流 · 线在零轴上方 = 正向（每日 146 截面）"
            />
            {icHistory && icHistory.points.length > 0 ? (
              <>
                <EChart
                  option={icTrendOption(icHistory.points, mode)}
                  height={260}
                  fallback={
                    <StatePanel tone="warn">图表库不可用，请查看收盘日报的 IC 段。</StatePanel>
                  }
                />
                <div className="aq-flow-cols">
                  {Object.entries(icHistory.latest_factors).map(([name, value]) => (
                    <div key={name} className="aq-insights-card">
                      <b className={value > 0 ? "aq-tone-up" : "aq-tone-down"}>
                        {value.toFixed(4)}
                      </b>
                      <span>{factorLabel(name)} · 最新 IC</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <EmptyState
                title="暂无 IC 历史数据"
                detail="runner 每日滚动诊断回流后，这里会出现各因子 IC 的时间序列。"
              />
            )}
          </section>

          {/* 双窗因子 IC 滚动判决（proposal-only 监控面：换族判据的每日滚动版） */}
          <DualVerdictBar icDual={icDual} />

          {/* 票级复盘明细：上次具体选了哪只票、事后结果如何（逐笔事实，非统计推断） */}
          {view.available && picksSummary.total > 0 ? (
            <RecentPicksDetail picks={view.recentPicks} />
          ) : null}

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
                        <td>{strategyLabel(row.name)}</td>
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
                        ? `${strategyLabel(alert.strategy)}：近期${alert.severity === "critical" ? "明显" : ""}走弱（近 ${alert.lookbackDays} 天样本不足，不展示胜率）`
                        : `${strategyLabel(alert.strategy)}：近 ${alert.lookbackDays} 天命中率 ${(alert.recentWinRate * 100).toFixed(1)}%`
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
                    {ledgerStatusLabel(status)} {count}
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

/**
 * 票级复盘明细（#69b）：把"上次具体选了哪只票、事后结果如何"落成一张可扫读的完整表。
 *
 * 与 DecisionPanel 内嵌的紧凑 `RecentPicksTable` 的区别：这里是**绩效页的一等公民**——
 * 带汇总条（命中/未中/平均收益·仅观测）+ 可点击列头排序，适合"逐笔回看"，而非决策面板里
 * 扫一眼的缩略表。数据与 DecisionPanel 同源（performance.recent_picks），零重复拉取。
 *
 * 诚实边界：
 *   - 命中/未中是逐笔事实计数（不受 §5.4 样本门槛约束，可如实展示）；
 *   - 平均收益是 PnL 派生，标注"仅观测"，绝不当主指标（§8）；
 *   - 收益缺失（returnPct/excessReturnPct = null）显示 "—"，与"真的是 0"区分开；
 *   - 红涨绿跌：A 股约定，正收益着色 aq-pick-return-pos、负收益着色 aq-pick-return-neg。
 */
function RecentPicksDetail({ picks }: { picks: readonly RecentPickView[] }) {
  const [sortKey, setSortKey] = useState<RecentPicksSortKey>("signalDate");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const rows = sortRecentPicks(picks, sortKey, sortDir);
  const sum = recentPicksSummary(picks);

  const toggle = (key: RecentPicksSortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      // 首次点某列给最直觉的方向：日期升序（旧→新），收益/超额降序（大→小）
      setSortDir(key === "signalDate" ? "asc" : "desc");
    }
  };

  // 列头排序指示：只在当前排序列上显，方向明确
  const sortMark = (key: RecentPicksSortKey) =>
    key !== sortKey ? null : sortDir === "asc" ? " ↑" : " ↓";

  return (
    <section className="aq-section">
      <SectionHeader
        title="票级复盘明细"
        description="上次具体选了哪只票、事后结果如何 · 逐笔事实（命中率见「按策略」，平均收益仅观测 §8）"
        count={`${sum.total} 笔`}
      />

      {/* 汇总条：命中/未中是逐笔事实，可如实展示；平均收益标注仅观测 */}
      <div className="aq-flow-cols">
        <div className="aq-insights-card">
          <b className="aq-tone-up">{sum.hitRatio !== null ? `${(sum.hitRatio * 100).toFixed(1)}%` : "—"}</b>
          <span>命中 {sum.winCount} / 未中 {sum.loseCount}</span>
        </div>
        <div className="aq-insights-card">
          <b
            className={
              sum.avgReturnPct === null
                ? undefined
                : sum.avgReturnPct > 0
                  ? "aq-pick-return-pos"
                  : sum.avgReturnPct < 0
                    ? "aq-pick-return-neg"
                    : "aq-pick-return-mid"
            }
          >
            {sum.avgReturnPct !== null ? formatReturnPct(sum.avgReturnPct) : "—"}
          </b>
          <span>平均收益（仅观测 · {sum.recordedReturns} 笔已记录）</span>
        </div>
        <div className="aq-insights-card">
          <b>
            <span className="aq-pick-return-pos">{sum.posCount}</span>
            {" / "}
            <span className="aq-pick-return-neg">{sum.negCount}</span>
          </b>
          <span>正 / 负收益笔数</span>
        </div>
      </div>

      <div className="aq-table-wrap">
        <table className="aq-table aq-decision-review-table">
          <thead>
            <tr>
              <th>
                <button type="button" className="aq-th-sort" onClick={() => toggle("signalDate")}>
                  代码 / 名称{sortMark("signalDate")}
                </button>
              </th>
              <th>信号日</th>
              <th>了结</th>
              <th className="aq-num">
                <button
                  type="button"
                  className="aq-th-sort aq-num"
                  onClick={() => toggle("returnPct")}
                >
                  收益{sortMark("returnPct")}
                </button>
              </th>
              <th className="aq-num">
                <button
                  type="button"
                  className="aq-th-sort aq-num"
                  onClick={() => toggle("excessReturnPct")}
                >
                  超额{sortMark("excessReturnPct")}
                </button>
              </th>
              <th>结果 / 策略</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const retClass =
                row.returnPct === null
                  ? "aq-pick-return-mid"
                  : row.returnPct > 0
                    ? "aq-pick-return-pos"
                    : row.returnPct < 0
                      ? "aq-pick-return-neg"
                      : "aq-pick-return-mid";
              const excessClass =
                row.excessReturnPct === null
                  ? "aq-pick-return-mid"
                  : row.excessReturnPct > 0
                    ? "aq-pick-return-pos"
                    : row.excessReturnPct < 0
                      ? "aq-pick-return-neg"
                      : "aq-pick-return-mid";
              return (
                <tr key={`${row.symbol}-${row.signalDate}`}>
                  <td>
                    <Link to={`/stock/${row.symbol}`} className="aq-link">
                      <b>{row.symbol}</b>
                    </Link>
                    <span className="aq-decision-cand-name">{row.name}</span>
                  </td>
                  <td className="aq-num">{row.signalDate || "—"}</td>
                  <td className="aq-num">
                    {row.exitDate ? (
                      <>
                        {row.exitDate}
                        <div className="aq-decision-review-strat">{exitReasonLabel(row.exitReason)}</div>
                      </>
                    ) : (
                      <span className="aq-pick-return-mid">未了结</span>
                    )}
                  </td>
                  <td className={cn("aq-num", retClass)}>{formatReturnPct(row.returnPct)}</td>
                  <td className={cn("aq-num", excessClass)}>
                    {row.excessReturnPct === null ? "—" : formatReturnPct(row.excessReturnPct)}
                  </td>
                  <td>
                    <Tag tone={row.win ? "ok" : "neutral"}>{row.win ? "命中" : "未中"}</Tag>
                    {row.strategies.length ? (
                      <span className="aq-decision-review-strat">{strategyListLabel(row.strategies)}</span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * 双窗因子 IC 滚动判决状态条（proposal-only 监控面）。
 *
 * 数据 = /aqsp/ic-dual-verdict（runner 每日双窗判决回流）；经 `dualVerdictSummary`
 * 归一成三态（hit 达标 / pending 方向稳待功效 / miss 翻向）。
 * 红线：纯展示「何时该回换族流程」的监控读数，**绝不写回打分/排序/下单**。
 * 配色沿用红涨绿跌（同票级复盘表）：t 正向 = 红（有效因子）、t 反向 = 绿（失效因子）。
 */
function tColumnClass(t: number | null): string {
  if (t === null) return "";
  return t > 0 ? "aq-pick-return-pos" : t < 0 ? "aq-pick-return-neg" : "";
}

function DualVerdictBar({ icDual }: { icDual: IcDualVerdictPayload | null }) {
  const v = dualVerdictSummary(icDual);

  if (!v.available || v.total === 0) {
    const emptyTitle = v.stale
      ? "双窗判决已隔离（数据陈旧）"
      : "暂无双窗判决数据";
    const emptyDetail = v.stale ? (
      "上一份双窗判决已超过新鲜度上限，避免把陈旧判决误当「最新」而隔离展示；等待下一次 runner 回流后自动恢复。"
    ) : (
      "runner 每日双窗判决回流后，这里会展示逐因子达标/观察/翻向状态与连续达标日数。"
    );
    return (
      <section className="aq-section">
        <SectionHeader
          title="双窗因子 IC 判决"
          description="相邻不重叠两窗（各 73 截面）逐因子同号且双 |t|≥2 达标 · 连续 5 个数据日达标 ⇒ 回换族流程（proposal-only，只记录不自动改参）"
        />
        <EmptyState title={emptyTitle} detail={emptyDetail} />
      </section>
    );
  }

  return (
    <section className="aq-section">
      <SectionHeader
        title="双窗因子 IC 判决"
        description={`截至 B 窗 ${v.as_of_b}（前窗 ${v.as_of_a}）· 两窗各 73 截面 · 同号且双 |t|≥2 才达标`}
      />
      <div className="aq-flow-cols">
        <div className="aq-insights-card">
          <b className={v.hitCount > 0 ? "aq-pick-return-pos" : undefined}>{v.hitCount}</b>
          <span>达标因子（同号且双 |t|≥2）</span>
        </div>
        <div className="aq-insights-card">
          <b>{v.pendingCount}</b>
          <span>观察中（方向稳、功效待补）</span>
        </div>
        <div className="aq-insights-card">
          <b className={v.missCount > 0 ? "aq-pick-return-neg" : undefined}>{v.missCount}</b>
          <span>翻向 / 未同向因子</span>
        </div>
      </div>

      {v.event === "revisit_family" ? (
        <p className="aq-note">
          ⚠️ 已触发 <b>revisit_family</b>：有因子连续 {v.streak_n} 个数据日双窗达标 ⇒ 建议回换族流程
          （本层只记录提醒，不自动改权重/下单）。
        </p>
      ) : null}
      {v.next_candidate ? (
        <p className="aq-note">
          观察候选：<b>{factorLabel(v.next_candidate)}</b> 方向稳定但功效未双达标，持续滚动监控。
        </p>
      ) : null}

      <div className="aq-table-wrap">
        <table className="aq-table">
          <thead>
            <tr>
              <th>因子</th>
              <th className="aq-num">A 窗 t</th>
              <th className="aq-num">B 窗 t</th>
              <th>状态</th>
              <th className="aq-num">连续达标日</th>
            </tr>
          </thead>
          <tbody>
            {v.factors.map((f: DualFactorView) => {
              const statusTag =
                f.status === "hit" ? (
                  <Tag tone="ok">达标</Tag>
                ) : f.status === "pending" ? (
                  <Tag tone="warn">观察</Tag>
                ) : (
                  <Tag tone="neutral">翻向</Tag>
                );
              return (
                <tr key={f.name}>
                  <td>{factorLabel(f.name)}</td>
                  <td className={cn("aq-num", tColumnClass(f.t_a))}>
                    {f.t_a === null ? "—" : f.t_a.toFixed(2)}
                  </td>
                  <td className={cn("aq-num", tColumnClass(f.t_b))}>
                    {f.t_b === null ? "—" : f.t_b.toFixed(2)}
                  </td>
                  <td>{statusTag}</td>
                  <td className="aq-num">
                    {f.streak}/{v.streak_n}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="aq-note">
        判据（方案 B §六）：两窗 IC 同号 且 双 |t|≥2；连续 {v.streak_n} 个数据日达标 ⇒
        revisit_family 事件。纯诊断层，不产出信号、不写回打分/排序/下单。
      </p>
    </section>
  );
}
