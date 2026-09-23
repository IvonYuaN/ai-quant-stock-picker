// 题材 / 概念传导页（/concepts）。
//
// 只读：把 /api/catalyst 的事件按 affected_sectors 聚合，呈现每条事件的传导路径与假设。
// 直接派生自现有 CatalystReport，不引入新数据、不做评分。
import { useEffect, useState } from "react";
import { ExternalLink } from "lucide-react";
import {
  fetchCatalyst,
  groupEventsBySector,
  impactLabel,
  impactToneClass,
  type CatalystData,
  type CatalystEvent,
} from "@/lib/catalyst-client";
import { Badge, Card, EmptyState, ErrorState, LoadingState, SectionHeader, Tag } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";

function extractMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "读取失败";
}

export function ConceptsPage() {
  const [data, setData] = useState<CatalystData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchCatalyst()
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

  const groups = data ? groupEventsBySector(data.events) : [];

  return (
    <>
      <SectionHeader
        title="题材 / 概念传导"
        description="按受影响板块聚合催化事件，呈现传导路径与假设。派生自现有催化报告，只读。"
        count={groups.length}
      />
      {loading ? <LoadingState label="正在读取催化事件流…" /> : null}
      {error ? <ErrorState error={error} onRefresh={() => window.location.reload()} /> : null}
      {!loading && !error && groups.length === 0 ? (
        <EmptyState
          title="暂无可聚合的题材"
          detail="催化事件流为空，或后端尚未生成催化报告（请先运行 news-catalysts）。"
        />
      ) : null}
      {groups.map((group) => (
        <Card key={group.sector} className="aq-concept-card">
          <header className="aq-concept-head">
            <h3>{group.sector}</h3>
            <span className="aq-section-count">{group.events.length} 条</span>
          </header>
          {group.events.map((event, index) => (
            <ConceptEvent key={index} event={event} />
          ))}
        </Card>
      ))}
    </>
  );
}

function ConceptEvent({ event }: { event: CatalystEvent }) {
  return (
    <article className="aq-event-row">
      <div className="aq-tag-row">
        <Badge className={cn(impactToneClass(event.impact))}>{impactLabel(event.impact)}</Badge>
        <Tag>{event.category ?? "消息"}</Tag>
      </div>
      <p className="aq-event-title">{event.title}</p>
      {event.transmission_path.length > 0 ? (
        <p className="aq-transmission-path">{event.transmission_path.join(" → ")}</p>
      ) : null}
      {event.transmission_hypothesis ? <p className="aq-detail-muted">{event.transmission_hypothesis}</p> : null}
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
