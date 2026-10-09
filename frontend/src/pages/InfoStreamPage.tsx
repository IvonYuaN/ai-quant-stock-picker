// 小道信息流页（/stream）—— issue #317 W2b。
//
// 只读：/api/aqsp/info-stream（财联社快讯 + 概念异动榜）。
// 每条信息自带证据（来源、时间、重要度）；单源故障显式「缺材料」提示，
// 不静默渲染成正常空数据。
import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Zap } from "lucide-react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SectionHeader,
  Tag,
  ToneCallout,
} from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { changeClass, formatSignedPct, formatYi } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { InfoStreamData, InfoStreamNewsItem } from "@/types/aqsp";

function extractMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "读取失败";
}

/** 快讯时间展示：优先取 ISO 串的 月-日 时:分；解析失败原样返回（证据不造假）。 */
function newsTime(ctime: string): string {
  if (!ctime) return "时间未记录";
  const match = ctime.match(/(\d{2}-\d{2})T(\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}` : ctime;
}

function SourceHealthBar({ data }: { data: InfoStreamData }) {
  const entries: ReadonlyArray<readonly [string, string, string]> = [
    ["cls_news", "财联社快讯", "cls_news"],
    ["concept_board", "概念板块", "concept_board"],
  ];
  return (
    <div className="aq-tag-row">
      {entries.map(([key, label]) => {
        const status = data.sources[key as keyof typeof data.sources];
        const ok = status === "ok";
        return (
          <Tag key={key} tone={ok ? "ok" : "warn"} title={status}>
            {label}：{ok ? "正常" : "缺材料"}
          </Tag>
        );
      })}
      <span className="aq-hint">数据时间 {data.generated_at.slice(11, 16)}</span>
    </div>
  );
}

function NewsItem({ item }: { item: InfoStreamNewsItem }) {
  return (
    <div className="aq-card">
      <div className="aq-card-head">
        <div className="aq-card-title">
          <h3>{item.title || "（无标题）"}</h3>
          <span className="aq-code">{newsTime(item.ctime)}</span>
        </div>
        <Tag tone={item.level === "A" ? "primary" : item.level === "B" ? "ok" : "neutral"}>
          {item.level || "?"} 级
        </Tag>
      </div>
      {item.summary ? <p className="aq-card-summary">{item.summary}</p> : null}
      <div className="aq-tag-row">
        <Tag tone="primary" title={`来源：${item.source} · id ${item.item_id}`}>
          {item.source}
        </Tag>
        {item.subjects.map((subject) => (
          <Tag key={subject}>{subject}</Tag>
        ))}
      </div>
    </div>
  );
}

export function InfoStreamPage() {
  const [data, setData] = useState<InfoStreamData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback((force: boolean) => {
    let alive = true;
    setLoading(true);
    api
      .infoStream(force)
      .then((value) => {
        if (!alive) return;
        setData(value);
        setError("");
      })
      .catch((reason: unknown) => {
        if (!alive) return;
        setError(extractMessage(reason));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => load(false), [load]);

  // 快讯按时间倒序（后端返回顺序不保证；ctime 解析失败的原样沉底）
  const news = data
    ? [...data.news].sort((a, b) => (a.ctime < b.ctime ? 1 : a.ctime > b.ctime ? -1 : 0))
    : [];
  const clsFailed = data ? data.sources.cls_news.startsWith("error") : false;
  const conceptFailed = data ? data.sources.concept_board.startsWith("error") : false;

  return (
    <div className="aq-page">
      <SectionHeader
        icon={Zap}
        title="小道信息"
        description="财联社快讯 · 概念异动榜 —— 每条自带来源与时间证据"
      >
        <button
          type="button"
          className="aq-btn"
          onClick={() => load(true)}
          disabled={loading}
          title="跳过缓存强制刷新（5 分钟 TTL）"
        >
          <RefreshCw className="aq-inline-icon" aria-hidden="true" />
          强制刷新
        </button>
      </SectionHeader>

      {error ? <ErrorState error={error} onRefresh={() => load(true)} /> : null}
      {loading && !data ? <LoadingState label="正在读取信息流" /> : null}

      {data ? (
        <>
          <SourceHealthBar data={data} />
          {clsFailed ? (
            <ToneCallout tone="warn" title="缺材料：财联社快讯源故障" detail={data.sources.cls_news} />
          ) : null}
          {conceptFailed ? (
            <ToneCallout tone="warn" title="缺材料：概念板块源故障" detail={data.sources.concept_board} />
          ) : null}

          <section className="aq-section">
            <SectionHeader title="概念异动榜" description="当日涨幅榜 · 涨跌家数 · 主力净流入" count={data.concepts.length} />
            {conceptFailed || data.concepts.length === 0 ? (
              <EmptyState title="暂无概念异动数据" detail={conceptFailed ? data.sources.concept_board : "源正常但未返回板块"} />
            ) : (
              <div className="aq-table-wrap">
                <table className="aq-table">
                  <thead>
                    <tr>
                      <th>板块</th>
                      <th>涨跌幅</th>
                      <th>涨/跌家数</th>
                      <th>主力净流入</th>
                      <th>净占比</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.concepts.map((concept) => (
                      <tr key={concept.board_code}>
                        <td>
                          <strong>{concept.board_name}</strong>
                        </td>
                        <td className={cn("aq-num", changeClass(concept.change_pct))}>
                          {formatSignedPct(concept.change_pct)}
                        </td>
                        <td className="aq-num">
                          <span className="aq-tone-up">{concept.up_count}</span> /{" "}
                          <span className="aq-tone-down">{concept.down_count}</span>
                        </td>
                        <td className={cn("aq-num", concept.main_net_inflow >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                          {formatYi(concept.main_net_inflow)}
                        </td>
                        <td className={cn("aq-num", concept.main_net_ratio >= 0 ? "aq-tone-up" : "aq-tone-down")}>
                          {formatSignedPct(concept.main_net_ratio)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="aq-section">
            <SectionHeader title="财联社快讯" description="按发布时间倒序 · A/B 级为重要度标注" count={news.length} />
            {clsFailed || news.length === 0 ? (
              <EmptyState title="暂无快讯" detail={clsFailed ? data.sources.cls_news : "源正常但未返回快讯"} />
            ) : (
              <div className="aq-card-grid">
                {news.map((item) => (
                  <NewsItem key={item.item_id} item={item} />
                ))}
              </div>
            )}
          </section>
        </>
      ) : null}
    </div>
  );
}
