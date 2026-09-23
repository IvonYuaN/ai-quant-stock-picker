// 个股研究页（/stock/:code）。
//
// 只读下钻：复用 StockDetailDrawer 的行情渲染（api.quote + changeClass + formatSignedPct），
// 叠加「相关新闻催化」（来自 /api/catalyst，按 affected_symbols 过滤）
// 与「事件面」（来自 /api/events：解禁预警 + 近 5 日龙虎榜，pit_cache 只读）。
// 不评分、不预测。
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { CalendarClock, ExternalLink } from "lucide-react";
import {
  api,
  type EventCalendarData,
  type Quote,
  type RecentLonghubangEvent,
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

  useEffect(() => {
    if (!code) return;
    let alive = true;
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
  }, [code]);

  if (!code) {
    return (
      <HeroState
        tone="neutral"
        title="未指定股票代码"
        detail="在地址栏输入 /stock/代码 打开个股研究，例如 /stock/600519。"
      />
    );
  }

  const events = catalyst ? filterEventsForSymbol(catalyst.events, code) : [];
  const upcomingUnlocks = eventCalendar ? sortUnlocksByDaysUntil(eventCalendar.upcoming_unlocks ?? []) : [];
  const recentLonghubang = eventCalendar?.recent_longhubang ?? [];

  return (
    <>
      <SectionHeader number="01" title={`个股研究 · ${code}`} description="行情快照 + 相关催化，只读、不评分。" />

      <Card className="aq-research-quote">
        {loading ? <LoadingState label="正在读取行情与催化…" /> : null}
        {quoteError ? <StatePanel tone="warn">{quoteError}，行情降级；下方催化仍可看。</StatePanel> : null}
        {!loading && !quote ? (
          <EmptyState title="未获取到行情" detail="该代码可能不是有效的 6 位 A 股代码，或后端行情源暂不可用。" />
        ) : null}
        {quote ? (
          <div className="aq-research-quote-body">
            <div className="aq-title-row">
              <h2>{quote.name || "读取中…"}</h2>
              <strong>{code}</strong>
            </div>
            <p className="aq-detail-muted">
              现价 <b className="aq-num">{quote.price.toFixed(2)}</b>{" "}
              <b className={cn("aq-num", changeClass(quote.change_pct))}>{formatSignedPct(quote.change_pct)}</b>
              {quote.pe_ttm ? ` · PE ${quote.pe_ttm.toFixed(1)}` : ""}
              {quote.pb ? ` · PB ${quote.pb.toFixed(2)}` : ""}
              {quote.mcap_yi ? ` · 市值 ${quote.mcap_yi.toFixed(0)} 亿` : ""}
              {` · 换手 ${quote.turnover_pct.toFixed(2)}%`}
            </p>
          </div>
        ) : null}
      </Card>

      <section className="aq-detail-block">
        <SectionHeader
          number="02"
          title="相关新闻催化"
          description="来自催化事件流，仅聚合展示，不评分。"
          count={events.length}
        />
        {catalystError ? <StatePanel tone="warn">{catalystError}，催化模块降级。</StatePanel> : null}
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
          description="解禁预警 + 近期龙虎榜（pit_cache 只读，缺数据 ≠ 没事件）。"
          icon={CalendarClock}
          count={upcomingUnlocks.length + recentLonghubang.length}
        />
        {eventsError ? <StatePanel tone="warn">{eventsError}，事件面降级。</StatePanel> : null}
        {!eventsError && !loading && upcomingUnlocks.length === 0 && recentLonghubang.length === 0 ? (
          <EmptyState title="暂无事件数据（pit_cache 未生成）" detail="由 scripts/preload_event_data.sh 预加载后可见。" />
        ) : null}
        {upcomingUnlocks.length > 0 ? (
          <>
            <p className="aq-eyebrow">解禁预警（{eventCalendar?.unlock_horizon_days ?? 30} 天内）</p>
            {upcomingUnlocks.map((event, index) => (
              <UnlockRow key={index} event={event} />
            ))}
          </>
        ) : null}
        {recentLonghubang.length > 0 ? (
          <>
            <p className="aq-eyebrow">近 {eventCalendar?.longhubang_lookback_days ?? 5} 日龙虎榜</p>
            {recentLonghubang.map((event, index) => (
              <LonghubangRow key={index} event={event} />
            ))}
          </>
        ) : null}
      </section>
    </>
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
