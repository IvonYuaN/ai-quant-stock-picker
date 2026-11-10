import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Badge, Card, EmptyState, ErrorState, LoadingState, SectionHeader, Tag } from "@/components/ui/primitives";
import { groupEventsBySector, impactLabel, impactToneClass, type CatalystEvent } from "@/lib/catalyst-client";
import { createCatalystResource, type CatalystResourceState } from "@/lib/catalyst-resource";
import { catalystReportMetadata, catalystSourceUrl } from "@/lib/catalyst-sector-view";
import { formatEvidenceTime } from "@/lib/format";

export function CatalystSectorSection() {
  const [state, setState] = useState<CatalystResourceState>({ data: null, loading: true, error: "" });
  const resource = useRef<ReturnType<typeof createCatalystResource> | null>(null);
  useEffect(() => {
    const active = createCatalystResource(setState);
    resource.current = active;
    void active.refresh();
    return () => { active.dispose(); resource.current = null; };
  }, []);
  const refresh = useCallback(() => { void resource.current?.refresh(); }, []);
  return <CatalystSectorView state={state} onRefresh={refresh} />;
}

/** Shared with the legacy page so its evidence cannot drift from the stream. */
export function CatalystSectorView({ state, onRefresh }: {
  state: CatalystResourceState;
  onRefresh: () => void;
}) {
  const { data, loading, error } = state;
  const groups = data ? groupEventsBySector(data.events) : [];
  const metadata = data ? catalystReportMetadata(data) : null;
  // 同一批催化事件里，「传导路径 / 传导假设 / 来源 / 发布时间」常常一字不差。
  // 逐条重复会把这一屏撑成几十份同样的长句 —— 全量相同的提到区头给一次，
  // 板块内相同的提到板块头，只有真正因事件而异的才留在事件行。
  // 同一批催化事件横跨多个板块，字段常常只在**全量**层面才看得出重复。
  // 统一用「首次出现」：某个值只在它第一次出现的那条里显示，后面不再重复。
  const seenPaths = new Set<string>();
  const seenHypotheses = new Set<string>();
  const seenSources = new Set<string>();
  const seenTimes = new Set<string>();
  const firstOf = (seen: Set<string>, value: string): boolean => {
    const key = (value || "").trim();
    if (!key) return true;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  };
  const renderedGroups = groups.map((group) => ({
    ...group,
    events: group.events.map((event) => ({
      event,
      showPath: firstOf(seenPaths, event.transmission_path.join(" → ")),
      showHypothesis: firstOf(seenHypotheses, event.transmission_hypothesis || ""),
      showSource: firstOf(seenSources, event.source || ""),
      showPublished: firstOf(seenTimes, event.published_at || ""),
    })),
  }));
  return (
    <section className="aq-section aq-catalyst-section" aria-label="板块催化与传导" aria-busy={loading}>
      <SectionHeader title="板块催化与传导" description="按受影响板块归集">
        <button type="button" className="aq-btn" onClick={onRefresh} disabled={loading}>
          <RefreshCw className="aq-inline-icon" aria-hidden="true" />
          刷新催化报告
        </button>
      </SectionHeader>
      {metadata && data ? (
        <div className="aq-tag-row aq-catalyst-metadata">
          <Tag tone={metadata.current ? "neutral" : "warn"}>{metadata.dateStatus}</Tag>
          <Tag tone={metadata.sourceOk ? "neutral" : "warn"}>{metadata.sourceLabel}</Tag>
          <span className="aq-note">报告日期 {metadata.reportDate}</span>
          <span className="aq-note" title={data.generated_at}>报告生成 {formatEvidenceTime(data.generated_at) || "未记录"}</span>
          {metadata.warningCount ? <span className="aq-note">报告记录 {metadata.warningCount} 项来源告警</span> : null}
        </div>
      ) : null}
      {loading ? <LoadingState label={data ? "正在刷新催化报告，保留上次内容…" : "正在读取催化报告…"} /> : null}
      {error ? <ErrorState
        error={data ? `${error}；仍显示上次成功读取的报告，日期与事件时间保持原记录。` : error}
        onRefresh={onRefresh}
      /> : null}
      {!loading && !error && data && !groups.length ? (
        <EmptyState title={data.source_status === "no_data" ? "催化报告尚未生成" : "暂无可聚合的板块催化"}
          detail={metadata?.sourceOk ? "当前报告没有催化事件。快讯与概念异动榜仍可单独查看。" : "当前缺少可用催化材料；不影响快讯与概念异动榜。"} />
      ) : null}
      {groups.length ? (
        <details className="aq-catalyst-evidence">
          <summary>{data?.events.length} 条催化 · {groups.length} 个板块 · 展开传导证据</summary>
          <p className="aq-note">多板块可能同时受影响，需核对原文。</p>
          <p className="aq-note">传导路径、假设、来源与发布时间在事件间重复时只显示第一次。</p>
          {renderedGroups.map((group) => (
            <Card key={group.sector} className="aq-concept-card">
              <header className="aq-concept-head"><h3>{group.sector}</h3><span className="aq-section-count">{group.events.length} 条</span></header>
              {group.events.map((row, index) => (
                <ConceptEvent
                  key={`${row.event.url || row.event.title}-${index}`}
                  event={row.event}
                  showPath={row.showPath}
                  showHypothesis={row.showHypothesis}
                  showSource={row.showSource}
                  showPublished={row.showPublished}
                />
              ))}
            </Card>
          ))}
        </details>
      ) : null}
    </section>
  );
}

function ConceptEvent({ event, showPath, showHypothesis, showSource, showPublished }: {
  event: CatalystEvent;
  showPath: boolean;
  showHypothesis: boolean;
  showSource: boolean;
  showPublished: boolean;
}) {
  const sourceUrl = catalystSourceUrl(event.url);
  const path = event.transmission_path.join(" → ");
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row"><Badge className={impactToneClass(event.impact)}>{impactLabel(event.impact)}</Badge><Tag>{event.category || "消息"}</Tag></div>
      <p className="aq-event-title">{event.title || "未记录标题"}</p>
      {showPublished ? <p className="aq-note" title={event.published_at}>事件发布时间：{formatEvidenceTime(event.published_at) || "未记录"}</p> : null}
      {showPath ? <p className="aq-transmission-path">传导路径：{path}</p> : null}
      {showHypothesis ? <p className="aq-detail-muted">传导假设：{event.transmission_hypothesis || "报告未提供"}</p> : null}
      {showSource ? (
        sourceUrl ? <a className="aq-radar-title" href={sourceUrl} target="_blank" rel="noopener noreferrer">{event.source || "查看原文来源"}<ExternalLink aria-hidden="true" /></a>
          : <span className="aq-detail-muted">来源：{event.source || "未记录"} · 原文链接未提供</span>
      ) : null}
    </article>
  );
}
