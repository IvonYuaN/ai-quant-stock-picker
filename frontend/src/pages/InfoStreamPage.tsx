// 小道信息流页（/stream）—— issue #317 W2b。
//
// 只读：/api/aqsp/info-stream（财联社快讯 + 概念异动榜）。
// 每条信息自带证据（来源、时间、重要度）；单源故障显式「缺材料」提示，
// 不静默渲染成正常空数据。
import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Zap } from "lucide-react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SectionHeader,
  StatePanel,
  Tag,
  ToneCallout,
} from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { changeClass, formatEvidenceTime, formatSignedPct, formatYi } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { InfoStreamData, InfoStreamNewsItem } from "@/types/aqsp";
import { CatalystSectorSection } from "@/components/aqsp/CatalystSectorSection";
import { PageOutline } from "@/components/ui/PageOutline";

function extractMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "读取失败";
}

/**
 * 快讯时间展示：优先取 ISO 串的 月-日 时:分；解析失败原样返回（证据不造假）。
 *
 * 聚合时间与单条时间用同一个格式化函数 —— 曾经是两份各自实现的正则，
 * 其中一份把「取数时刻」标成了「数据时间」（#317 W3 修正）。
 */
function newsTime(ctime: string): string {
  return formatEvidenceTime(ctime) || "时间未记录";
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
      {/*
        🔴 「数据时间」必须是最新一条快讯的发布时间，不是取数时刻（#317 W3）。
        源停更时取数时刻照样新鲜，用它当数据时间等于把「我刚拉过」说成「数据是新的」。
        取数时刻单独标注为「取数」，两者并列，读者自己判断数据有多旧。
      */}
      <span className="aq-hint">
        数据时间 {data.news_latest_ctime ? formatEvidenceTime(data.news_latest_ctime) : "未记录"}
      </span>
      <span className="aq-hint">取数 {formatEvidenceTime(data.generated_at)}</span>
    </div>
  );
}

export function InfoStreamLoadStatus({
  error,
  loading,
  hasData,
  onRefresh,
}: {
  error: string;
  loading: boolean;
  hasData: boolean;
  onRefresh: () => void;
}) {
  return (
    <>
      {error ? <ErrorState
        error={hasData ? `${error}；仍显示上次成功读取的数据。` : error}
        onRefresh={onRefresh}
      /> : null}
      {loading && !hasData ? <LoadingState label="正在读取信息流" /> : null}
    </>
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
  const requestSequence = useRef(0);

  const load = useCallback((force: boolean) => {
    const run = ++requestSequence.current;
    setLoading(true);
    api
      .infoStream(force)
      .then((value) => {
        if (requestSequence.current !== run) return;
        setData(value);
        setError("");
      })
      .catch((reason: unknown) => {
        if (requestSequence.current !== run) return;
        setError(extractMessage(reason));
      })
      .finally(() => {
        if (requestSequence.current === run) setLoading(false);
      });
    return () => {
      requestSequence.current += 1;
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
      <header className="aq-page-head">
        <div><p className="aq-eyebrow"><Zap aria-hidden="true" /> AQSP · 消息与线索</p><h1>消息与线索</h1><p className="aq-page-sub">先读有时间与来源的新闻事实，再看板块传导和异动；线索需要核验。</p></div>
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
      </header>

      <PageOutline items={[
        { id: "stream-news", label: "新闻事实" },
        { id: "stream-catalysts", label: "板块传导" },
        { id: "stream-concepts", label: "概念异动" },
      ]} />

      {data ? (
        <>
          <SourceHealthBar data={data} />
          {clsFailed ? (
            <ToneCallout tone="warn" title="缺材料：财联社快讯源故障" detail={data.sources.cls_news} />
          ) : null}
          {conceptFailed ? (
            <ToneCallout tone="warn" title="缺材料：概念板块源故障" detail={data.sources.concept_board} />
          ) : null}
        </>
      ) : null}

      <section id="stream-news" className="aq-section aq-module-group" aria-label="新闻事实">
        <SectionHeader number="01 · 新闻事实" title="发生了什么" description="财联社快讯 · 按发布时间倒序 · A/B 级为重要度标注" count={news.length} />
        <InfoStreamLoadStatus
          error={error}
          loading={loading}
          hasData={Boolean(data)}
          onRefresh={() => load(true)}
        />
        {data ? (
          <>
            {clsFailed || news.length === 0 ? (
              <EmptyState title="暂无快讯" detail={clsFailed ? data.sources.cls_news : "源正常但未返回快讯"} />
            ) : (
              <div className="aq-card-grid">
                {news.map((item) => (
                  <NewsItem key={item.item_id} item={item} />
                ))}
              </div>
            )}
          </>
        ) : null}
      </section>
      <section id="stream-catalysts" className="aq-module-group" aria-label="板块传导线索">
        <SectionHeader number="02 · 板块传导" title="消息可能影响哪些方向" description="报告中的传导逻辑用于寻找研究线索；核对原文后再判断。" />
        <CatalystSectorSection />
      </section>
      <section id="stream-concepts" className="aq-section aq-module-group" aria-label="概念异动线索">
        <SectionHeader
          number="03 · 概念异动"
          title="哪些概念出现异动"
          // 源没有日期字段，不能把取数时间冒充行情时间。
          description="涨跌家数与资金流仅作线索 · 数据时间未提供（源无日期字段）"
          count={data?.concepts.length}
        />
        {data ? (
          <>
            {conceptFailed || data.concepts.length === 0 ? (
              <EmptyState title="暂无概念异动数据" detail={conceptFailed ? data.sources.concept_board : "源正常但未返回板块"} />
            ) : (
              <details className="aq-workspace-fold">
                <summary>展开概念异动榜<span>已取得 {data.concepts.length} 个概念 · 源时间未提供</span></summary>
                <div className="aq-workspace-fold-body">
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
                </div>
              </details>
            )}
          </>
        ) : loading ? <LoadingState label="正在读取概念异动依据…" />
          : <StatePanel tone="warn">概念异动依据尚未取得；新闻与催化报告的读取状态分别核对。</StatePanel>}
      </section>
    </div>
  );
}
