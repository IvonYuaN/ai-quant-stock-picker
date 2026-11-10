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

/**
 * 快讯正文展示口径：同一句话只出现一次，长正文给出视觉落点。
 *
 * 财联社电报的 summary 常以「【标题】」开头，把卡片标题原样再念一遍；
 * 正文自带的 \n 分段在 <p> 里又会被浏览器折叠掉，几十条 160+ 字的整块
 * 文字就糊成一屏。这里只做两件事：
 *   1. 去掉与标题**完全一致**的【…】前缀（不一致时原样保留，不猜、不改写内容）
 *   2. 保留数据自带分段；过长的单段按句末标点切成约 90 字的句群
 * 全程不截断、不折叠、不改写任何数字 —— 证据一个字都不能少。
 */
export function stripTitleEcho(summary: string, title: string): string {
  const head = title.trim();
  if (!head) return summary;
  const prefix = `【${head}】`;
  return summary.startsWith(prefix) ? summary.slice(prefix.length) : summary;
}

function splitLongParagraph(paragraph: string, target = 55): string[] {
  if (paragraph.length <= target) return [paragraph];
  // 按句末标点切、保留标点本身；没有句读时不硬切，交给浏览器折行。
  const sentences = paragraph.split(/(?<=[。！？；])/).filter(Boolean);
  if (sentences.length < 2) return [paragraph];
  const groups: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    if (current && current.length + sentence.length > target) {
      groups.push(current);
      current = sentence;
    } else {
      current += sentence;
    }
  }
  if (current) groups.push(current);
  return groups;
}

export function newsBody(summary: string | undefined, title: string | undefined): string[] {
  const text = stripTitleEcho((summary || "").trim(), title || "");
  if (!text) return [];
  return text
    .split(/\n+/)
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => splitLongParagraph(line));
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

export function NewsItem({ item, time, showSource }: { item: InfoStreamNewsItem; time: string; showSource: boolean }) {
  const body = newsBody(item.summary, item.title);
  return (
    <div className="aq-card aq-news-card">
      <div className="aq-card-head">
        <div className="aq-card-title">
          <h3>{item.title || "（无标题）"}</h3>
          {time ? <span className="aq-code">{time}</span> : null}
        </div>
        <Tag tone={item.level === "A" ? "primary" : item.level === "B" ? "ok" : "neutral"}>
          {item.level || "?"} 级
        </Tag>
      </div>
      {body.length ? (
        <div className="aq-news-body">
          {body.map((paragraph, index) => (
            <p key={index} className="aq-news-paragraph">{paragraph}</p>
          ))}
        </div>
      ) : null}
      <div className="aq-tag-row">
        {showSource ? (
          <Tag tone="primary" title={`来源：${item.source} · id ${item.item_id}`}>
            {item.source}
          </Tag>
        ) : null}
        {item.subjects.map((subject) => (
          <Tag key={subject}>{subject}</Tag>
        ))}
      </div>
    </div>
  );
}

/** 全部取值相同且不止一条时返回该值（供上提），否则返回 null。 */
function sharedValue(values: readonly (string | undefined)[]): string | null {
  const filled = values.map((value) => (value || "").trim()).filter(Boolean);
  const uniq = [...new Set(filled)];
  return uniq.length === 1 && filled.length > 1 ? uniq[0] : null;
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
  // 一屏 20~30 条快讯里，来源常常只有一两个、同一时刻会连发好几条。
  // 逐条重复"来源 + 时间"就是几十份同样的字 —— 来源上提到列表头，
  // 同一时刻只在第一条出现。
  const sharedNewsSource = sharedValue(news.map((item) => item.source));
  const seenNewsTimes = new Set<string>();
  const newsRows = news.map((item) => {
    const time = newsTime(item.ctime);
    const firstTime = time && !seenNewsTimes.has(time);
    if (time) seenNewsTimes.add(time);
    return { item, time: firstTime ? time : "" };
  });
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
              <>
                {sharedNewsSource ? (
                  <p className="aq-note">本批快讯来源相同：{sharedNewsSource}</p>
                ) : null}
                <div className="aq-card-grid aq-news-grid">
                  {newsRows.map((row) => (
                    <NewsItem
                      key={row.item.item_id}
                      item={row.item}
                      time={row.time}
                      showSource={!sharedNewsSource}
                    />
                  ))}
                </div>
              </>
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
          </>
        ) : loading ? <LoadingState label="正在读取概念异动依据…" />
          : <StatePanel tone="warn">概念异动依据尚未取得；新闻与催化报告的读取状态分别核对。</StatePanel>}
      </section>
    </div>
  );
}
