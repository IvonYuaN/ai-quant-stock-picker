// 资讯雷达：12 个赛道的公开 RSS 资讯聚合。
//
// 后端有两个状态必须分开表达：
//   - **骨架**（还没抓过）：赛道列表在，但 items 全空 → 提示点「抓取」。
//   - **真空**（抓过了但近期没有新资讯）→ 才是"没有资讯"。
// 两者混成一句"暂无数据"，用户会以为功能坏了。
import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Newspaper, RefreshCw, Rss } from "lucide-react";
import { Badge, EmptyState, LoadingState, SectionHeader, StatePanel } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { normalizeRadar, type RadarItemView, type RadarView } from "@/lib/radar-view";
import { cn } from "@/lib/utils";

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

export function RadarPage() {
  const [radar, setRadar] = useState<RadarView | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRadar(normalizeRadar(await api.radar()));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "资讯雷达读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function forceRefresh() {
    setRefreshing(true);
    try {
      setRadar(normalizeRadar(await api.radarRefresh()));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "抓取失败");
    } finally {
      setRefreshing(false);
    }
  }

  const stats = radar?.stats;
  const totalItems = radar?.industries.reduce((sum, industry) => sum + industry.items.length, 0) ?? 0;

  return (
    <div className="aq-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Rss aria-hidden="true" />
            AQSP · 资讯雷达
          </p>
          <div className="aq-title-row">
            <h1>资讯雷达</h1>
            {radar?.generatedAt ? <strong>{radar.generatedAt}</strong> : null}
          </div>
          <p className="aq-page-sub">
            {radar?.recentDays ? `近 ${radar.recentDays} 天` : "公开 RSS 聚合"} · 共 {totalItems} 条
            {stats ? ` · ${stats.industries} 赛道 / ${stats.totalSources} 源` : ""}
          </p>
        </div>
        <button
          type="button"
          className="aq-btn"
          onClick={() => void forceRefresh()}
          disabled={refreshing || loading}
          title="强制重抓全部源，约 20-40 秒"
        >
          <RefreshCw className={cn((refreshing || loading) && "aq-spin")} aria-hidden="true" />
          {refreshing ? "抓取中" : "抓取最新"}
        </button>
      </header>

      {refreshing ? <StatePanel>正在重抓全部 RSS 源，约需 20-40 秒，请稍候…</StatePanel> : null}
      {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
      {loading && !radar ? <LoadingState label="正在读取资讯雷达…" /> : null}

      {stats && stats.failedSources > 0 ? (
        <Badge tone="warn">{stats.failedSources} 个源本次抓取失败</Badge>
      ) : null}

      {radar && !radar.hasContent ? (
        <EmptyState
          title="还没有抓取过资讯"
          detail="首次使用需要点「抓取最新」拉取全部 RSS 源（约 20-40 秒），之后会读缓存。"
          icon={Newspaper}
        />
      ) : null}

      {radar && radar.hasContent ? (
        <div className="aq-radar-grid">
          {radar.industries.map((industry) => (
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
      ) : null}
    </div>
  );
}
