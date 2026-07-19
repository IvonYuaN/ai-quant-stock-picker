import {
  AlertCircle,
  ArrowRight,
  Bot,
  CalendarDays,
  Check,
  CircleAlert,
  Clock3,
  ExternalLink,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  UsersRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { RESEARCH_SECTION_IDS, TEST_VARIANTS_SECTION_ID } from "@/lib/research-layout";
import type { AqspAgentResult, AqspCandidate, AqspMessage, AqspSnapshot } from "@/lib/api";
import {
  debateProcessText,
  dedupeResearchText,
  formatResearchDate,
  isCurrentEmptyObservation,
  latestReviewDate,
  messageSourceUrl,
  sameResearchText,
  snapshotConclusion,
} from "@/lib/research-view";
import {
  formatAqspTime,
  isAqspSnapshotStale,
  useWorkspaceSnapshot,
} from "./useAqspSnapshot";

function uniqueNonEmpty(values: readonly string[] | undefined, limit = 4): string[] {
  return Array.from(new Set((values ?? []).map((value) => value.trim()).filter(Boolean))).slice(0, limit);
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="vr-empty-state">
      <CircleAlert className="h-4 w-4 shrink-0" />
      <div>
        <p className="font-medium text-foreground/85">{title}</p>
        <p className="mt-1 leading-relaxed">{detail}</p>
      </div>
    </div>
  );
}

function SnapshotMeta({ snapshot }: { snapshot: AqspSnapshot }) {
  const stale = isAqspSnapshotStale(snapshot);
  const componentFreshness = snapshot.meta?.freshness;
  const contextLines = snapshot.market_context?.summary_lines ?? [];
  const crossMarketStale = contextLines.some((line) => /实时跨市:\s*stale/i.test(line));
  const newsUnavailable = /失败|timeout|超时/i.test(snapshot.market_context?.status ?? "") || snapshot.message_status === "来源失败";
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      <span>{snapshot.selected_date || "日期未记录"}</span>
      <span>更新 {formatAqspTime(snapshot.generated_at)}</span>
      <span className={cn("vr-status", stale ? "vr-status-warning" : "vr-status-success")}>
        {stale ? "历史数据" : "数据有效"}
      </span>
      {newsUnavailable && <span className="vr-status vr-status-warning">消息源受限</span>}
      {crossMarketStale && <span className="vr-status vr-status-warning">跨市场数据滞后</span>}
      {componentFreshness?.candidates === "fresh" && <span className="vr-status vr-status-success">行情实时</span>}
    </div>
  );
}

function FreshnessNotice({ snapshot }: { snapshot: AqspSnapshot }) {
  if (!isAqspSnapshotStale(snapshot)) return null;
  return (
    <div className="vr-freshness">
      <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
      <span>这是历史日期的数据，内容仅用于观察与复核。</span>
    </div>
  );
}

function RecommendationGateNotice({ snapshot }: { snapshot: AqspSnapshot }) {
  const gate = snapshot.recommendation_gate;
  if (!gate || gate.recommendation_allowed) {
    return (
      <div className="vr-gate-notice vr-gate-notice-ready">
        <Check className="h-4 w-4 shrink-0" />
        <span>全局研究 gate 已通过，可进入纸面复核。</span>
      </div>
    );
  }
  const reasons = gate.reasons.slice(0, 3).map((reason) => {
    if (reason.startsWith("coldstart_below_minimum:")) return "冷启动样本不足";
    if (reason.startsWith("paper_tracking_below_minimum:")) return "纸面跟踪样本不足";
    if (reason === "walkforward_not_ok") return "Walk-forward 双门未通过";
    if (reason === "walkforward_updated_at_missing") return "Walk-forward 更新时间缺失";
    if (reason.startsWith("walkforward_stale:")) return "Walk-forward 证据已过期";
    if (reason.startsWith("circuit_breaker_active_until:")) {
      const parts = reason.split(":");
      return `组合保护冷却至 ${parts[parts.length - 1]}`;
    }
    if (reason.startsWith("freshness_not_ready:")) return "实时行情或消息源未达到新鲜度要求";
    return reason;
  });
  return (
    <div className="vr-gate-notice vr-gate-notice-blocked">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <strong>当前仅观察，未达到可推荐条件</strong>
        <span className="mt-1 block text-[11px]">{reasons.join("；") || "推荐 gate 未通过"}</span>
      </div>
    </div>
  );
}

