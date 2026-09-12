// 「今日研究」页的编排层。
//
// 版面顺序 = 判断顺序：
//   页头（哪一天 / 数据可不可信 / 刷新）
//   → 常驻块（跨市主线 → 当天结论 → 门禁 → 研究链与阶段）
//   → 页签（候选 / 证据 / 讨论）
//
// 常驻块不随页签变化，所以"今天能不能动"永远第一眼可见；页签只承载细节。
// 所有派生都在 daily-view 里完成，本文件不做业务判断。
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { CalendarDays, Compass, RefreshCw, Sparkles } from "lucide-react";
import { Badge, ErrorState, LoadingState, ToneCallout } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import {
  buildDailyView,
  resolveTodaySection,
  TODAY_SECTION_CATALOGUE,
  type DailyView,
} from "@/lib/daily-view";
import { formatResearchDate } from "@/lib/research-view";
import { formatAqspTime, isAqspSnapshotStale, useWorkspaceSnapshot } from "./useAqspSnapshot";
import { MarketStrip } from "./MarketStrip";
import { ReviewExportButton } from "./ReviewExportButton";
import { StockDetailDrawer, type StockIntro } from "./StockDetailDrawer";
import { CandidateSection } from "./sections/CandidateSection";
import { MessageSection } from "./sections/MessageSection";
import { DiscussionSection } from "./sections/DiscussionSection";
import type { AqspSnapshot } from "@/types/aqsp";

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
          AQSP · 短线研究
        </p>
        <div className="aq-title-row">
          <h1>今日研究</h1>
          <strong>{date || "日期未记录"}</strong>
        </div>
        <div className="aq-meta-row">
          {snapshot ? <span>更新 {formatAqspTime(snapshot.generated_at)}</span> : null}
          {snapshot ? (
            <Badge tone={historical || stale ? "warn" : "ok"}>
              {historical ? "历史日期" : stale ? "当前快照已过期" : "当前数据"}
            </Badge>
          ) : null}
          {freshness?.candidates === "fresh" ? <Badge tone="ok">行情新鲜</Badge> : null}
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
      <span className="aq-date-label">研究日期</span>
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
      <MarketStrip />
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
              detail="当前页面不使用历史数据代替当天候选。下一次任务产出后会更新这里；需要回看可去「结论归档」。"
            />
          ) : null}
        </div>
      </div>

      <PhaseStrip view={view} />
    </div>
  );
}

function SectionTabs({ active }: { active: string }) {
  return (
    <nav className="aq-tabs" aria-label="今日研究分段">
      {TODAY_SECTION_CATALOGUE.map((section) => (
        <Link
          key={section.id}
          to={`/today#${section.id}`}
          className={cn("aq-tab", active === section.id && "aq-tab-active")}
        >
          <span className="aq-tab-label">{section.tabLabel}</span>
          <span className="aq-tab-desc">{section.description}</span>
        </Link>
      ))}
    </nav>
  );
}

function ActiveSection({
  view,
  active,
  onPick,
}: {
  view: DailyView;
  active: string;
  onPick: (symbol: string) => void;
}) {
  const section = view.sections.find((item) => item.id === active) ?? view.sections[0];
  if (section.id === "candidates") return <CandidateSection view={view} section={section} onPick={onPick} />;
  if (section.id === "messages") return <MessageSection view={view} section={section} />;
  return <DiscussionSection view={view} section={section} />;
}

export function TodayWorkspace() {
  const { data, loading, error, refresh, selectedDate, switching } = useWorkspaceSnapshot();
  const { hash } = useLocation();
  const active = resolveTodaySection(hash);
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
  const view = useMemo(() => (data && !switching ? buildDailyView(data) : null), [data, switching]);

  const targetDate = selectedDate || data?.selected_date || "";

  // 抽屉只展示 AQSP 自己的判断；落在当天候选里时才有评分与证据链
  const pickedRow = picked ? view?.candidates.find((row) => row.symbol === picked) : undefined;
  const intro: StockIntro | undefined = pickedRow
    ? {
        scoreText: pickedRow.scoreText,
        status: pickedRow.status,
        evidence: pickedRow.evidence,
        ready: pickedRow.ready,
        strategies: pickedRow.strategies ? [pickedRow.strategies] : [],
        reasons: pickedRow.reasons,
        nextStep: pickedRow.nextStep,
        context: pickedRow.context,
      }
    : undefined;

  return (
    <div className="aq-page">
      <TodayHeader
        date={targetDate}
        snapshot={switching ? null : data}
        loading={loading}
        onRefresh={refresh}
        actions={view ? <ReviewExportButton view={view} /> : null}
      />

      {data ? <DatePicker snapshot={data} /> : null}

      {switching ? (
        <LoadingState label={`正在读取 ${selectedDate} 的研究数据…`} />
      ) : error && !data ? (
        <ErrorState error={error} onRefresh={refresh} />
      ) : !data ? (
        <LoadingState />
      ) : !view ? (
        <LoadingState />
      ) : (
        <>
          <PersistentBlock view={view} />
          <SectionTabs active={active} />
          <main className="aq-active-section" aria-live="polite">
            <ActiveSection view={view} active={active} onPick={setPicked} />
          </main>
        </>
      )}

      <StockDetailDrawer symbol={picked} onClose={closeDrawer} intro={intro} />
    </div>
  );
}
