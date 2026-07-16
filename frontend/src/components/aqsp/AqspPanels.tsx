import {
  AlertCircle,
  ArrowRight,
  Bot,
  CalendarDays,
  Check,
  CircleAlert,
  Clock3,
  Columns3,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  UsersRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { AqspAgentResult, AqspCandidate, AqspCrossMarket, AqspMessage, AqspSnapshot } from "@/lib/api";
import { debateProcessText, formatResearchDate, snapshotConclusion } from "@/lib/research-view";
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
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      <span>{snapshot.selected_date || "日期未记录"}</span>
      <span>更新 {formatAqspTime(snapshot.generated_at)}</span>
      <span className={cn("vr-status", stale ? "vr-status-warning" : "vr-status-success")}>
        {stale ? "历史数据" : "数据有效"}
      </span>
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
      <div className="mt-3 flex flex-wrap gap-1.5">
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
      {candidate.context && <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{candidate.context}</p>}
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
        <ul className="mt-3 space-y-1 text-xs leading-relaxed text-foreground/80">
          {candidate.deterministic_reasons.slice(0, 3).map((reason) => (
            <li key={reason} className="flex items-start gap-2"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />{reason}</li>
          ))}
        </ul>
      )}
      {candidate.next_step && (
        <p className="mt-4 flex items-start gap-1.5 border-t border-border/50 pt-3 text-xs text-warning">
          <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />下一观察：{candidate.next_step}
        </p>
      )}
    </article>
  );
}

function matchingTransmission(message: AqspMessage, items: readonly AqspCrossMarket[]): AqspCrossMarket | null {
  const title = message.title.trim();
  if (!title) return null;
  return items.find((item) => item.source_title === title || title.includes(item.source_title) || item.source_title.includes(title)) ?? null;
}

function MessageCard({ message, transmission }: { message: AqspMessage; transmission: AqspCrossMarket | null }) {
  const sectors = uniqueNonEmpty(message.affected_sectors ?? transmission?.affected_sectors, 5);
  const path = uniqueNonEmpty(message.transmission_path ?? transmission?.transmission_path, 4);
  const validations = uniqueNonEmpty(message.validation_signals ?? transmission?.validation_signals, 3);
  const impact = message.impact || transmission?.action || transmission?.summary || "";
  return (
    <article className="vr-message-card">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {message.category && <span className="vr-chip vr-chip-primary">{message.category}</span>}
            {message.source && <span className="vr-chip">{message.source}</span>}
            <time className="text-[10px] text-muted-foreground">{formatAqspTime(message.published_at)}</time>
          </div>
          <h3 className="mt-2 text-sm font-medium leading-relaxed">{message.title || "消息标题未记录"}</h3>
        </div>
        <MessageSquareText className="mt-0.5 h-4 w-4 shrink-0 text-primary/75" />
      </div>
      {message.summary && <p className="mt-3 text-xs leading-relaxed text-foreground/78">{message.summary}</p>}
      {impact && (
        <div className="vr-message-impact">
          <p className="vr-kicker text-primary">影响</p>
          <p>{impact}</p>
        </div>
      )}
      {sectors.length > 0 && (
        <div className="mt-3">
          <p className="vr-kicker">产业链映射</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">{sectors.map((sector) => <span key={sector} className="vr-chip vr-chip-primary">{sector}</span>)}</div>
        </div>
      )}
      {path.length > 0 && (
        <div className="mt-3">
          <p className="vr-kicker">传导路径</p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">{path.join(" → ")}</p>
        </div>
      )}
      {validations.length > 0 && (
        <div className="mt-3 border-t border-border/45 pt-3">
          <p className="vr-kicker">验证信号</p>
          <ul className="mt-1.5 space-y-1 text-[11px] leading-relaxed text-muted-foreground">
            {validations.map((signal) => <li key={signal}>· {signal}</li>)}
          </ul>
        </div>
      )}
    </article>
  );
}

