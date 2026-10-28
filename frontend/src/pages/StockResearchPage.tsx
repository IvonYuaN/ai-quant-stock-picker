// 个股研究页（/stock/:code）。
//
// 研究日期来自共享快照；评分、消息和讨论严格同日，当前行情与事件独立展示。
// 页面不重算评分，加入自选只写当前浏览器。
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, CalendarClock, Check, ExternalLink, RefreshCw, Star } from "lucide-react";
import { toast } from "sonner";
import {
  api,
  type DividendPlanEvent,
  type EarningsForecastEvent,
  type EventCalendarData,
  type Quote,
  type RecentLonghubangEvent,
  type SuspendResumeEvent,
  type UpcomingUnlockEvent,
} from "@/lib/api";
import {
  fetchCatalyst,
  filterEventsForSymbol,
  impactLabel,
  impactToneClass,
  type CatalystData,
  type CatalystEvent,
} from "@/lib/catalyst-client";
import {
  netAmountView,
  severityBadgeClass,
  severityLabel,
  sortUnlocksByDaysUntil,
  unlockRatioText,
} from "@/lib/event-view";
import { changeClass, formatSignedPct } from "@/lib/format";
import {
  Badge,
  Card,
  EmptyState,
  HeroState,
  LoadingState,
  SectionHeader,
  StatePanel,
  Tag,
} from "@/components/ui/primitives";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";
import { messagesForCandidate } from "@/lib/candidate-chain";
import { debateProcessText, messageSourceUrl } from "@/lib/research-view";
import { todayResearchHref } from "@/lib/research-route";
import { evidenceLabel, statusLabel } from "@/lib/display-labels";
import { loadWatch, saveWatch, WATCHLIST_CHANGED_EVENT } from "@/lib/watchlist";
import { CandidateObservationPanel } from "@/components/aqsp/CandidateObservationPanel";
import { ResearchValidationContext } from "@/components/aqsp/ResearchValidationContext";
import { OwnedPositionPlanEntry } from "@/components/aqsp/OwnedPositionPlanEntry";
import { cn } from "@/lib/utils";

function extractMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "读取失败";
}

