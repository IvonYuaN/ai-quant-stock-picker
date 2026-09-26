// 「今日决策」面板：把散在候选 / 走门 / 绩效 / 台账新鲜度四处的问题
// 收敛成一句话结论 + 阻塞项 + 可动/观察表，让"今天到底能不能下手"一眼可见。
//
// 数据面只读两个已存在的端点：
//   - snapshot（由父级 useWorkspaceSnapshot 提供，含 recommendation_gate + candidates）
//   - performance（本组件独立拉取，含命中率 / 退化预警 / 台账新鲜度 / 冷启动）
// 全部派生走 lib/decision-view 的纯函数，本组件不写任何业务判断、不触发下单（§5 红线）。
//
// 与整页一致：加载/错误都优雅降级为一条细缝，绝不把"读不到绩效"升级成整页白屏。
import { useCallback, useEffect, useRef, useState } from "react";
import { ClipboardCheck, OctagonPause } from "lucide-react";
import { api, type PerformancePayload } from "@/lib/api";
import {
  exitReasonLabel,
  formatReturnPct,
  normalizePerformance,
  stalenessMessage,
  type RecentPickView,
} from "@/lib/performance-view";
import {
  buildDecision,
  formatHitRate,
  tableMode,
  type DecisionView,
} from "@/lib/decision-view";
import { Badge, LoadingState, SectionHeader, Tag, ToneCallout } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import type { AqspSnapshot } from "@/types/aqsp";

