// 「今日研究」页的编排层。
//
// 2026-11-09 瘦身：候选池 / 研究依据 / 风险提醒 / 候选观察 / 收盘复盘已各自独立成
// 路由模块（见 lib/ia.ts 的导航三组），本页只留**总览**——当天结论、门禁、阶段进度、
// 候选摘要与持仓跟进。原来塞在这里的证据链、消息讨论、决策依据都搬去了对应模块，
// 不再用折叠区藏起来。
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { CalendarDays, Compass, RefreshCw, Sparkles } from "lucide-react";
import { Badge, HeroState, LoadingState, ToneCallout } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import {
  buildDailyView,
  type DailyView,
} from "@/lib/daily-view";
import { formatResearchDate } from "@/lib/research-view";
import { scheduleHashTargetScroll } from "@/lib/hash-navigation";
import { formatAqspTime, isAqspSnapshotStale, useWorkspaceSnapshot } from "./useAqspSnapshot";
import { MarketStrip } from "./MarketStrip";
import { ReviewExportButton } from "./ReviewExportButton";
import { CandidateSummary } from "./CandidateSummary";
import { CandidateDrawer } from "./CandidateDrawer";
import { TodayStrategyBoard, StrategyRecordDetails } from "./TodayStrategyBoard";
import type { AqspSnapshot } from "@/types/aqsp";
import { ALERTS_CHANGED_EVENT, shanghaiDate } from "@/lib/operation";

function TodayHeader({
  date,
  snapshot,
  loading,
  onRefresh,
  actions,
}: {
  date: string;
  snapshot: AqspSnapshot | null;
  loading: boolean;
  onRefresh: () => void;
  actions?: ReactNode;
}) {
  const stale = snapshot ? isAqspSnapshotStale(snapshot) : false;
  const historical = snapshot?.meta?.historical ?? false;
  const freshness = snapshot?.meta?.freshness;
  return (
    <header className="aq-page-head">
      <div>
        <p className="aq-eyebrow">
          <Compass aria-hidden="true" />
          AQSP · 今日投资研究
        </p>
        <div className="aq-title-row">
          <h1>今日研究台</h1>
          <strong>{shanghaiDate()}</strong>
        </div>
        <p className="aq-page-sub">
          先看大盘与行业，再研究选股理由和 AI 路线；采用条件后继续跟进。
        </p>
        <div className="aq-meta-row">
          <span>候选日期 {date || "待产出"}</span>
          {snapshot ? <span>更新 {formatAqspTime(snapshot.generated_at)}</span> : null}
          {snapshot ? (
            <Badge tone={historical || stale ? "warn" : "ok"}>
              {historical ? "历史日期" : stale ? "当前快照已过期" : "当前数据"}
            </Badge>
          ) : null}
          {!stale && !historical && freshness?.candidates === "fresh" ? <Badge tone="ok">行情新鲜</Badge> : null}
          {freshness?.messages === "stale" ? <Badge tone="warn">消息滞后</Badge> : null}
        </div>
      </div>
      <div className="aq-row-actions">
        {actions}
        <button
          type="button"
          className="aq-btn"
          onClick={onRefresh}
          disabled={loading}
          title="刷新研究数据"
        >
          <RefreshCw className={cn(loading && "aq-spin")} aria-hidden="true" />
          刷新
        </button>
      </div>
    </header>
  );
}

