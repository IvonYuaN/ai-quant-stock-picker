// 「今日研究」页的编排层。
//
// 版面顺序 = 判断顺序：
//   页头（哪一天 / 数据可不可信 / 刷新）
//   → 常驻块（跨市主线 → 当天结论 → 门禁 → 研究链与阶段）
//   → 页签（候选 / 证据 / 讨论）
//
// 常驻块不随页签变化，所以"今天能不能动"永远第一眼可见；页签只承载细节。
// 所有派生都在 daily-view 里完成，本文件不做业务判断。
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { CalendarDays, Compass, RefreshCw, Sparkles } from "lucide-react";
import { Badge, HeroState, LoadingState, ToneCallout } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import {
  buildDailyView,
  resolveTodaySection,
  type DailyView,
  type SectionView,
} from "@/lib/daily-view";
import { formatResearchDate } from "@/lib/research-view";
import { formatAqspTime, isAqspSnapshotStale, useWorkspaceSnapshot } from "./useAqspSnapshot";
import { MarketStrip } from "./MarketStrip";
import { ReviewExportButton } from "./ReviewExportButton";
import { DecisionPanel } from "./DecisionPanel";
import { ClosingReviewPanel } from "./ClosingReviewPanel";
import { StockDetailDrawer, type StockIntro } from "./StockDetailDrawer";
import { CandidateSection } from "./sections/CandidateSection";
import { MessageSection } from "./sections/MessageSection";
import { DiscussionSection } from "./sections/DiscussionSection";
import { AlertInbox } from "./AlertInbox";
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
          AQSP · T+1 决策工作台
        </p>
        <div className="aq-title-row">
          <h1>今日决策</h1>
          <strong>{shanghaiDate()}</strong>
        </div>
        <p className="aq-page-sub">
          先看持仓风险，再核对候选、消息与大盘。
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

function SectionTabs({ active, sections }: { active: string; sections: readonly SectionView[] }) {
  const { search } = useLocation();
  return (
    <nav className="aq-tabs" aria-label="今日研究分段">
      {sections.map((section) => (
        <Link
          key={section.id}
          to={`/today${search}#${section.id}`}
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
}: {
  view: DailyView;
  active: string;
}) {
  // 候选段已固定在首屏（见 TodayWorkspace 的渲染顺序），本组件只承载「细节段」。
  // 所以兜底**不能**再落到 sections[0] —— 那是候选段，会让首屏那份被渲染第二遍。
  const section =
    view.sections.find((item) => item.id === active && item.id !== "candidates") ??
    view.sections.find((item) => item.id !== "candidates");
  if (!section) return null;
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
  const view = data && !switching ? buildDailyView(data) : null;

  // 首屏即选股：候选段固定顶到最上面，其余段（为什么 / 多空观点）走下方页签。
  const candidateSection = view?.sections.find((section) => section.id === "candidates");
  const detailSections = view ? view.sections.filter((section) => section.id !== "candidates") : [];
  const activeDetailId =
    detailSections.some((section) => section.id === active) ? active : detailSections[0]?.id ?? "";

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
    <div className="aq-page aq-today-workspace">
      <TodayHeader
        date={targetDate}
        snapshot={switching ? null : data}
        loading={loading}
        onRefresh={() => { refresh(); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); }}
        actions={view ? <ReviewExportButton view={view} /> : null}
      />

      {/* 实盘该卖提醒（#431）：读的是实盘持仓的 LIVE 风控触发，与研究历史日期解耦——
          切日期 / 本地快照缺失都不该让"今天能不能卖"这条风控提醒消失，故置于主分支外、最靠上。 */}
      <AlertInbox />

      {data ? <DatePicker snapshot={data} /> : null}

      {error && data && !switching ? (
        <div className="aq-refresh-warning" role="alert">
          <strong>刷新失败，保留上次成功数据</strong>
          <span>{error} · 上次更新 {formatAqspTime(data.generated_at)}</span>
        </div>
      ) : null}

      {/* 本地研究快照缺失/读取中时，仍然展示**直接读公开源**的实时市场环境
          （指数 / 涨跌家数 / 隔夜外围）。实时行情不该被本地重管线的产出卡住 ——
          快照是本机算出来的，市场环境是别人网站上现成的，两者解耦。 */}
      {!data ? <MarketStrip /> : null}

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
          {candidateSection ? (
            <CandidateSection view={view} section={candidateSection} onPick={setPicked} />
          ) : null}
          <MarketStrip />
          <details className="aq-workspace-fold" open={active !== "candidates" || undefined} key={active}>
            <summary>消息证据与多空讨论 <span>{view.messages.length} 条消息 · {view.debates.length} 只复核</span></summary>
            <div className="aq-workspace-fold-body">
              <SectionTabs active={activeDetailId} sections={detailSections} />
              <div className="aq-active-section" aria-live="polite"><ActiveSection view={view} active={activeDetailId} /></div>
            </div>
          </details>
          <details className="aq-workspace-fold">
            <summary>研究过程与验证依据</summary>
            <div className="aq-workspace-fold-body"><PersistentBlock view={view} /><DecisionPanel snapshot={data} /></div>
          </details>
          <details className="aq-workspace-fold">
            <summary>收盘复盘与历史验证</summary>
            <div className="aq-workspace-fold-body"><ClosingReviewPanel /></div>
          </details>
        </>
      )}

      <StockDetailDrawer symbol={picked} onClose={closeDrawer} intro={intro} />
    </div>
  );
}