function DebateCard({ result }: { result: AqspAgentResult }) {
  const process = debateProcessText(result);
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
      <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]">
        <div className="rounded-lg border border-border/55 bg-background/20 p-3">
          <p className="vr-kicker flex items-center gap-1.5"><UsersRound className="h-3.5 w-3.5" />讨论过程</p>
          <p className="mt-2 text-xs leading-relaxed text-foreground/80">{process || "讨论过程未记录"}</p>
          {result.active_roles.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{result.active_roles.map((role) => <span key={role} className="vr-chip">{role}</span>)}</div>}
        </div>
        <div className="grid min-w-[9rem] grid-cols-3 gap-1.5 sm:grid-cols-1">
          <div className="vr-vote vr-vote-bull"><span>支持</span><strong>{result.bull_count}</strong></div>
          <div className="vr-vote vr-vote-neutral"><span>保留</span><strong>{result.neutral_count}</strong></div>
          <div className="vr-vote vr-vote-bear"><span>风险</span><strong>{result.bear_count}</strong></div>
        </div>
      </div>
      <div className="mt-4 border-l-2 border-primary/70 pl-3">
        <p className="vr-kicker text-primary">讨论结论</p>
        <p className="mt-1 text-sm leading-relaxed">{result.conclusion || "暂无可展示的讨论结论。"}</p>
      </div>
      {(result.primary_risk_gate || result.next_trigger) && (
        <div className="mt-4 grid gap-2 border-t border-border/50 pt-3 text-xs sm:grid-cols-2">
          {result.primary_risk_gate && <p className="flex items-start gap-1.5 text-warning"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />风险卡点：{result.primary_risk_gate}</p>}
          {result.next_trigger && <p className="flex items-start gap-1.5 text-muted-foreground"><Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />验证条件：{result.next_trigger}</p>}
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

export function AqspResearchWorkspace() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  if (loading && !data) return <LoadingState />;
  if (error && !data) return <ErrorState error={error} onRefresh={refresh} />;
  if (!data) return <EmptyState title="暂无研究数据" detail="当前没有可展示的快照，数据产出后会自动出现在这里。" />;

  const conclusion = snapshotConclusion(data);
  const stale = isAqspSnapshotStale(data);
  return (
    <div className="vr-research-page">
      <header className="vr-page-topline" id="overview">
        <div>
          <p className="vr-kicker text-primary">AQSP / DAILY RESEARCH</p>
          <div className="mt-2 flex flex-wrap items-end gap-x-3 gap-y-1">
            <h1 className="text-2xl font-semibold">AQSP 研究工作台</h1>
            <span className="text-sm text-muted-foreground">{data.selected_date || "日期未记录"}</span>
          </div>
          <div className="mt-2"><SnapshotMeta snapshot={data} /></div>
        </div>
        <button type="button" onClick={refresh} disabled={loading} className="vr-refresh-button" title="刷新研究数据">
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新
        </button>
      </header>

      <DateStrip snapshot={data} />
      {stale && <FreshnessNotice snapshot={data} />}
      {error && <div className="mb-5 text-xs text-warning">后台刷新未完成，仍展示上一次已读取的数据。</div>}

      <section className="vr-conclusion-panel" aria-labelledby="conclusion-title">
        <div className="flex items-start gap-3">
          <span className="vr-section-icon"><Sparkles className="h-4 w-4" /></span>
          <div className="min-w-0">
            <p className="vr-kicker text-primary">研究结论</p>
            <h2 id="conclusion-title" className="mt-2 text-lg font-semibold leading-relaxed">{conclusion || "今日结论未记录"}</h2>
            {data.summaries.length > 1 && <div className="mt-3 space-y-1 text-xs leading-relaxed text-muted-foreground">{data.summaries.slice(1, 3).map((line) => <p key={line}>· {line}</p>)}</div>}
          </div>
        </div>
        <div className="vr-summary-stats" aria-label="研究数据统计">
          <div><strong>{data.candidates.length}</strong><span>候选</span></div>
          <div><strong>{data.messages.length}</strong><span>消息</span></div>
          <div><strong>{data.debates.length}</strong><span>讨论</span></div>
          <div><strong>{data.source.lag_days > 0 ? `${data.source.lag_days}d` : "0d"}</strong><span>数据滞后</span></div>
        </div>
      </section>

      <div className="vr-board-grid">
        <section id="candidates" className="vr-board-section">
          <div className="vr-section-heading"><div><p className="vr-kicker">评分与依据</p><h2>候选研究</h2></div><span className="vr-count">{data.candidates.length} 条</span></div>
          {data.candidates.length === 0 ? <EmptyState title="当前没有候选" detail="可能是研究 gate 阻塞，或当天数据尚未产出。" /> : <div className="vr-candidate-grid">{data.candidates.map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} />)}</div>}
        </section>

        <section id="messages" className="vr-board-section vr-messages-section">
          <div className="vr-section-heading"><div><p className="vr-kicker flex items-center gap-1.5"><Columns3 className="h-3.5 w-3.5" />独立证据列</p><h2>消息证据</h2></div><span className="vr-count">{data.message_status || `${data.messages.length} 条`}</span></div>
          {data.messages.length === 0 ? <EmptyState title="当前没有消息摘要" detail="快照未记录可核验消息，不在界面中补充推断。" /> : <div className="vr-message-list">{data.messages.slice(0, 5).map((message) => <MessageCard key={`${message.title}-${message.published_at}`} message={message} transmission={matchingTransmission(message, data.market_context?.cross_market ?? [])} />)}</div>}
        </section>
      </div>

      <section id="discussion" className="vr-board-section vr-discussion-section">
        <div className="vr-section-heading"><div><p className="vr-kicker">分歧与风险</p><h2>讨论复核</h2></div><span className="vr-count">{data.debates.length} 条</span></div>
        {data.debates.length === 0 ? <EmptyState title="暂无讨论记录" detail="当前快照没有多 Agent 讨论结果，保留确定性研究数据。" /> : <div className="grid gap-3 xl:grid-cols-2">{data.debates.map((result) => <DebateCard key={result.symbol} result={result} />)}</div>}
        <div className="mt-5 flex items-start gap-2 border-t border-border/50 pt-3 text-[11px] leading-relaxed text-muted-foreground/70"><ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />讨论仅作为研究补充，确定性评分和原始证据保持独立。</div>
      </section>
    </div>
  );
}

export function AqspDailySnapshot() {
  return <AqspResearchWorkspace />;
}

export function AqspIntelSnapshot() {
  return <AqspResearchWorkspace />;
}

export function AqspPaperResearch() {
  return <AqspResearchWorkspace />;
}