function CurrentEmptyObservationNotice({ snapshot }: { snapshot: AqspSnapshot }) {
  const { selectDate } = useWorkspaceSnapshot();
  if (!isCurrentEmptyObservation(snapshot)) return null;
  const recentDate = latestReviewDate(snapshot);
  const recentLabel = recentDate ? formatResearchDate(recentDate) : null;
  return (
    <div className="vr-empty-observation" role="status">
      <CalendarDays className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <strong>今日无交易任务，当前仅观察</strong>
        <p className="mt-1">当前日期没有候选或消息；推荐 gate 已阻塞，系统没有把历史数据当作今日推荐。</p>
        {recentLabel && (
          <button type="button" onClick={() => selectDate(recentDate)} className="mt-1.5 text-left underline underline-offset-2 hover:text-foreground">
            最近可回看：{recentLabel.day}（{recentLabel.weekday}）
          </button>
        )}
      </div>
    </div>
  );
}

function DateStrip({ snapshot }: { snapshot: AqspSnapshot }) {
  const { loading, selectedDate, selectDate } = useWorkspaceSnapshot();
  const dates = snapshot.available_dates;
  if (dates.length === 0) return null;
  const activeDate = selectedDate || snapshot.selected_date;
  return (
    <div className="vr-date-strip" aria-label="研究日期">
      <div className="flex min-w-0 items-center gap-2 text-[11px] font-semibold text-muted-foreground">
        <CalendarDays className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span>日期</span>
      </div>
      <div className="vr-date-strip-list">
        {dates.map((date) => {
          const label = formatResearchDate(date);
          const active = date === activeDate;
          return (
            <button
              key={date}
              type="button"
              onClick={() => selectDate(date)}
              className={cn("vr-date-pill", active && "vr-date-pill-active")}
              aria-pressed={active}
              disabled={loading && active}
            >
              <span className="font-mono">{label.day}</span>
              <span>{label.weekday}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CandidateCard({ candidate }: { candidate: AqspCandidate }) {
  return (
    <article className="vr-research-card group">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{candidate.display_name || candidate.symbol}</p>
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">{candidate.symbol || "代码未记录"}</p>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-mono text-xl font-bold tabular-nums text-primary">
            {Number.isFinite(candidate.score) ? candidate.score.toFixed(1) : "—"}
          </p>
          <p className="text-[10px] text-muted-foreground">评分</p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="vr-chip vr-chip-primary">{candidate.research_status || "状态未记录"}</span>
        <span className="vr-chip">{candidate.evidence_status || "证据状态未记录"}</span>
      </div>
      {(candidate.data_source || candidate.freshness || candidate.data_fetched_at || candidate.data_timestamp_source) && (
        <div className="mt-3 border-t border-border/50 pt-2 text-[10px] leading-relaxed text-muted-foreground" aria-label="候选数据 provenance">
          {candidate.data_source && <span>源：{candidate.data_source}</span>}
          {candidate.freshness && <span>{candidate.data_source ? " · " : ""}新鲜度：{candidate.freshness}</span>}
          {candidate.data_fetched_at && <span> · 抓取：{formatAqspTime(candidate.data_fetched_at)}</span>}
          {candidate.data_timestamp_source && <span> · 时间依据：{candidate.data_timestamp_source}</span>}
        </div>
      )}
      {candidate.context && <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{candidate.context}</p>}
      {(candidate.technical_metrics ?? []).length > 0 && (
        <div className="vr-technical-grid" aria-label="短线技术指标">
          {(candidate.technical_metrics ?? []).map((metric) => (
            <div key={metric.key}>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
            </div>
          ))}
        </div>
      )}
      {candidate.deterministic_reasons.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs leading-relaxed text-foreground/80">
          {candidate.deterministic_reasons.slice(0, 3).map((reason) => (
            <li key={reason} className="flex items-start gap-2"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />{reason}</li>
          ))}
        </ul>
      )}
      {candidate.next_step && (
        <p className="mt-3 flex items-start gap-1.5 border-t border-border/50 pt-2 text-xs text-warning">
          <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />下一观察：{candidate.next_step}
        </p>
      )}
    </article>
  );
}

function MessageCard({ message }: { message: AqspMessage }) {
  const sectors = uniqueNonEmpty(message.affected_sectors, 4);
  const path = uniqueNonEmpty(message.transmission_path, 4);
  const validation = uniqueNonEmpty(message.validation_signals, 2);
  const invalidation = uniqueNonEmpty(message.invalidation_signals, 2);
  const evidence = uniqueNonEmpty(message.supporting_evidence, 2);
  const summary = sameResearchText(message.title, message.summary) ? "" : message.summary;
  const sourceUrl = messageSourceUrl(message);
  return (
    <article className="vr-message-card">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {message.category && <span className="vr-chip vr-chip-primary">{message.category}</span>}
            {message.event_type && <span className="vr-chip">{message.event_type}</span>}
            {message.source && <span className="vr-chip">{message.source}</span>}
            {message.impact && <span className={cn("vr-chip", message.impact === "利空" ? "vr-chip-negative" : message.impact === "利好" ? "vr-chip-positive" : "")}>{message.impact}</span>}
            <time className="text-[10px] text-muted-foreground">{formatAqspTime(message.published_at)}</time>
          </div>
          <h3 className="mt-2 text-sm font-medium leading-relaxed">{message.title || "消息标题未记录"}</h3>
        </div>
        <MessageSquareText className="mt-0.5 h-4 w-4 shrink-0 text-primary/75" />
      </div>
      {summary && <p className="mt-1.5 text-xs leading-relaxed text-foreground/78">{summary}</p>}
      {sectors.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="vr-kicker mr-0.5">影响板块</span>
          {sectors.map((sector) => <span key={sector} className="vr-chip vr-chip-primary">{sector}</span>)}
        </div>
      )}
      {(path.length > 0 || message.transmission_hypothesis) && (
        <div className="vr-message-impact">
          <p className="vr-kicker">产业链传导</p>
          {path.length > 0 && <p className="mt-1 text-xs font-medium">{path.join(" -> ")}</p>}
          {message.transmission_hypothesis && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{message.transmission_hypothesis}</p>}
          {(validation.length > 0 || invalidation.length > 0) && <div className="mt-2 grid gap-1 text-[11px] text-muted-foreground sm:grid-cols-2">
            {validation.length > 0 && <p><span className="text-success">确认信号：</span>{validation.join("；")}</p>}
            {invalidation.length > 0 && <p><span className="text-warning">失效条件：</span>{invalidation.join("；")}</p>}
          </div>}
        </div>
      )}
      {evidence.length > 0 && <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">证据：{evidence.join("；")}</p>}
      {sourceUrl && (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-flex max-w-full items-center gap-1 text-[11px] text-primary underline-offset-2 hover:underline"
        >
          <ExternalLink className="h-3.5 w-3.5 shrink-0" />
          <span className="truncate">查看原文</span>
        </a>
      )}
    </article>
  );
}

function DebateCard({ result }: { result: AqspAgentResult }) {
  const process = debateProcessText(result);
  const conclusion = result.conclusion.trim();
  const visibleProcess = sameResearchText(process, conclusion) ? "" : process;
  const roundSummaries = dedupeResearchText(result.round_summaries ?? [])
    .filter((summary) => !sameResearchText(summary, conclusion) && !sameResearchText(summary, process))
    .slice(0, 2);
  return (
    <article className="vr-debate-card">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2.5">
          <span className="vr-agent-mark"><Bot className="h-4 w-4" /></span>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{result.display_name || result.symbol || "对象未记录"}</p>
            <p className="mt-1 font-mono text-[11px] text-muted-foreground">{result.symbol || "代码未记录"}</p>
          </div>
        </div>
        <span className={cn("vr-status", result.conclusion ? "vr-status-success" : "vr-status-warning")}>
          {result.conclusion ? "结论已记录" : "结论缺失"}
        </span>
      </div>
      <div className="mt-3 grid gap-2.5 sm:grid-cols-[1fr_auto]">
        <div className="vr-debate-process">
          <p className="vr-kicker flex items-center gap-1.5"><UsersRound className="h-3.5 w-3.5" />Agent 讨论</p>
          <p className="mt-2 text-xs leading-relaxed text-foreground/80">{visibleProcess || "讨论过程未记录"}</p>
          {result.active_roles.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{result.active_roles.map((role) => <span key={role} className="vr-chip">{role}</span>)}</div>}
          {roundSummaries.length > 0 && (
            <ol className="mt-2 space-y-1 border-t border-border/45 pt-2 text-[11px] leading-relaxed text-muted-foreground">
              {roundSummaries.map((summary, index) => <li key={`${index}-${summary}`}>第 {index + 1} 轮：{summary}</li>)}
            </ol>
          )}
        </div>
        <div className="grid min-w-[9rem] grid-cols-3 gap-1.5 sm:grid-cols-1">
          <div className="vr-vote vr-vote-bull"><span>支持</span><strong>{result.bull_count}</strong></div>
          <div className="vr-vote vr-vote-neutral"><span>保留</span><strong>{result.neutral_count}</strong></div>
          <div className="vr-vote vr-vote-bear"><span>风险</span><strong>{result.bear_count}</strong></div>
        </div>
      </div>
      <div className="vr-debate-conclusion">
        <p className="vr-kicker text-primary">汇总结论</p>
        <p className="mt-1 text-sm leading-relaxed">{conclusion || "暂无可展示的讨论结论。"}</p>
      </div>
      {(result.primary_risk_gate || result.next_trigger) && (
        <div className="mt-3 grid gap-2 border-t border-border/50 pt-2 text-xs sm:grid-cols-2">
          {result.primary_risk_gate && <p className="flex items-start gap-1.5 text-warning"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />风险：{result.primary_risk_gate}</p>}
          {result.next_trigger && <p className="flex items-start gap-1.5 text-muted-foreground"><Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />下一验证：{result.next_trigger}</p>}
        </div>
      )}
    </article>
  );
}

function LoadingState() {
  return <div className="vr-state-panel"><RefreshCw className="h-4 w-4 animate-spin text-primary" /><span>正在读取研究数据…</span></div>;
}

function ErrorState({ error, onRefresh }: { error: string; onRefresh: () => void }) {
  return (
    <div className="vr-state-panel vr-state-panel-warning">
      <AlertCircle className="h-4 w-4 shrink-0 text-warning" />
      <span className="min-w-0 flex-1">暂时无法读取研究数据：{error}</span>
      <button type="button" onClick={onRefresh} className="vr-icon-button" title="重新读取"><RefreshCw className="h-4 w-4" /></button>
    </div>
  );
}

function MarketContextCard({ snapshot }: { snapshot: AqspSnapshot }) {
  const context = snapshot.market_context;
  const lines = uniqueNonEmpty(context?.summary_lines, 5);
  const crossMarket = context?.cross_market ?? [];
  return (
    <section id={RESEARCH_SECTION_IDS[3]} className="vr-module vr-board-section">
      <div className="vr-section-heading"><div><p className="vr-kicker">05 · 市场与传导</p><h2>市场与产业链</h2></div><span className="vr-count">{crossMarket.length} 条</span></div>
      {!context ? <EmptyState title="暂无市场上下文" detail="当前快照没有可核验的跨市场或产业链传导记录。" /> : (
        <div className="vr-context-list">
          <div className="vr-context-status"><span>状态</span><strong>{context.status || "未记录"}</strong></div>
          {context.overview && <p className="text-xs leading-relaxed">{context.overview}</p>}
          {lines.length > 0 && <ul className="space-y-1.5 text-xs leading-relaxed text-muted-foreground">{lines.map((line) => <li key={line}>· {line}</li>)}</ul>}
          {crossMarket.length > 0 && <div className="space-y-2 border-t border-border/50 pt-2">{crossMarket.slice(0, 4).map((item) => <div key={`${item.rule_id}-${item.source_title}`} className="vr-context-item"><strong>{item.theme || item.rule_id}</strong><span>{item.summary || item.action || "待验证"}</span></div>)}</div>}
          {context.warnings.length > 0 && <p className="text-[11px] leading-relaxed text-warning">数据告警：{context.warnings.slice(0, 2).join("；")}</p>}
        </div>
      )}
    </section>
  );
}

function TestVariantsPanel({ snapshot }: { snapshot?: AqspSnapshot }) {
  const gateReady = snapshot?.recommendation_gate?.recommendation_allowed === true;
  return (
    <section id={TEST_VARIANTS_SECTION_ID} className="vr-lab-panel" aria-label="测试与变体">
      <div className="vr-section-heading"><div><p className="vr-kicker">独立实验区</p><h2>测试与变体</h2></div><span className="vr-count">不参与正式推荐</span></div>
      <div className="vr-lab-grid">
        <div><span>正式短线主线</span><strong className={gateReady ? "vr-lab-ready" : "vr-lab-observe"}>{gateReady ? "可进入纸面复核" : snapshot ? "当前仅观察" : "等待研究快照"}</strong><p>使用实时数据，受全局 gate 约束。</p></div>
        <div><span>Walk-forward 变体组</span><strong>历史回测验证</strong><p>用于比较参数变体，不改写今日评分。</p></div>
        <div><span>盘中资源保护变体</span><strong>观察模式</strong><p>降低外部抓取资源占用，不进入正式 ledger。</p></div>
      </div>
    </section>
  );
}

export function AqspResearchWorkspace() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  if (loading && !data) return <><LoadingState /><TestVariantsPanel /></>;
  if (error && !data) return <><ErrorState error={error} onRefresh={refresh} /><TestVariantsPanel /></>;
  if (!data) return <><EmptyState title="暂无研究数据" detail="当前没有可展示的快照，数据产出后会自动出现在这里。" /><TestVariantsPanel /></>;

  const conclusion = snapshotConclusion(data);
  const stale = isAqspSnapshotStale(data);
  return (
    <div className="vr-research-page">
      <div className="vr-top-summary">
        <header className="vr-page-topline" id="overview">
          <div>
            <p className="vr-kicker text-primary">AQSP / DAILY RESEARCH</p>
            <div className="mt-1 flex flex-wrap items-end gap-x-3 gap-y-1">
              <h1 className="text-2xl font-semibold">当天研究</h1>
              <span className="vr-current-date">{data.selected_date || "日期未记录"}</span>
            </div>
            <div className="mt-1"><SnapshotMeta snapshot={data} /></div>
          </div>
          <button type="button" onClick={refresh} disabled={loading} className="vr-refresh-button" title="刷新研究数据">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新
          </button>
        </header>

        <DateStrip snapshot={data} />
      {stale && <FreshnessNotice snapshot={data} />}
      <RecommendationGateNotice snapshot={data} />
      <CurrentEmptyObservationNotice snapshot={data} />
      {error && <div className="mb-3 text-xs text-warning">后台刷新未完成，仍展示上一次已读取的数据。</div>}

        <section className="vr-module vr-conclusion-panel" aria-labelledby="conclusion-title">
          <div className="flex min-w-0 items-start gap-3">
            <span className="vr-section-icon"><Sparkles className="h-4 w-4" /></span>
            <div className="min-w-0">
              <p className="vr-kicker text-primary">01 · 当天结论</p>
              <h2 id="conclusion-title" className="mt-1 text-base font-semibold leading-relaxed">{conclusion || "今日结论未记录"}</h2>
              {data.summaries.length > 1 && <div className="mt-2 space-y-1 text-xs leading-relaxed text-muted-foreground">{data.summaries.slice(1, 3).map((line) => <p key={line}>· {line}</p>)}</div>}
            </div>
          </div>
          <div className="vr-summary-stats" aria-label="研究数据统计">
            <div><strong>{data.candidates.length}</strong><span>候选</span></div>
            <div><strong>{data.messages.length}</strong><span>消息</span></div>
            <div><strong>{data.debates.length}</strong><span>讨论</span></div>
            <div><strong>{data.source.lag_days > 0 ? `${data.source.lag_days}d` : "0d"}</strong><span>数据滞后</span></div>
          </div>
        </section>
      </div>

      <div className="vr-research-columns">
      <section id={RESEARCH_SECTION_IDS[0]} className="vr-module vr-board-section">
        <div className="vr-section-heading"><div><p className="vr-kicker">02 · 来源与影响</p><h2>消息</h2></div><span className="vr-count">{data.messages.length} 条</span></div>
        {data.messages.length === 0 ? <EmptyState title="当前没有消息摘要" detail="快照未记录可核验消息，不在界面中补充推断。" /> : <div className="vr-message-list">{data.messages.map((message) => <MessageCard key={`${message.title}-${message.published_at}`} message={message} />)}</div>}
      </section>

      <section id={RESEARCH_SECTION_IDS[1]} className="vr-module vr-board-section">
        <div className="vr-section-heading"><div><p className="vr-kicker">03 · 评分与依据</p><h2>候选</h2></div><span className="vr-count">{data.candidates.length} 个</span></div>
        {data.candidates.length === 0 ? <EmptyState title="当前没有候选" detail="可能是研究 gate 阻塞，或当天数据尚未产出。" /> : <div className="vr-candidate-grid">{data.candidates.map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} />)}</div>}
      </section>

      <section id={RESEARCH_SECTION_IDS[2]} className="vr-module vr-board-section vr-discussion-section">
        <div className="vr-section-heading"><div><p className="vr-kicker">04 · 分歧与风险</p><h2>Agent 讨论</h2></div><span className="vr-count">{data.debates.length} 条</span></div>
        {data.debates.length === 0 ? <EmptyState title="暂无讨论记录" detail="当前快照没有多 Agent 讨论结果，保留确定性研究数据。" /> : <div className="grid gap-3 xl:grid-cols-2">{data.debates.map((result) => <DebateCard key={result.symbol} result={result} />)}</div>}
      </section>
      <MarketContextCard snapshot={data} />
      </div>
      <TestVariantsPanel snapshot={data} />
    </div>
  );
}

export function AqspDailySnapshot() {
  return <AqspResearchWorkspace />;
}