// 绩效端点失败时的兜底：把 available 置 false，让面板只呈现"走门 + 候选"，
// 不因"读不到绩效"就把整块决策区藏掉。
const EMPTY_PERF: PerformancePayload = {
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

export function DecisionPanel({ snapshot }: { snapshot: AqspSnapshot }) {
  const [perfPayload, setPerfPayload] = useState<PerformancePayload | null>(null);
  const [perfError, setPerfError] = useState("");
  const seq = useRef(0);

  const load = useCallback(() => {
    const run = seq.current + 1;
    seq.current = run;
    setPerfError("");
    api
      .performance()
      .then((payload) => {
        if (seq.current !== run) return;
        setPerfPayload(payload ?? EMPTY_PERF);
      })
      .catch((err) => {
        if (seq.current !== run) return;
        setPerfPayload(EMPTY_PERF);
        setPerfError(err instanceof Error ? err.message : "绩效读取失败");
      });
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // performance 尚未到：只给一条细缝，不隐藏"走门 + 候选"（下面会先渲染能渲染的部分）。
  const perf = perfPayload ? normalizePerformance(perfPayload) : null;
  const decision: DecisionView | null =
    perf && perfPayload ? buildDecision(snapshot, perf, stalenessMessage(perf)) : null;

  // 结论语气：能明确"可动/不可动"才有颜色；连快照都读不全时不表态。
  const headlineTone = !decision ? "neutral" : decision.canAct ? "ok" : "warn";
  // perf 还没到（null）时保守取 observe；只有拿到归一化视图才可能判 act。
  const mode = perf ? tableMode(snapshot, perf) : "observe";

  return (
    <section className="aq-decision" aria-label="今日决策">
      <SectionHeader
        icon={decision?.canAct ? ClipboardCheck : OctagonPause}
        title="今日决策"
        description="走门 · 候选 · 命中 · 台账四件事，合成一句能不能下手"
        count={
          <Badge tone={mode === "act" ? "ok" : "warn"}>
            {mode === "act" ? "可动模式" : "观察模式"}
          </Badge>
        }
      />

      {/* 一句结论。绩效未到时给占位，失败时降级为"走门+候选仍可见"。 */}
      {decision ? (
        <ToneCallout tone={headlineTone} title={decision.headline} detail={decision.staleness} />
      ) : perfError ? (
        <ToneCallout
          tone="warn"
          title="绩效暂不可用"
          detail={`走门与候选仍可见；绩效读取失败：${perfError}。`}
        />
      ) : (
        <LoadingState label="正在合成今日决策…" />
      )}

      {/* 阻塞项：空 = 无阻塞。 */}
      {decision && decision.blockers.length > 0 ? (
        <ul className="aq-decision-blockers">
          {decision.blockers.map((blocker, index) => (
            <li key={`${blocker.kind}-${index}`} className={cn("aq-decision-blocker", `aq-decision-blocker-${blocker.tone}`)}>
              <Tag tone={blocker.tone}>{blocker.title}</Tag>
              {blocker.detail ? <span className="aq-decision-blocker-detail">{blocker.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {/* 候选表：gate 未放行时整表降级为"观察"，列头措辞随之切换。 */}
      {decision && decision.candidates.length > 0 ? (
        <div className="aq-table-wrap">
          <table className="aq-table aq-decision-table">
            <thead>
              <tr>
                <th>代码 / 名称</th>
                <th className="aq-num">评分</th>
                <th>状态</th>
                <th>关联策略</th>
                <th>{mode === "act" ? "下一步" : "观察动作"}</th>
              </tr>
            </thead>
            <tbody>
              {decision.candidates.map((row) => (
                <tr key={row.symbol}>
                  <td>
                    <b>{row.symbol}</b>
                    <span className="aq-decision-cand-name">{row.name}</span>
                  </td>
                  <td className="aq-num">{row.score}</td>
                  <td>{row.status}</td>
                  <td>{row.strategies || "—"}</td>
                  <td>{row.nextStep || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : decision ? (
        <div className="aq-state aq-state-neutral">
          <span className="aq-state-text">今日无候选可列。</span>
        </div>
      ) : null}

      {/* 策略健康度：命中率只在 canShowHitRate 为真时呈现（§5.4 诚实门槛）。 */}
      {decision && decision.strategies.length > 0 ? (
        <div className="aq-decision-strategies">
          {decision.strategies.map((line) => (
            <div key={line.name} className="aq-decision-strategy">
              <span className="aq-decision-strategy-name">{line.name}</span>
              <Tag tone={line.decayNote ? "warn" : "neutral"}>
                命中率 {formatHitRate(line)}
              </Tag>
              {line.decayNote ? <span className="aq-decision-strategy-note">{line.decayNote}</span> : null}
            </div>
          ))}
        </div>
      ) : null}

      {/* 票级复盘：上次选了哪只票、事后如何（红涨绿跌，缺失显 "—"）。 */}
      {perf ? <RecentPicksTable picks={perf.recentPicks} /> : null}

      {/* 冷启动进度：只报进度，不报胜率。 */}
      {decision && decision.coldStart ? (
        <div className="aq-progress aq-decision-progress" aria-label="信号日积累进度">
          <div
            className="aq-progress-bar"
            style={{ width: `${Math.round(decision.signalProgress * 100)}%` }}
          />
        </div>
      ) : null}
    </section>
  );
}

/**
 * 票级复盘表：把"上次具体选了哪只票、事后结果如何"落成一张可扫读表。
 *
 * 诚实边界：
 *   - 数据只来自 performance.recent_picks（已过滤 validated 且非模拟行），
 *     读不到（旧后端）时 recentPicks 为空 → 整块不渲染，不编数字。
 *   - 收益缺失（returnPct / excessReturnPct 为 null）显示 "—"，与"真的是 0"区分开。
 *   - 红涨绿跌：A 股约定，正收益着色 ok(红)、负收益着色 warn 侧——
 *     这里用 class 前缀 aq-pick-return-pos / -neg，色值在 index.css 定，遵循"涨红跌绿"。
 */
function RecentPicksTable({ picks }: { picks: readonly RecentPickView[] }) {
  if (picks.length === 0) return null;
  return (
    <div className="aq-decision-review" aria-label="最近复盘">
      <p className="aq-decision-review-title">最近复盘 · 上次选了哪些票、事后如何</p>
      <div className="aq-table-wrap">
        <table className="aq-table aq-decision-review-table">
          <thead>
            <tr>
              <th>代码 / 名称</th>
              <th>信号日</th>
              <th>了结</th>
              <th className="aq-num">收益</th>
              <th className="aq-num">超额</th>
              <th>结果</th>
            </tr>
          </thead>
          <tbody>
            {picks.map((row) => {
              const retClass =
                row.returnPct === null ? "aq-pick-return-mid" : row.returnPct > 0 ? "aq-pick-return-pos" : row.returnPct < 0 ? "aq-pick-return-neg" : "aq-pick-return-mid";
              const excessClass =
                row.excessReturnPct === null ? "aq-pick-return-mid" : row.excessReturnPct > 0 ? "aq-pick-return-pos" : row.excessReturnPct < 0 ? "aq-pick-return-neg" : "aq-pick-return-mid";
              return (
                <tr key={`${row.symbol}-${row.signalDate}`}>
                  <td>
                    <b>{row.symbol}</b>
                    <span className="aq-decision-cand-name">{row.name}</span>
                  </td>
                  <td className="aq-num">{row.signalDate || "—"}</td>
                  <td className="aq-num">{exitReasonLabel(row.exitReason)}</td>
                  <td className={cn("aq-num", retClass)}>{formatReturnPct(row.returnPct)}</td>
                  <td className={cn("aq-num", excessClass)}>
                    {row.excessReturnPct === null ? "—" : formatReturnPct(row.excessReturnPct)}
                  </td>
                  <td>
                    <Tag tone={row.win ? "ok" : "neutral"}>{row.win ? "命中" : "未中"}</Tag>
                    {row.strategies.length ? (
                      <span className="aq-decision-review-strat">{row.strategies.join(" · ")}</span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