function DatePicker({ snapshot }: { snapshot: AqspSnapshot }) {
  const { loading, selectedDate, selectDate } = useWorkspaceSnapshot();
  const activeDate = selectedDate || snapshot.selected_date;
  return (
    <div className="aq-date-bar" aria-label="研究日期">
      <CalendarDays className="aq-date-icon" aria-hidden="true" />
      <span className="aq-date-label">候选日期</span>
      <div className="aq-date-list">
        {snapshot.available_dates.map((date) => {
          const label = formatResearchDate(date);
          const active = date === activeDate;
          return (
            <button
              key={date}
              type="button"
              className={cn("aq-date", active && "aq-date-active")}
              onClick={() => selectDate(date)}
              disabled={loading && active}
              aria-pressed={active}
            >
              <b>{label.day}</b>
              <span>{label.weekday}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CrossMarketStrip({ view }: { view: DailyView }) {
  const cm = view.crossMarket;
  if (!cm) return null;
  return (
    <div className="aq-cm-strip" aria-label="跨市主线">
      <div className="aq-cm-item aq-cm-main">
        <span className="aq-cm-label">跨市主线</span>
        <b>{cm.theme}</b>
        {cm.strength ? <span className="aq-cm-strength">{cm.strength}</span> : null}
      </div>
      <div className="aq-cm-item">
        <span className="aq-cm-label">先看</span>
        <b>{cm.watch}</b>
      </div>
      <div className="aq-cm-item aq-tone-ok">
        <span className="aq-cm-label">确认</span>
        <b>{cm.confirm}</b>
      </div>
      <div className="aq-cm-item aq-tone-warn">
        <span className="aq-cm-label">失效</span>
        <b>{cm.invalid}</b>
      </div>
    </div>
  );
}

function PhaseStrip({ view }: { view: DailyView }) {
  const phases = view.phaseLanes;
  const produced = phases.filter((phase) => phase.produced).length;
  return (
    <div className="aq-phase-strip">
      <div className="aq-phase-lane">
        {phases.map((phase) => (
          <div className={cn("aq-phase", !phase.produced && "aq-phase-empty")} key={phase.id}>
            <div>
              <b>{phase.label}</b>
              <span>{phase.status}</span>
            </div>
            <small>{phase.note}</small>
          </div>
        ))}
      </div>
      <div className="aq-status-line">
        <span>
          <b>{view.candidates.length}</b>候选
        </span>
        <span>
          <b>{view.messages.length}</b>消息
        </span>
        <span>
          <b>{view.debates.length}</b>复核
        </span>
        <span>
          <b>{view.coverageText}</b>全池周期覆盖
        </span>
        <span className="aq-status-muted">
          阶段 {produced}/{phases.length}
        </span>
      </div>
    </div>
  );
}

/** 常驻块：市场环境 → 跨市主线 → 结论 → 门禁 → 阶段。不随页签变化。 */
function PersistentBlock({ view }: { view: DailyView }) {
  return (
    <div className="aq-persistent">
      <CrossMarketStrip view={view} />

      <div className="aq-lead-grid">
        <section className="aq-conclusion">
          <p className="aq-eyebrow">
            <Sparkles aria-hidden="true" />
            当天结论
          </p>
          <strong>{view.conclusion || "当天结论未记录"}</strong>
          <p className="aq-conclusion-sub">{view.chainDetail}</p>
        </section>

        <div className="aq-lead-gate">
          <ToneCallout tone={view.gate.tone} title={view.gate.label} detail={view.gate.detail} />
          <ToneCallout tone={view.chain.tone} title={view.chain.label} detail={view.chain.detail} />
          {view.isEmptyObservation ? (
            <ToneCallout
              tone="warn"
              title="当天暂无实时产物"
              detail="当前页面不使用历史数据代替当天候选。下一次任务产出后会更新这里；需要回看可用上方日期切换。"
            />
          ) : null}
        </div>
      </div>

      <PhaseStrip view={view} />
    </div>
  );
}

/** 顶部 KPI 数据块：候选 / 门禁 / 研究链 / 覆盖 / 消息，一眼可读。 */
function KpiRow({ view }: { view: DailyView }) {
  const top = view.candidates.reduce((max, row) => Math.max(max, row.score ?? 0), 0);
  const tiles: Array<{ label: string; value: string; sub?: string; tone?: "ok" | "warn" | "neutral" }> = [
    { label: "候选", value: String(view.candidates.length), sub: view.candidates.length ? `最高 ${top.toFixed(1)} 分` : "今日无标的", tone: view.candidates.length ? "ok" : "neutral" },
    { label: "门禁", value: view.gate.label, sub: view.gate.detail, tone: view.gate.tone },
    { label: "研究链", value: view.chain.label, sub: view.chainDetail, tone: view.chain.tone },
    { label: "覆盖", value: view.coverageText || "—", sub: view.hasLiveContent ? "实时产物" : "非实时", tone: "neutral" },
    { label: "消息/复核", value: `${view.messages.length} / ${view.debates.length}`, sub: "条消息 · 只复核", tone: "neutral" },
  ];
  return (
    <div className="aq-kpi-row">
      {tiles.map((tile) => (
        <div key={tile.label} className={cn("aq-kpi", tile.tone && `aq-kpi-${tile.tone}`)}>
          <span className="aq-kpi-label">{tile.label}</span>
          <strong className="aq-kpi-value">{tile.value}</strong>
          {tile.sub ? <span className="aq-kpi-sub">{tile.sub}</span> : null}
        </div>
      ))}
    </div>
  );
}

export function TodayWorkspace() {
  const { data, loading, error, refresh, selectedDate, switching } = useWorkspaceSnapshot();
  const location = useLocation();
  const { hash } = location;
  const [searchParams, setSearchParams] = useSearchParams();
  // `?symbol=` 让命令面板 / 分享链接能直接打开某只股票的详情
  const symbolParam = searchParams.get("symbol");
  const [picked, setPicked] = useState<string | null>(symbolParam);

  useEffect(() => {
    if (symbolParam) setPicked(symbolParam);
  }, [symbolParam]);

  const closeDrawer = useCallback(() => {
    setPicked(null);
    if (!symbolParam) return;
    const next = new URLSearchParams(searchParams);
    next.delete("symbol");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, symbolParam]);

  // 日期未对齐时不给页面任何"当前日期"的展示模型，从根上杜绝张冠李戴。
  const view = data && !switching ? buildDailyView(data) : null;
  const researchReady = !!view;
  useEffect(() => {
    if (["#market-context", "#candidates", "#strategy-followups"].includes(hash)) {
      return scheduleHashTargetScroll(hash);
    }
  }, [hash, researchReady]);

  const targetDate = selectedDate || data?.selected_date || "";

  return (
    <div className="aq-page aq-today-workspace">
      <TodayHeader
        date={targetDate}
        snapshot={switching ? null : data}
        loading={loading}
        onRefresh={() => { refresh(); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); }}
        actions={view ? <ReviewExportButton view={view} /> : null}
      />

      {data ? <DatePicker snapshot={data} /> : null}

      {error && data && !switching ? (
        <div className="aq-refresh-warning" role="alert">
          <strong>刷新失败，保留上次成功数据</strong>
          <span>{error} · 上次更新 {formatAqspTime(data.generated_at)}</span>
        </div>
      ) : null}

      {switching && error ? (
        <HeroState tone="warn" title={`无法读取 ${selectedDate} 的研究记录`} detail={`${error}。保留日期选择，重试或切换其他日期。`} action={<button type="button" className="aq-btn aq-btn-primary" onClick={refresh}><RefreshCw aria-hidden="true" />重试该日期</button>} />
      ) : switching ? (
        <LoadingState label={`正在读取 ${selectedDate} 的研究数据…`} />
      ) : error && !data ? (
        <HeroState
          tone="warn"
          title="读不到研究快照"
          detail={`${error}。可点「重试」重新读取；若持续失败，通常是后端未启动，或当日快照（data/runtime/home_dashboard_snapshot.json）尚未产出。`}
          action={
            <button type="button" className="aq-btn aq-btn-primary" onClick={refresh}>
              <RefreshCw aria-hidden="true" />
              重试
            </button>
          }
        />
      ) : !data ? (
        <LoadingState />
      ) : !view ? (
        <LoadingState />
      ) : (
        <>
          {/* 首屏仪表盘：先给总量与门禁，再看市场与结论。 */}
          <KpiRow view={view} />
          <section id="market-context" className="aq-section aq-market-anchor" aria-label="市场与行业方向">
            <MarketStrip />
          </section>
          <PersistentBlock view={view} />
          <CandidateSummary view={view} onPick={setPicked} />
        </>
      )}

      <TodayStrategyBoard />
      <StrategyRecordDetails />

      <CandidateDrawer view={view} symbol={picked} onClose={closeDrawer} />
    </div>
  );
}
