// 消息证据区块：可引用的消息、产业链传导，以及市场关联背景。
import { ArrowRight, CircleAlert, ExternalLink, MessageSquareText } from "lucide-react";
import { Card, EmptyState, SectionHeader, Tag } from "@/components/ui/primitives";
import { formatAqspTime } from "@/components/aqsp/useAqspSnapshot";
import { unique, type DailyView, type MessageRow, type SectionView } from "@/lib/daily-view";

function MessageCard({ row }: { row: MessageRow }) {
  const hasSummary = row.summary.trim() && row.summary.trim() !== row.title.trim();
  return (
    <Card accent="success" className="aq-message-card">
      <div className="aq-card-head">
        <div className="aq-tag-row">
          <Tag tone="primary">{row.category || "消息"}</Tag>
          {row.eventType ? <Tag>{row.eventType}</Tag> : null}
          {row.impact ? <Tag tone={row.impactTone}>{row.impact}</Tag> : null}
        </div>
        <time className="aq-time">{formatAqspTime(row.publishedAt)}</time>
      </div>

      <h3 className="aq-message-title">
        <MessageSquareText className="aq-inline-icon aq-tone-primary" aria-hidden="true" />
        {row.title || "消息标题未记录"}
      </h3>

      {hasSummary ? <p className="aq-card-summary">{row.summary}</p> : null}

      {row.sectors.length > 0 ? (
        <p className="aq-inline-field">
          <b>影响板块</b>
          {row.sectors.join(" · ")}
        </p>
      ) : null}

      {row.transmissionPath.length > 0 || row.transmissionHypothesis ? (
        <div className="aq-transmission">
          <b>产业链传导</b>
          {row.transmissionPath.length > 0 ? <p>{row.transmissionPath.join(" → ")}</p> : null}
          {row.transmissionHypothesis ? <span>{row.transmissionHypothesis}</span> : null}
        </div>
      ) : null}

      {row.validation.length > 0 ? (
        <p className="aq-inline-field">
          <b>确认</b>
          {row.validation.join("；")}
        </p>
      ) : null}

      {row.invalidation.length > 0 ? (
        <p className="aq-inline-field aq-tone-warn">
          <b>失效</b>
          {row.invalidation.join("；")}
        </p>
      ) : null}

      {row.sourceUrl ? (
        <a className="aq-source-link" href={row.sourceUrl} target="_blank" rel="noreferrer">
          <ExternalLink className="aq-inline-icon" aria-hidden="true" />
          查看来源{row.source ? ` · ${row.source}` : ""}
        </a>
      ) : (
        <p className="aq-source-missing">
          <CircleAlert className="aq-inline-icon" aria-hidden="true" />
          来源未记录，未纳入证据链
        </p>
      )}
    </Card>
  );
}

function MarketContextBlock({ view }: { view: DailyView }) {
  const context = view.marketContext;
  if (!context) return null;
  const lines = unique(context.summaryLines, 4);
  const links = context.crossMarket.slice(0, 4);
  if (!context.overview && lines.length === 0 && links.length === 0 && context.warnings.length === 0) return null;
  return (
    <div className="aq-context">
      <div className="aq-subhead">
        <h3>市场与产业链关联</h3>
        <span>{links.length} 条</span>
      </div>
      {context.overview ? <p className="aq-card-summary">{context.overview}</p> : null}
      {lines.length > 0 ? (
        <ul className="aq-bullet-list">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ) : null}
      {links.length > 0 ? (
        <div className="aq-link-list">
          {links.map((link) => (
            <div key={`${link.ruleId}-${link.sourceTitle}`}>
              <b>{link.theme || link.ruleId}</b>
              <span>{link.summary || link.action || "待验证"}</span>
            </div>
          ))}
        </div>
      ) : null}
      {context.warnings.length > 0 ? (
        <p className="aq-tone-warn aq-note">数据告警：{context.warnings.slice(0, 2).join("；")}</p>
      ) : null}
    </div>
  );
}

export function MessageSection({ view, section }: { view: DailyView; section: SectionView }) {
  return (
    <section id={section.id} className="aq-section">
      <SectionHeader
        number={section.number}
        title={section.label}
        description={section.description}
        count={section.count}
      />

      {view.sourceCoverage.length > 0 ? (
        <div className="aq-coverage">
          <strong>消息采集覆盖</strong>
          {view.sourceCoverage.map((line) => (
            <span key={line}>{line.replace(/^(来源覆盖|时效筛选|消息结果)[:：]\s*/, "")}</span>
          ))}
        </div>
      ) : null}

      {view.messages.length === 0 ? (
        <EmptyState
          title={section.empty?.title ?? "当天未形成可引用消息证据"}
          detail={
            <>
              <p>{section.empty?.detail}</p>
              {view.marketWarnings.length > 0 ? <p>来源检查：{view.marketWarnings.join("；")}</p> : null}
            </>
          }
        />
      ) : (
        <div className="aq-card-grid">
          {view.messages.map((row) => (
            <MessageCard key={row.key} row={row} />
          ))}
        </div>
      )}

      <MarketContextBlock view={view} />

      {view.crossMarket ? (
        <p className="aq-note">
          <ArrowRight className="aq-inline-icon" aria-hidden="true" />
          跨市主线见页首「{view.crossMarket.theme}」，本段只列个股级证据。
        </p>
      ) : null}
    </section>
  );
}
