import { useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock3,
  GitBranch,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Link } from "react-router-dom";
import { GlassCard } from "@/components/ui/GlassCard";
import { ApiError, authHeaders, type AqspAgentResult, type AqspCandidate, type AqspCrossMarket, type AqspSnapshot } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatAqspTime, isAqspSnapshotStale } from "./useAqspSnapshot";

interface SnapshotResponse {
  data?: AqspSnapshot;
}

function useWorkspaceSnapshot() {
  const [data, setData] = useState<AqspSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState(() => localStorage.getItem("vr-selected-date") || "");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const onDateChange = (event: Event) => {
      const date = (event as CustomEvent<string>).detail;
      if (date) setSelectedDate(date);
    };
    window.addEventListener("vr-date-change", onDateChange);
    return () => window.removeEventListener("vr-date-change", onDateChange);
  }, []);

  useEffect(() => {
    let active = true;
    const query = selectedDate ? `?date=${encodeURIComponent(selectedDate)}` : "";
    setLoading(true);
    setError(null);
    fetch(`/api/aqsp/snapshot${query}`, { headers: authHeaders() })
      .then(async (response) => {
        let payload: SnapshotResponse | null = null;
        try {
          payload = (await response.json()) as SnapshotResponse;
        } catch {
          // Keep the HTTP status as the useful failure detail.
        }
        if (!response.ok || !payload?.data) {
          throw new ApiError(`研究快照暂不可用 HTTP ${response.status}`, response.status);
        }
        return payload.data;
      })
      .then((snapshot) => {
        if (!active) return;
        setData(snapshot);
        if (!selectedDate) {
          setSelectedDate(snapshot.selected_date);
          localStorage.setItem("vr-selected-date", snapshot.selected_date);
        }
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setError(reason instanceof ApiError ? reason.message : "研究快照加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reloadKey, selectedDate]);

  return { data, loading, error, refresh: () => setReloadKey((value) => value + 1) };
}

function SnapshotMeta({ snapshot }: { snapshot: AqspSnapshot }) {
  const stale = isAqspSnapshotStale(snapshot);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      <span>研究日 {snapshot.selected_date || "—"}</span>
      <span>更新于 {formatAqspTime(snapshot.generated_at)}</span>
      <span className={cn("rounded-full px-2 py-0.5", stale ? "bg-warning/15 text-warning" : "bg-success/15 text-success")}>
        {stale ? "历史快照" : "数据有效"}
      </span>
    </div>
  );
}

function SnapshotState({ loading, error, snapshot, onRefresh }: { loading: boolean; error: string | null; snapshot: AqspSnapshot | null; onRefresh: () => void }) {
  if (loading && !snapshot) {
    return <GlassCard className="mb-5"><p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />读取研究快照…</p></GlassCard>;
  }
  if (error && !snapshot) {
    return <GlassCard className="mb-5 border-warning/30"><div className="flex items-start justify-between gap-3"><p className="flex items-start gap-2 text-sm text-muted-foreground"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />暂时无法读取当天研究：{error}</p><button onClick={onRefresh} className="text-muted-foreground hover:text-primary" title="重试"><RefreshCw className="h-4 w-4" /></button></div></GlassCard>;
  }
  if (!snapshot) return <GlassCard className="mb-5"><p className="text-sm text-muted-foreground">暂无研究快照，数据产出后会显示在这里。</p></GlassCard>;
  return null;
}

function FreshnessNotice({ snapshot }: { snapshot: AqspSnapshot }) {
  if (!isAqspSnapshotStale(snapshot)) return null;
  return <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs leading-relaxed text-warning"><Clock3 className="mt-0.5 h-4 w-4 shrink-0" />这是历史快照，以下内容仅用于纸面观察与复核。</div>;
}

function EmptyState({ children }: { children: string }) {
  return <p className="rounded-lg border border-dashed border-border/70 p-4 text-xs text-muted-foreground">{children}</p>;
}

function CandidateCard({ candidate, compact = false }: { candidate: AqspCandidate; compact?: boolean }) {
  return (
    <div className={cn("rounded-xl border border-border/60 bg-muted/20", compact ? "p-3" : "p-4")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><p className="truncate font-semibold">{candidate.display_name || candidate.symbol}</p><p className="font-mono text-[11px] text-muted-foreground">{candidate.symbol}</p></div>
        <div className="text-right"><p className="font-mono text-lg font-bold text-primary">{Number.isFinite(candidate.score) ? candidate.score.toFixed(1) : "—"}</p><p className="text-[10px] text-muted-foreground">研究分</p></div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5"><span className="rounded-full bg-primary/12 px-2 py-0.5 text-[11px] text-primary">{candidate.research_status || "待复核"}</span><span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">{candidate.evidence_status || "证据待补"}</span></div>
      {candidate.context && <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{candidate.context}</p>}
      {!compact && candidate.deterministic_reasons.length > 0 && <div className="mt-3 space-y-1 text-xs text-foreground/80">{candidate.deterministic_reasons.slice(0, 3).map((reason) => <p key={reason}>· {reason}</p>)}</div>}
      {candidate.next_step && <p className="mt-3 flex items-start gap-1.5 text-xs text-warning"><ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />下一观察：{candidate.next_step}</p>}
    </div>
  );
}

function CommitteeCard({ result }: { result: AqspAgentResult }) {
  return (
    <div className="rounded-xl border border-border/60 bg-muted/20 p-3.5">
      <div className="flex items-start gap-2.5"><Bot className="mt-0.5 h-4 w-4 shrink-0 text-primary" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-baseline justify-between gap-2"><p className="font-medium">{result.display_name || result.symbol}</p>{result.round_count > 0 && <span className="text-[10px] text-muted-foreground">{result.round_count} 轮讨论</span>}</div><p className="mt-1 text-xs leading-relaxed text-foreground/85">{result.conclusion || "暂无委员会结论"}</p></div></div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground"><span>共识 {result.bull_count}</span><span>保留 {result.neutral_count}</span><span>风险 {result.bear_count}</span></div>
      {result.primary_risk_gate && <p className="mt-2 text-xs text-warning">风险卡点：{result.primary_risk_gate}</p>}
      {result.next_trigger && <p className="mt-1 text-xs text-muted-foreground">验证条件：{result.next_trigger}</p>}
    </div>
  );
}

function MessageCard({ title, summary, impact, source, category, publishedAt }: { title: string; summary: string; impact: string; source: string; category: string; publishedAt: string }) {
  return <article className="rounded-xl border border-border/60 bg-muted/20 p-3.5"><div className="flex items-start justify-between gap-3"><h4 className="min-w-0 text-xs font-medium leading-relaxed">{title}</h4><time className="shrink-0 text-[10px] text-muted-foreground">{formatAqspTime(publishedAt)}</time></div><p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{summary || impact || "暂无摘要"}</p><p className="mt-2 text-[10px] text-muted-foreground/65">{source} · {category}</p></article>;
}

export function AqspDailySnapshot() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  return <><SnapshotState loading={loading} error={error} snapshot={data} onRefresh={refresh} />{data && <GlassCard className="mb-5" glow>
    <div className="flex flex-wrap items-start justify-between gap-3"><div><p className="vr-kicker">当天研究简报</p><h2 className="mt-1 text-xl font-semibold">先看结论，再看证据</h2><p className="mt-1 text-xs text-muted-foreground">候选、消息与委员会结果来自同一份只读快照。</p></div><button onClick={refresh} disabled={loading} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-primary disabled:opacity-50"><RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新</button></div>
    <div className="mt-3"><SnapshotMeta snapshot={data} /></div><FreshnessNotice snapshot={data} />
    <div className="vr-conclusion mt-5"><p className="vr-kicker text-primary">当天结论</p><p className="mt-1 text-base font-semibold leading-relaxed">{data.summaries[0] || data.market_context?.overview || "暂无当天结论"}</p>{data.summaries.length > 1 && <div className="mt-2 space-y-1 text-xs text-muted-foreground">{data.summaries.slice(1).map((line) => <p key={line}>· {line}</p>)}</div>}</div>
    <div className="mt-6 grid gap-6 xl:grid-cols-[1.12fr_.88fr]">
      <section><div className="mb-2 flex items-center justify-between gap-2"><div><p className="vr-kicker">候选</p><h3 className="mt-1 text-sm font-semibold">今天值得继续看的对象</h3></div><Link to="/paper-research" className="text-xs text-primary hover:underline">完整列表</Link></div>{data.candidates.length === 0 ? <EmptyState>当前没有候选，可能是研究 gate 阻塞或数据不足。</EmptyState> : <div className="space-y-2.5">{data.candidates.slice(0, 3).map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} compact />)}</div>}</section>
      <section><div className="mb-2"><p className="vr-kicker">消息</p><h3 className="mt-1 text-sm font-semibold">需要放进证据链的变化</h3></div>{data.messages.length === 0 ? <EmptyState>当前没有进入快照的消息。</EmptyState> : <div className="space-y-2.5">{data.messages.slice(0, 4).map((message) => <MessageCard key={`${message.title}-${message.published_at}`} title={message.title} summary={message.summary} impact={message.impact} source={message.source} category={message.category} publishedAt={message.published_at} />)}</div>}</section>
    </div>
    <section className="mt-6"><div className="mb-2"><p className="vr-kicker">委员会结果</p><h3 className="mt-1 text-sm font-semibold">保留分歧，不替代确定性评分</h3></div>{data.debates.length === 0 ? <EmptyState>本次快照没有委员会结果，保留确定性研究数据。</EmptyState> : <div className="grid gap-2.5 md:grid-cols-2">{data.debates.slice(0, 4).map((result) => <CommitteeCard key={result.symbol} result={result} />)}</div>}</section>
    <div className="mt-5 flex items-start gap-2 border-t border-border/50 pt-3 text-[11px] leading-relaxed text-muted-foreground/70"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />只读纸面研究，不写入持仓或收益记录。</div>
  </GlassCard>}</>;
}

function CrossMarketCard({ item }: { item: AqspCrossMarket }) {
  return <div className="rounded-xl border border-border/60 bg-muted/20 p-4"><div className="flex items-start justify-between gap-3"><div className="min-w-0"><p className="font-medium">{item.theme || "跨市场事件"}</p><p className="mt-1 truncate text-[11px] text-muted-foreground">{item.source_region} · {item.source_title}</p></div><span className="rounded-full bg-primary/12 px-2 py-0.5 text-[10px] text-primary">{item.strength || "观察"}</span></div>{item.summary && <p className="mt-3 text-xs leading-relaxed">{item.summary}</p>}{item.transmission_path.length > 0 && <p className="mt-3 flex items-start gap-1.5 text-xs text-foreground/80"><GitBranch className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />{item.transmission_path.join(" → ")}</p>}<div className="mt-3 grid gap-3 sm:grid-cols-2"><div><p className="text-[10px] text-success">验证信号</p><p className="mt-1 text-xs text-muted-foreground">{item.validation_signals.join("；") || "暂无"}</p></div><div><p className="text-[10px] text-warning">失效条件</p><p className="mt-1 text-xs text-muted-foreground">{item.invalidation_signals.join("；") || "暂无"}</p></div></div></div>;
}

export function AqspIntelSnapshot() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  return <><SnapshotState loading={loading} error={error} snapshot={data} onRefresh={refresh} />{data && <GlassCard className="mb-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="vr-kicker">消息核验</p><h2 className="mt-1 text-xl font-semibold">来源状态与跨市场事实</h2><p className="mt-1 text-xs text-muted-foreground">只展示快照内已落盘的来源和验证条件。</p></div><button onClick={refresh} disabled={loading} className="text-muted-foreground hover:text-primary disabled:opacity-50" title="刷新"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} /></button></div><div className="mt-3"><SnapshotMeta snapshot={data} /></div><FreshnessNotice snapshot={data} /><div className="mt-5 grid gap-2 sm:grid-cols-4"><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">有效源</p><p className="mt-1 font-medium">{data.source.effective || "未记录"}</p></div><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">源状态</p><p className="mt-1 font-medium text-primary">{data.source.status || "未记录"}</p></div><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">最新交易日</p><p className="mt-1 font-mono text-sm">{data.source.latest_trade_date || "—"}</p></div><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">数据滞后</p><p className={cn("mt-1 font-mono text-sm", data.source.lag_days > 0 ? "text-warning" : "text-success")}>{data.source.lag_days} 天</p></div></div>{data.market_context ? <><div className="mt-5 rounded-lg border border-primary/20 bg-primary/5 p-3"><p className="text-[11px] font-medium text-primary">传导总览 · {data.market_context.status || "观察"}</p><p className="mt-1 text-sm leading-relaxed">{data.market_context.overview || "暂无总览"}</p>{data.market_context.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-warning">{warning}</p>)}</div><div className="mt-4 space-y-2.5">{data.market_context.cross_market.length === 0 ? <EmptyState>当前没有跨市场传导记录。</EmptyState> : data.market_context.cross_market.slice(0, 3).map((item) => <CrossMarketCard key={item.rule_id} item={item} />)}</div></> : <div className="mt-5"><EmptyState>当前快照没有跨市场传导上下文。</EmptyState></div>}</GlassCard>}</>;
}

export function AqspPaperResearch() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  return <><SnapshotState loading={loading} error={error} snapshot={data} onRefresh={refresh} />{data && <GlassCard className="mb-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="vr-kicker">候选工作区</p><h2 className="mt-1 text-xl font-semibold">纸面研究</h2><p className="mt-1 text-xs text-muted-foreground">逐项记录证据、风险卡点和下一观察条件。</p></div><button onClick={refresh} disabled={loading} className="text-muted-foreground hover:text-primary disabled:opacity-50" title="刷新"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} /></button></div><div className="mt-3"><SnapshotMeta snapshot={data} /></div><FreshnessNotice snapshot={data} />{data.candidates.length === 0 ? <div className="mt-5"><EmptyState>暂无纸面候选，快照可能尚未产出，或当前研究 gate 未放行。</EmptyState></div> : <div className="mt-5 grid gap-3 md:grid-cols-2">{data.candidates.map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} />)}</div>}<div className="mt-5 rounded-lg border border-border/50 bg-muted/15 p-3 text-xs leading-relaxed text-muted-foreground"><p className="font-medium text-foreground">研究边界</p><p className="mt-1">候选仅用于纸面研究与历史复核，不生成操作指令。</p></div></GlassCard>}</>;
}