export function StockResearchPage() {
  const { code } = useParams<{ code: string }>();
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteError, setQuoteError] = useState("");
  const [catalyst, setCatalyst] = useState<CatalystData | null>(null);
  const [catalystError, setCatalystError] = useState("");
  const [eventCalendar, setEventCalendar] = useState<EventCalendarData | null>(null);
  const [eventsError, setEventsError] = useState("");
  const [loading, setLoading] = useState(true);
  const [resourceCode, setResourceCode] = useState("");
  const [reload, setReload] = useState(0);
  const [watched, setWatched] = useState(() => loadWatch().includes(code ?? ""));
  const workspace = useWorkspaceSnapshot();
  const snapshot = workspace.data && !workspace.switching && !workspace.error &&
    (workspace.selectedDate ? workspace.data.selected_date === workspace.selectedDate : !workspace.data.meta?.historical)
    ? workspace.data : null;
  const view = snapshot ? buildDailyView(snapshot) : null;
  const row = view?.candidates.find((candidate) => candidate.symbol === code);
  const debate = view?.debates.find((result) => result.symbol === code);
  const datedMessages = snapshot && code ? messagesForCandidate(snapshot.messages, code) : [];
  const validCode = Boolean(code && /^\d{6}$/.test(code));
  const researchDate = view?.date || workspace.selectedDate;

  useEffect(() => {
    const sync = () => setWatched(loadWatch().includes(code ?? ""));
    sync();
    window.addEventListener(WATCHLIST_CHANGED_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(WATCHLIST_CHANGED_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, [code]);

  useEffect(() => {
    let alive = true;
    setResourceCode(code ?? "");
    setQuote(null);
    setCatalyst(null);
    setEventCalendar(null);
    if (!code || !/^\d{6}$/.test(code)) { setLoading(false); return; }
    setLoading(true);
    setQuoteError("");
    setCatalystError("");
    setEventsError("");
    void (async () => {
      const [q, c, e] = await Promise.allSettled([
        api.quote(code),
        fetchCatalyst(),
        api.events(code),
      ]);
      if (!alive) return;
      if (q.status === "fulfilled") setQuote(q.value[code] ?? null);
      else setQuoteError(extractMessage(q.reason));
      if (c.status === "fulfilled") setCatalyst(c.value);
      else setCatalystError(extractMessage(c.reason));
      if (e.status === "fulfilled") setEventCalendar(e.value);
      else setEventsError(extractMessage(e.reason));
      setLoading(false);
    })();
    return () => {
      alive = false;
    };
  }, [code, reload]);

  if (!code || !validCode) {
    return (
      <HeroState
        tone="neutral"
        title="股票代码无效"
        detail="请输入有效的 6 位 A 股代码。"
        action={<Link className="aq-btn" to="/today">返回今日研究</Link>}
      />
    );
  }

  const matchedResource = resourceCode === code;
  const currentQuote = matchedResource ? quote : null;
  const currentCalendar = matchedResource ? eventCalendar : null;
  const events = matchedResource && catalyst ? filterEventsForSymbol(catalyst.events, code) : [];
  const upcomingUnlocks = currentCalendar ? sortUnlocksByDaysUntil(currentCalendar.upcoming_unlocks ?? []) : [];
  const recentLonghubang = currentCalendar?.recent_longhubang ?? [];
  const suspendResumes = currentCalendar?.suspend_resumes ?? [];
  const recentEarnings = currentCalendar?.recent_earnings_forecasts ?? [];
  const upcomingDividends = currentCalendar?.upcoming_dividends ?? [];
  let returnHref = "/today";
  try { returnHref = todayResearchHref(researchDate || undefined); } catch { /* Invalid dates are reported by the snapshot boundary. */ }

  function addToWatchlist() {
    const existing = loadWatch();
    if (existing.includes(code!)) { setWatched(true); return; }
    try {
      saveWatch([...existing, code!]);
      setWatched(true);
      toast("已加入自选，保存在此浏览器");
    } catch {
      toast("自选保存失败，请检查浏览器存储后重试");
    }
  }

  return (
    <div className="aq-page aq-research-workspace">
      <header className="aq-page-head aq-research-head">
        <div>
          <Link className="aq-research-back" to={returnHref}><ArrowLeft aria-hidden="true" />返回{researchDate ? ` ${researchDate} ` : "今日"}研究</Link>
          <p className="aq-eyebrow">个股研究</p>
          <div className="aq-title-row"><h1>{row?.name || currentQuote?.name || code}</h1><span className="aq-code">{code}</span></div>
          <p className="aq-page-sub">{view ? `${view.date} · ${view.isHistorical ? "历史研究记录" : "当日研究快照"}` : workspace.error ? "研究快照暂不可用" : "研究快照读取中"} · 评分与依据来自同一日期</p>
        </div>
        <div className="aq-research-actions">
          <OwnedPositionPlanEntry code={code} />
          <button type="button" className="aq-btn aq-btn-primary" onClick={addToWatchlist} disabled={watched}>
            {watched ? <Check aria-hidden="true" /> : <Star aria-hidden="true" />}{watched ? "已在自选" : "加入自选"}
          </button>
          <Link className="aq-btn" to={researchDate ? `/my/holdings?date=${encodeURIComponent(researchDate)}#watchlist` : "/my/holdings#watchlist"}>我的持仓与自选</Link>
          <button type="button" className="aq-btn aq-btn-icon" title="刷新研究与行情" aria-label="刷新研究与行情" onClick={() => { workspace.refresh(); setReload((value) => value + 1); }}><RefreshCw aria-hidden="true" /></button>
        </div>
      </header>

      <section className="aq-research-summary" aria-label="同日研究摘要">
        {workspace.loading && !view ? <LoadingState label="正在读取所选日期的研究依据…" /> : null}
        {workspace.error ? <StatePanel tone="warn" action={<button type="button" className="aq-btn" onClick={workspace.refresh}>重试研究</button>}>{workspace.error}，暂不展示未核实的评分与依据。</StatePanel> : null}
        {view ? (
          <>
            {snapshot ? <ResearchValidationContext snapshot={snapshot} symbol={code} /> : null}
            {!row ? <EmptyState title="该股票不在所选日期候选中" detail={`${view.date} 未提供 ${code} 的 AQSP 评分与证据链，可查看独立行情和事件，也可加入自选。`} /> : (
              <>
                <div className="aq-research-score"><strong className="aq-num">{row.scoreText}</strong><span>确定性评分</span><Tag tone={row.ready ? "ok" : "warn"}>{row.ready ? "依据已齐 · 可继续复核" : "证据待补 · 仅供观察"}</Tag><Tag>{statusLabel(row.status)}</Tag><Tag>{evidenceLabel(row.evidence)}</Tag></div>
                {row.context ? <p className="aq-card-summary">{row.context}</p> : null}
                {row.coverageNote ? <StatePanel tone="warn">{row.coverageNote}</StatePanel> : null}
                <div className="aq-research-columns">
                  <div className="aq-research-evidence"><p className="aq-label">为什么入选</p>{row.reasons.length ? <ul className="aq-reason-list">{row.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul> : <p className="aq-detail-muted">选股依据尚未记录。</p>}<p className="aq-detail-muted">命中策略：{row.strategies || "未记录"}</p></div>
                  <div className="aq-research-evidence"><p className="aq-label">下一观察与风险</p><p>{row.nextStep || "等待新增证据后复核。"}</p>{debate?.primary_risk_gate ? <StatePanel tone="warn">{debate.primary_risk_gate}</StatePanel> : <p className="aq-detail-muted">个股级风险结论尚未记录，不代表无风险。</p>}{debate?.next_trigger ? <p className="aq-detail-muted">下一验证：{debate.next_trigger}</p> : null}</div>
                </div>
                {row.metrics.length ? <dl className="aq-research-kv">{row.metrics.map((metric) => <div key={metric.key}><dt>{metric.label}</dt><dd>{metric.value}</dd></div>)}</dl> : null}
                {row.breakdown.length ? <details className="aq-drawer"><summary className="aq-drawer-summary">评分拆解与数据来源</summary><div className="aq-drawer-body"><ul className="aq-reason-list">{row.breakdown.map((item) => <li key={item}>{item}</li>)}</ul><p className="aq-detail-muted">{row.provenance || "来源未记录"}</p></div></details> : row.provenance ? <p className="aq-detail-muted">来源：{row.provenance}</p> : null}
              </>
            )}
          </>
        ) : null}
      </section>

      <CandidateObservationPanel symbol={code} researchDate={researchDate} historical={view?.isHistorical ?? false} />

      {view && row ? <section className="aq-detail-block">
        <SectionHeader title="同日消息与多空复核" description={`${view.date} 的个股证据，与下方实时行情分别呈现。`} count={`${datedMessages.length} 条消息`} />
        <div className="aq-research-columns">
          <div className="aq-research-evidence"><p className="aq-label">可引用消息</p>{datedMessages.length ? datedMessages.map((message, index) => <article className="aq-event-row" key={`${message.title}-${index}`}><a className="aq-radar-title" href={messageSourceUrl(message)} target="_blank" rel="noreferrer">{message.title}<ExternalLink aria-hidden="true" /></a><p className="aq-detail-muted">{message.source} · {message.published_at}</p>{message.summary ? <p>{message.summary}</p> : null}{message.validation_signals?.length ? <p className="aq-detail-muted">确认条件：{message.validation_signals.join("；")}</p> : null}{message.invalidation_signals?.length ? <p className="aq-detail-muted">失效条件：{message.invalidation_signals.join("；")}</p> : null}</article>) : <EmptyState title="暂无同日可引用消息" detail="缺少关联股票代码及原文链接的消息，不作为个股依据。" />}</div>
          <div className="aq-research-evidence"><p className="aq-label">多空结论</p>{debate ? <><p>{debate.conclusion || "尚未记录结论"}</p><p className="aq-detail-muted">{debateProcessText(debate) || "过程尚未记录"}</p>{debate.disagreement_points?.length ? <ul className="aq-reason-list">{debate.disagreement_points.map((point) => <li key={point}>分歧：{point}</li>)}</ul> : null}{debate.uncertainty_points?.length ? <ul className="aq-reason-list">{debate.uncertainty_points.map((point) => <li key={point}>待核实：{point}</li>)}</ul> : null}<p className="aq-detail-muted">讨论仅供研究，不覆盖确定性评分。</p></> : <EmptyState title="尚未形成同日多空复核" detail="保留现有确定性依据，等待个股支持与反证。" />}</div>
        </div>
      </section> : null}

      <SectionHeader title="实时行情" description="当前接口返回的行情快照，不作为历史日期的价格证据。" />

      <Card className="aq-research-quote">
        {loading ? <LoadingState label="正在读取行情与催化…" /> : null}
        {quoteError ? <StatePanel tone="warn" action={<button type="button" className="aq-btn" onClick={() => setReload((value) => value + 1)}>重试行情与事件</button>}>{quoteError}，行情降级；下方催化仍可看。</StatePanel> : null}
        {!loading && !currentQuote ? (
          <EmptyState title="未获取到行情" detail="该代码可能不是有效的 6 位 A 股代码，或后端行情源暂不可用。" />
        ) : null}
        {currentQuote ? (
          <div className="aq-research-quote-body">
            <div className="aq-title-row">
              <h2>{currentQuote.name || code}</h2>
              <strong>{code}</strong>
            </div>
            <p className="aq-detail-muted">
              现价 <b className="aq-num">{currentQuote.price.toFixed(2)}</b>{" "}
              <b className={cn("aq-num", changeClass(currentQuote.change_pct))}>{formatSignedPct(currentQuote.change_pct)}</b>
              {currentQuote.pe_ttm ? ` · PE ${currentQuote.pe_ttm.toFixed(1)}` : ""}
              {currentQuote.pb ? ` · PB ${currentQuote.pb.toFixed(2)}` : ""}
              {currentQuote.mcap_yi ? ` · 市值 ${currentQuote.mcap_yi.toFixed(0)} 亿` : ""}
              {` · 换手 ${currentQuote.turnover_pct.toFixed(2)}%`}
            </p>
          </div>
        ) : null}
      </Card>

      <section className="aq-detail-block">
        <SectionHeader
          number="02"
          title="相关新闻催化"
          description="当前催化事件流，独立于所选日期的研究快照，不评分。"
          count={events.length}
        />
        {catalystError ? <StatePanel tone="warn" action={<button type="button" className="aq-btn" onClick={() => setReload((value) => value + 1)}>重试事件</button>}>{catalystError}，催化模块降级。</StatePanel> : null}
        {!catalystError && !loading && events.length === 0 ? (
          <EmptyState title="暂无相关催化" detail="该股票未出现在当前催化事件流中。" />
        ) : null}
        {events.map((event, index) => (
          <CatalystRow key={index} event={event} />
        ))}
      </section>

      <section className="aq-detail-block">
        <SectionHeader
          number="03"
          title="事件面"
          description="当前事件缓存：解禁 / 龙虎榜 / 停复牌 / 业绩预告 / 分红。缺少记录不代表没有风险。"
          icon={CalendarClock}
          count={
            upcomingUnlocks.length +
            recentLonghubang.length +
            suspendResumes.length +
            recentEarnings.length +
            upcomingDividends.length
          }
        />
        {eventsError ? <StatePanel tone="warn" action={<button type="button" className="aq-btn" onClick={() => setReload((value) => value + 1)}>重试事件</button>}>{eventsError}，事件面降级。</StatePanel> : null}
        {!eventsError &&
        !loading &&
        upcomingUnlocks.length === 0 &&
        recentLonghubang.length === 0 &&
        suspendResumes.length === 0 &&
        recentEarnings.length === 0 &&
        upcomingDividends.length === 0 ? (
          <EmptyState title="暂无事件数据" detail="当前事件缓存尚未提供记录，缺少数据不代表没有风险事件。" />
        ) : null}
        {upcomingUnlocks.length > 0 ? (
          <>
            <p className="aq-eyebrow">解禁预警（{currentCalendar?.unlock_horizon_days ?? 30} 天内）</p>
            {upcomingUnlocks.map((event, index) => (
              <UnlockRow key={index} event={event} />
            ))}
          </>
        ) : null}
        {recentLonghubang.length > 0 ? (
          <>
            <p className="aq-eyebrow">近 {currentCalendar?.longhubang_lookback_days ?? 5} 日龙虎榜</p>
            {recentLonghubang.map((event, index) => (
              <LonghubangRow key={index} event={event} />
            ))}
          </>
        ) : null}
        {suspendResumes.length > 0 ? (
          <>
            <p className="aq-eyebrow">停复牌动态</p>
            {suspendResumes.map((event, index) => (
              <SuspendRow key={index} event={event} />
            ))}
          </>
        ) : null}
        {recentEarnings.length > 0 ? (
          <>
            <p className="aq-eyebrow">业绩预告</p>
            {recentEarnings.map((event, index) => (
              <EarningsRow key={index} event={event} />
            ))}
          </>
        ) : null}
        {upcomingDividends.length > 0 ? (
          <>
            <p className="aq-eyebrow">分红送转（即将除权除息）</p>
            {upcomingDividends.map((event, index) => (
              <DividendRow key={index} event={event} />
            ))}
          </>
        ) : null}
      </section>
    </div>
  );
}

function CatalystRow({ event }: { event: CatalystEvent }) {
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row">
        <Badge className={cn(impactToneClass(event.impact))}>{impactLabel(event.impact)}</Badge>
        <Tag>{event.category ?? "消息"}</Tag>
        <span className="aq-detail-muted">{event.published_at}</span>
      </div>
      <p className="aq-event-title">{event.title}</p>
      {event.url ? (
        <a className="aq-radar-title" href={event.url} target="_blank" rel="noreferrer">
          {event.source || "查看来源"}
          <ExternalLink aria-hidden="true" />
        </a>
      ) : (
        <span className="aq-detail-muted">{event.source}</span>
      )}
    </article>
  );
}

function UnlockRow({ event }: { event: UpcomingUnlockEvent }) {
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row">
        <Badge className={severityBadgeClass(event.severity)}>{severityLabel(event.severity)}</Badge>
        <span className="aq-detail-muted">
          {event.days_until} 天后（{event.event_date}）
        </span>
        <span className="aq-detail-muted">{unlockRatioText(event.ratio)}</span>
      </div>
      <p className="aq-event-title">
        {event.name || event.symbol} · {event.detail || "限售解禁"}
      </p>
    </article>
  );
}

function LonghubangRow({ event }: { event: RecentLonghubangEvent }) {
  const net = netAmountView(event.net_amount);
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row">
        <Tag>{event.event_date}</Tag>
        <span className="aq-detail-muted">{event.days_ago} 天前</span>
        <b className={cn("aq-num", net.cls)}>{net.text}</b>
      </div>
      {event.interpretation ? <p className="aq-detail-muted">{event.interpretation}</p> : null}
    </article>
  );
}

