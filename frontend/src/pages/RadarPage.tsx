// 资讯雷达 → 事件中枢。
//
// 现在 /radar 是两个并列视角：
//   - 事件中枢（catalyst）：后端聚合的新闻催化事件，带影响/板块标签与关联个股涨跌幅。
//   - RSS 资讯（radar）：原有 12 赛道公开 RSS 聚合，逻辑完全复用，不动。
// 两个 tab 各自独立拉取、独立降级；catalyst 取不到时走空态，绝不在页面抛错。
import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, Newspaper, RefreshCw, Rss, Zap } from "lucide-react";
import { Badge, EmptyState, LoadingState, SectionHeader, StatePanel, Tag } from "@/components/ui/primitives";
import { api, type Quote } from "@/lib/api";
import { normalizeRadar, type RadarItemView, type RadarView } from "@/lib/radar-view";
import { normalizeCatalyst, type CatalystEventView, type CatalystView } from "@/lib/catalyst-view";
import { changeClass, formatSignedPct } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/daily-view";

type Tab = "catalyst" | "radar";

function ItemRow({ item }: { item: RadarItemView }) {
  return (
    <li className="aq-radar-item">
      <div className="aq-radar-item-head">
        <span className="aq-num aq-radar-time">{item.time || "—"}</span>
        <Badge tone="neutral">{item.source || "未标注来源"}</Badge>
      </div>
      {item.url ? (
        <a className="aq-radar-title" href={item.url} target="_blank" rel="noreferrer">
          {item.title}
          <ExternalLink aria-hidden="true" />
        </a>
      ) : (
        <span className="aq-radar-title">{item.title}</span>
      )}
      {item.summary ? <p className="aq-radar-summary">{item.summary}</p> : null}
    </li>
  );
}

function impactTone(impact: string): Tone {
  if (impact === "positive") return "ok";
  if (impact === "negative") return "warn";
  return "neutral";
}

function EventCard({ event, quotes }: { event: CatalystEventView; quotes: Record<string, Quote> }) {
  return (
    <article className="aq-radar-card" key={event.key}>
      <div className="aq-radar-item-head">
        <Badge tone={impactTone(event.impact)}>{event.impactLabel}</Badge>
        {event.category ? <Tag>{event.category}</Tag> : null}
        <span className="aq-num aq-radar-time">{event.publishedAt || "—"}</span>
      </div>
      {event.url ? (
        <a className="aq-radar-title" href={event.url} target="_blank" rel="noreferrer">
          {event.title}
          <ExternalLink aria-hidden="true" />
        </a>
      ) : (
        <span className="aq-radar-title">{event.title}</span>
      )}
      {event.summary ? <p className="aq-radar-summary">{event.summary}</p> : null}
      {event.affectedSectors.length > 0 ? (
        <div className="aq-tag-row">
          {event.affectedSectors.map((sector) => (
            <Tag key={sector}>{sector}</Tag>
          ))}
        </div>
      ) : null}
      {event.affectedSymbols.length > 0 ? (
        <div className="aq-tag-row">
          <span className="aq-detail-muted">关联个股</span>
          {event.affectedSymbols.map((code) => {
            const quote = quotes[code];
            return (
              <span key={code} className={cn("aq-chip", quote && changeClass(quote.change_pct))}>
                {quote?.name || code}
                {quote ? ` ${formatSignedPct(quote.change_pct)}` : " —"}
              </span>
            );
          })}
        </div>
      ) : null}
    </article>
  );
}

export function RadarPage() {
  const [tab, setTab] = useState<Tab>("catalyst");

  /* ---- RSS 资讯（原 radar） ---- */
  const [radar, setRadar] = useState<RadarView | null>(null);
  const [radarError, setRadarError] = useState("");
  const [radarLoading, setRadarLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  /* ---- 事件中枢（catalyst） ---- */
  const [catalyst, setCatalyst] = useState<CatalystView | null>(null);
  const [catalystError, setCatalystError] = useState("");
  const [catalystLoading, setCatalystLoading] = useState(true);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [impactFilter, setImpactFilter] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string | null>(null);

  const loadRadar = useCallback(async () => {
    setRadarLoading(true);
    try {
      setRadar(normalizeRadar(await api.radar()));
      setRadarError("");
    } catch (err) {
      setRadarError(err instanceof Error ? err.message : "资讯雷达读取失败");
    } finally {
      setRadarLoading(false);
    }
  }, []);

  const loadCatalyst = useCallback(async () => {
    setCatalystLoading(true);
    try {
      setCatalyst(normalizeCatalyst(await api.catalyst()));
      setCatalystError("");
    } catch (err) {
      setCatalystError(err instanceof Error ? err.message : "新闻催化读取失败");
    } finally {
      setCatalystLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRadar();
    void loadCatalyst();
  }, [loadRadar, loadCatalyst]);

  async function forceRefresh() {
    setRefreshing(true);
    try {
      setRadar(normalizeRadar(await api.radarRefresh()));
      setRadarError("");
    } catch (err) {
      setRadarError(err instanceof Error ? err.message : "抓取失败");
    } finally {
      setRefreshing(false);
    }
  }

  /* 关联个股：跨所有事件去重后，行情只拉一次。 */
  const allSymbols = useMemo(() => {
    if (!catalyst) return [];
    const seen = new Set<string>();
    for (const event of catalyst.events) for (const code of event.affectedSymbols) seen.add(code);
    return [...seen];
  }, [catalyst]);

  useEffect(() => {
    if (allSymbols.length === 0) {
      setQuotes({});
      return;
    }
    let alive = true;
    api
      .quote(allSymbols.join(","))
      .then((data) => {
        if (alive) setQuotes(data);
      })
      .catch(() => {
        /* 行情失败只影响关联个股一列，事件本身仍展示 */
      });
    return () => {
      alive = false;
    };
  }, [allSymbols]);

  const filteredEvents = useMemo(() => {
    if (!catalyst) return [];
    return catalyst.events.filter(
      (event) =>
        (impactFilter == null || event.impact === impactFilter) &&
        (categoryFilter == null || event.category === categoryFilter),
    );
  }, [catalyst, impactFilter, categoryFilter]);

  const stats = radar?.stats;
  const totalItems = radar?.industries.reduce((sum, industry) => sum + industry.items.length, 0) ?? 0;

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Rss aria-hidden="true" />
            AQSP · 资讯与事件
          </p>
          <div className="aq-title-row">
            <h1>资讯雷达</h1>
            {tab === "catalyst" && catalyst?.generatedAt ? <strong>{catalyst.generatedAt}</strong> : null}
            {tab === "radar" && radar?.generatedAt ? <strong>{radar.generatedAt}</strong> : null}
          </div>
          <p className="aq-page-sub">
            {tab === "catalyst"
              ? catalyst
                ? `${catalyst.events.length} 条催化事件`
                : "新闻催化事件"
              : radar?.recentDays
                ? `近 ${radar.recentDays} 天 · 共 ${totalItems} 条`
                : "公开 RSS 聚合"}
          </p>
        </div>
        {tab === "radar" ? (
          <button
            type="button"
            className="aq-btn"
            onClick={() => void forceRefresh()}
            disabled={refreshing || radarLoading}
            title="强制重抓全部源，约 20-40 秒"
          >
            <RefreshCw className={cn((refreshing || radarLoading) && "aq-spin")} aria-hidden="true" />
            {refreshing ? "抓取中" : "抓取最新"}
          </button>
        ) : null}
      </header>

      <nav className="aq-tabs" aria-label="雷达分段">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "catalyst"}
          className={cn("aq-tab", tab === "catalyst" && "aq-tab-active")}
          onClick={() => setTab("catalyst")}
        >
          <span className="aq-tab-label">
            <Zap aria-hidden="true" /> 事件中枢
          </span>
          <span className="aq-tab-desc">新闻催化 · 关联个股</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "radar"}
          className={cn("aq-tab", tab === "radar" && "aq-tab-active")}
          onClick={() => setTab("radar")}
        >
          <span className="aq-tab-label">RSS 资讯</span>
          <span className="aq-tab-desc">公开 RSS 聚合</span>
        </button>
      </nav>

      {stats && stats.failedSources > 0 ? (
        <Badge tone="warn">部分源不可用（{stats.failedSources} 个本次抓取失败）</Badge>
      ) : null}

      {radar && !radar.hasContent ? (
        radar.generatedAt ? (
          <EmptyState
            title="近期无新资讯"
            detail={`雷达已于 ${radar.generatedAt} 抓取，但近 ${radar.recentDays || "?"} 天无新条目。可点「抓取最新」强制重抓。`}
            icon={Newspaper}
          />
        ) : (
          <EmptyState
            title="雷达数据尚未生成"
            detail="后台定时抓取任务会定期拉取 RSS；若长时间为空，可点「抓取最新」手动触发。"
            icon={Newspaper}
          />
        )
      ) : null}

      {tab === "catalyst" ? (
        <CatalystPanel
          view={catalyst}
          loading={catalystLoading}
          error={catalystError}
          quotes={quotes}
          events={filteredEvents}
          impactFilter={impactFilter}
          categoryFilter={categoryFilter}
          onImpact={(value) => setImpactFilter((prev) => (prev === value ? null : value))}
          onCategory={(value) => setCategoryFilter((prev) => (prev === value ? null : value))}
        />
      ) : (
        <RadarPanel
          view={radar}
          loading={radarLoading}
          error={radarError}
          refreshing={refreshing}
          stats={stats}
        />
      )}
    </div>
  );
}