const SUSPEND_STATE_LABEL: Record<string, string> = {
  resuming_soon: "即将复牌",
  recently_resumed: "已复牌",
  suspended: "停牌中",
};

function SuspendRow({ event }: { event: SuspendResumeEvent }) {
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row">
        <Badge className="aq-badge-warn">{SUSPEND_STATE_LABEL[event.state] ?? "停复牌"}</Badge>
        <span className="aq-detail-muted">
          停牌 {event.suspend_date}
          {event.resume_date ? ` · 预计复牌 ${event.resume_date}` : ""}
        </span>
      </div>
      <p className="aq-event-title">{event.name || event.symbol}</p>
      <p className="aq-detail-muted">{event.detail}</p>
    </article>
  );
}

function EarningsRow({ event }: { event: EarningsForecastEvent }) {
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row">
        <Badge className="aq-badge-up">{event.forecast_type || "业绩预告"}</Badge>
        <span className="aq-detail-muted">
          {event.notice_date} 披露 · {event.days_ago} 天前
        </span>
      </div>
      <p className="aq-event-title">{event.name || event.symbol}</p>
      <p className="aq-detail-muted">{event.detail}</p>
    </article>
  );
}

function DividendRow({ event }: { event: DividendPlanEvent }) {
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row">
        <Tag>{event.ex_dividend_date} 除权除息</Tag>
        <span className="aq-detail-muted">{event.days_until} 天后</span>
        {event.progress ? <span className="aq-detail-muted">{event.progress}</span> : null}
      </div>
      <p className="aq-event-title">{event.name || event.symbol}</p>
      <p className="aq-detail-muted">{event.detail}</p>
    </article>
  );
}