function CatalystPanel({
  view,
  loading,
  error,
  quotes,
  events,
  impactFilter,
  categoryFilter,
  onImpact,
  onCategory,
}: {
  view: CatalystView | null;
  loading: boolean;
  error: string;
  quotes: Record<string, Quote>;
  events: CatalystEventView[];
  impactFilter: string | null;
  categoryFilter: string | null;
  onImpact: (value: string) => void;
  onCategory: (value: string) => void;
}) {
  if (error) return <StatePanel tone="warn">{error}</StatePanel>;
  if (loading && !view) return <LoadingState label="正在读取新闻催化…" />;
  if (!view) return <EmptyState title="新闻催化尚未生成" icon={Newspaper} />;

  const hasNoData = view.events.length === 0 || view.sourceStatus === "no_data";
  if (hasNoData) {
    return (
      <EmptyState
        title="新闻催化尚未生成"
        detail="后端暂未产出催化事件（source_status 为 no_data），稍后再来看。"
        icon={Newspaper}
      />
    );
  }

  return (
    <>
      <FacetBar
        impacts={view.facets.impacts}
        categories={view.facets.categories}
        impactFilter={impactFilter}
        categoryFilter={categoryFilter}
        onImpact={onImpact}
        onCategory={onCategory}
      />
      <div className="aq-radar-grid">
        {events.length === 0 ? (
          <EmptyState title="没有符合筛选条件的事件" detail="试着取消上方的影响或板块筛选。" icon={Newspaper} />
        ) : (
          events.map((event) => <EventCard key={event.key} event={event} quotes={quotes} />)
        )}
      </div>
    </>
  );
}

function FacetBar({
  impacts,
  categories,
  impactFilter,
  categoryFilter,
  onImpact,
  onCategory,
}: {
  impacts: { value: string; label: string }[];
  categories: string[];
  impactFilter: string | null;
  categoryFilter: string | null;
  onImpact: (value: string) => void;
  onCategory: (value: string) => void;
}) {
  if (impacts.length === 0 && categories.length === 0) return null;
  return (
    <div className="aq-tag-row">
      {impacts.map((facet) => (
        <button
          type="button"
          key={facet.value}
          className={cn("aq-chip", impactFilter === facet.value && "aq-tag-primary")}
          onClick={() => onImpact(facet.value)}
        >
          {facet.label}
        </button>
      ))}
      {categories.map((category) => (
        <button
          type="button"
          key={category}
          className={cn("aq-chip", categoryFilter === category && "aq-tag-primary")}
          onClick={() => onCategory(category)}
        >
          {category}
        </button>
      ))}
    </div>
  );
}

function RadarPanel({
  view,
  loading,
  error,
  refreshing,
  stats,
}: {
  view: RadarView | null;
  loading: boolean;
  error: string;
  refreshing: boolean;
  stats: { industries: number; totalSources: number; failedSources: number } | undefined;
}) {
  if (refreshing) return <StatePanel>正在重抓全部 RSS 源，约需 20-40 秒，请稍候…</StatePanel>;
  if (error) return <StatePanel tone="warn">{error}</StatePanel>;
  if (loading && !view) return <LoadingState label="正在读取资讯雷达…" />;

  if (stats && stats.failedSources > 0) {
    return <Badge tone="warn">{stats.failedSources} 个源本次抓取失败</Badge>;
  }
  if (view && !view.hasContent) {
    return (
      <EmptyState
        title="还没有抓取过资讯"
        detail="首次使用需要点「抓取最新」拉取全部 RSS 源（约 20-40 秒），之后会读缓存。"
        icon={Newspaper}
      />
    );
  }
  if (!view || !view.hasContent) return null;

  return (
    <div className="aq-radar-grid">
      {view.industries.map((industry) => (
        <section className="aq-radar-card" key={industry.key || industry.name}>
          <SectionHeader
            title={industry.name}
            description={industry.total ? `${industry.total} 个源` : undefined}
            count={`${industry.items.length} 条`}
          />
          {industry.items.length === 0 ? (
            <p className="aq-detail-muted">该赛道近期无新条目。</p>
          ) : (
            <ul className="aq-radar-list">
              {industry.items.slice(0, 8).map((item) => (
                <ItemRow key={item.key} item={item} />
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}
