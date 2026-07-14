import {
  AlertCircle,
  ArrowRight,
  Bot,
  Clock3,
  Database,
  FileSearch,
  GitBranch,
  Loader2,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { Link } from "react-router-dom";
import { GlassCard } from "@/components/ui/GlassCard";
import type { AqspAgentResult, AqspCandidate, AqspCrossMarket, AqspSnapshot } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatAqspTime, isAqspSnapshotStale, useAqspSnapshot } from "./useAqspSnapshot";

function SnapshotMeta({ snapshot }: { snapshot: AqspSnapshot }) {
  const stale = isAqspSnapshotStale(snapshot);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
      <span>研究日 {snapshot.selected_date || "—"}</span>
      <span>生成于 {formatAqspTime(snapshot.generated_at)}</span>
      <span className={cn("rounded-full px-2 py-0.5", stale ? "bg-warning/15 text-warning" : "bg-success/15 text-success")}>
        {stale ? "快照已过期" : "快照有效"}
      </span>
    </div>
  );
}

function SnapshotState({
  loading,
  error,
  snapshot,
  onRefresh,
}: {
  loading: boolean;
  error: string | null;
  snapshot: AqspSnapshot | null;
  onRefresh: () => void;
}) {
  if (loading && !snapshot) {
    return (
      <GlassCard className="mb-6">
        <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> AQSP 快照加载中…</p>
      </GlassCard>
    );
  }
  if (error && !snapshot) {
    return (
      <GlassCard className="mb-6 border-warning/30">
        <div className="flex items-start justify-between gap-3">
          <p className="flex items-start gap-2 text-sm text-muted-foreground"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-warning" /> AQSP 只读快照暂未接通：{error}</p>
          <button onClick={onRefresh} className="shrink-0 text-muted-foreground hover:text-primary" title="重试"><RefreshCw className="h-4 w-4" /></button>
        </div>
      </GlassCard>
    );
  }
  if (!snapshot) {
    return (
      <GlassCard className="mb-6">
        <p className="text-sm text-muted-foreground">暂无 AQSP 快照。数据产出后，这里显示纸面观察内容。</p>
      </GlassCard>
    );
  }
  return null;
}

function FreshnessNotice({ snapshot }: { snapshot: AqspSnapshot }) {
  if (!isAqspSnapshotStale(snapshot)) return null;
  return (
    <div className="mb-4 flex items-start gap-2 rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs leading-relaxed text-warning">
      <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
      <span>快照已过期，以下内容仅作为历史纸面观察，先刷新或等待新的 AQSP 产出。</span>
    </div>
  );
}

function CandidateCard({ candidate, compact = false }: { candidate: AqspCandidate; compact?: boolean }) {
  return (
    <div className={cn("rounded-xl border border-border/50 bg-muted/20", compact ? "p-3" : "p-4")}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-semibold">{candidate.display_name || candidate.symbol}</p>
          <p className="font-mono text-[11px] text-muted-foreground">{candidate.symbol}</p>
        </div>
        <div className="text-right">
          <p className="font-mono text-lg font-bold text-primary">{Number.isFinite(candidate.score) ? candidate.score.toFixed(1) : "—"}</p>
          <p className="text-[10px] text-muted-foreground">研究分</p>
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <span className="rounded-full bg-primary/12 px-2 py-0.5 text-[11px] text-primary">{candidate.research_status || "待复核"}</span>
        <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">{candidate.evidence_status || "证据状态未知"}</span>
      </div>
      {candidate.context && <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{candidate.context}</p>}
      {!compact && candidate.deterministic_reasons.length > 0 && (
        <div className="mt-3 space-y-1 text-xs text-foreground/80">
          {candidate.deterministic_reasons.slice(0, 3).map((reason) => <p key={reason}>· {reason}</p>)}
        </div>
      )}
      {candidate.next_step && (
        <p className="mt-3 flex items-start gap-1.5 text-xs text-warning"><ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0" />纸面观察：{candidate.next_step}</p>
      )}
    </div>
  );
}

function AgentCard({ result }: { result: AqspAgentResult }) {
  return (
    <div className="rounded-xl border border-border/50 bg-muted/20 p-3">
      <div className="flex items-start gap-2">
        <Bot className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{result.display_name || result.symbol}</p>
          <p className="mt-1 text-xs leading-relaxed text-foreground/80">{result.conclusion || "暂无结论"}</p>
        </div>
      </div>
      {result.primary_risk_gate && <p className="mt-2 text-xs text-warning">风险卡点：{result.primary_risk_gate}</p>}
      {result.next_trigger && <p className="mt-1 text-xs text-muted-foreground">后续观察：{result.next_trigger}</p>}
      {result.active_roles.length > 0 && <p className="mt-2 text-[11px] text-muted-foreground">视角：{result.active_roles.join("、")}</p>}
    </div>
  );
}

export function AqspDailySnapshot() {
  const { data, loading, error, refresh } = useAqspSnapshot();
  return (
    <>
      <SnapshotState loading={loading} error={error} snapshot={data} onRefresh={refresh} />
      {data && (
        <GlassCard glow className="mb-6">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2"><FileSearch className="h-5 w-5 text-primary" /><h2 className="font-semibold">AQSP 当日结论</h2><span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] text-primary">只读快照</span></div>
              <p className="mt-1 text-xs text-muted-foreground">候选、消息与研究讨论统一来自同一份当前日快照。</p>
            </div>
            <button onClick={refresh} disabled={loading} className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-primary disabled:opacity-50">
              <RefreshCw className={cn("h-3.5 w-3.5", loading && "animate-spin")} />刷新
            </button>
          </div>
          <SnapshotMeta snapshot={data} />
          <FreshnessNotice snapshot={data} />

          <div className="mt-4 rounded-xl border border-primary/25 bg-primary/5 p-4">
            <p className="text-[11px] font-medium text-primary">当前结论</p>
            <p className="mt-1 text-base font-semibold leading-relaxed">{data.summaries[0] || data.market_context?.overview || "暂无当前结论"}</p>
            {data.summaries.length > 1 && <div className="mt-2 space-y-1 text-xs text-muted-foreground">{data.summaries.slice(1).map((line) => <p key={line}>· {line}</p>)}</div>}
          </div>

          <div className="mt-5 grid gap-4 lg:grid-cols-2">
            <section>
              <div className="mb-2 flex items-center justify-between"><h3 className="text-sm font-semibold">候选观察 · 最多 3 个</h3><Link to="/paper-research" className="text-xs text-primary hover:underline">查看纸面研究</Link></div>
              {data.candidates.length === 0 ? <p className="rounded-lg border border-dashed border-border/70 p-4 text-xs text-muted-foreground">当前没有候选，可能是 gate 阻塞或数据不足。</p> : <div className="space-y-2">{data.candidates.slice(0, 3).map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} compact />)}</div>}
            </section>
            <section>
              <h3 className="mb-2 text-sm font-semibold">Agent 结果</h3>
              {data.debates.length === 0 ? <p className="rounded-lg border border-dashed border-border/70 p-4 text-xs text-muted-foreground">本次快照没有 Agent 结果，保留确定性研究数据。</p> : <div className="space-y-2">{data.debates.slice(0, 3).map((result) => <AgentCard key={result.symbol} result={result} />)}</div>}
            </section>
          </div>

          <section className="mt-5">
            <h3 className="mb-2 text-sm font-semibold">当前消息</h3>
            {data.messages.length === 0 ? <p className="rounded-lg border border-dashed border-border/70 p-4 text-xs text-muted-foreground">当前没有进入快照的消息。</p> : <div className="grid gap-2 md:grid-cols-2">{data.messages.slice(0, 4).map((message) => <div key={`${message.title}-${message.published_at}`} className="rounded-lg bg-muted/20 p-3"><div className="flex items-center justify-between gap-2"><span className="text-xs font-medium">{message.title}</span><span className="shrink-0 text-[10px] text-muted-foreground">{formatAqspTime(message.published_at)}</span></div><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{message.summary || message.impact}</p><p className="mt-2 text-[10px] text-muted-foreground/70">{message.source} · {message.category}</p></div>)}</div>}
          </section>
          <p className="mt-4 flex items-center gap-1.5 text-[11px] text-muted-foreground/70"><ShieldAlert className="h-3.5 w-3.5" />只读纸面研究。AQSP 快照不会改变 Vibe-Research 的客观数据口径。</p>
        </GlassCard>
      )}
    </>
  );
}

function CrossMarketCard({ item }: { item: AqspCrossMarket }) {
  return (
    <div className="rounded-xl border border-border/50 bg-muted/20 p-4">
      <div className="flex items-start justify-between gap-3"><div><p className="font-medium">{item.theme || "跨市事件"}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.source_region} · {item.source_title}</p></div><span className="rounded-full bg-primary/12 px-2 py-0.5 text-[10px] text-primary">{item.strength || "观察"}</span></div>
      {item.summary && <p className="mt-3 text-xs leading-relaxed">{item.summary}</p>}
      {item.transmission_path.length > 0 && <p className="mt-3 flex items-start gap-1.5 text-xs text-foreground/80"><GitBranch className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />{item.transmission_path.join(" → ")}</p>}
      <div className="mt-3 grid gap-2 sm:grid-cols-2"><div><p className="text-[10px] text-success">验证信号</p><p className="mt-1 text-xs text-muted-foreground">{item.validation_signals.join("；") || "暂无"}</p></div><div><p className="text-[10px] text-warning">失效条件</p><p className="mt-1 text-xs text-muted-foreground">{item.invalidation_signals.join("；") || "暂无"}</p></div></div>
    </div>
  );
}

export function AqspIntelSnapshot() {
  const { data, loading, error, refresh } = useAqspSnapshot();
  return (
    <>
      <SnapshotState loading={loading} error={error} snapshot={data} onRefresh={refresh} />
      {data && (
        <GlassCard className="mb-6">
          <div className="mb-4 flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><Database className="h-5 w-5 text-primary" /><h2 className="font-semibold">AQSP 源健康与跨市传导</h2></div><p className="mt-1 text-xs text-muted-foreground">只展示快照内已落盘的来源状态与传导假设，不把它们转成操作建议。</p></div><button onClick={refresh} disabled={loading} className="text-muted-foreground hover:text-primary disabled:opacity-50" title="刷新"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} /></button></div>
          <SnapshotMeta snapshot={data} /><FreshnessNotice snapshot={data} />
          <div className="mt-4 grid gap-2 sm:grid-cols-4"><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">有效源</p><p className="mt-1 font-medium">{data.source.effective || "未记录"}</p></div><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">源状态</p><p className="mt-1 font-medium text-primary">{data.source.status || "未记录"}</p></div><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">最新交易日</p><p className="mt-1 font-mono text-sm">{data.source.latest_trade_date || "—"}</p></div><div className="rounded-lg bg-muted/20 p-3"><p className="text-[11px] text-muted-foreground">数据滞后</p><p className={cn("mt-1 font-mono text-sm", data.source.lag_days > 0 ? "text-warning" : "text-success")}>{data.source.lag_days} 天</p></div></div>
          {data.market_context ? <><div className="mt-5 rounded-lg border border-primary/20 bg-primary/5 p-3"><p className="text-[11px] font-medium text-primary">传导总览 · {data.market_context.status || "观察"}</p><p className="mt-1 text-sm leading-relaxed">{data.market_context.overview || "暂无总览"}</p>{data.market_context.warnings.map((warning) => <p key={warning} className="mt-2 text-xs text-warning">{warning}</p>)}</div><div className="mt-4 space-y-2">{data.market_context.cross_market.length === 0 ? <p className="rounded-lg border border-dashed border-border/70 p-4 text-xs text-muted-foreground">当前没有跨市传导记录。</p> : data.market_context.cross_market.slice(0, 3).map((item) => <CrossMarketCard key={item.rule_id} item={item} />)}</div></> : <p className="mt-5 rounded-lg border border-dashed border-border/70 p-4 text-xs text-muted-foreground">当前快照没有跨市传导上下文。</p>}
          <p className="mt-4 flex items-center gap-1.5 text-[11px] text-muted-foreground/70"><ShieldAlert className="h-3.5 w-3.5" />传导内容仅用于纸面研究，需等待 A 股验证信号。</p>
        </GlassCard>
      )}
    </>
  );
}

export function AqspPaperResearch() {
  const { data, loading, error, refresh } = useAqspSnapshot();
  return (
    <>
      <SnapshotState loading={loading} error={error} snapshot={data} onRefresh={refresh} />
      {data && (
        <GlassCard>
          <div className="mb-4 flex items-start justify-between gap-3"><div><div className="flex items-center gap-2"><FileSearch className="h-5 w-5 text-primary" /><h2 className="font-semibold">纸面候选观察</h2><span className="rounded-full bg-warning/15 px-2 py-0.5 text-[10px] text-warning">只读研究</span></div><p className="mt-1 text-xs text-muted-foreground">按 AQSP 当前快照逐项记录证据、风险卡点和下一观察条件。</p></div><button onClick={refresh} disabled={loading} className="text-muted-foreground hover:text-primary disabled:opacity-50" title="刷新"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} /></button></div>
          <SnapshotMeta snapshot={data} /><FreshnessNotice snapshot={data} />
          {data.candidates.length === 0 ? <div className="mt-5 rounded-lg border border-dashed border-border/70 p-8 text-center text-sm text-muted-foreground">暂无纸面候选。快照可能尚未产出，或当前研究 gate 未放行。</div> : <div className="mt-5 grid gap-3 md:grid-cols-2">{data.candidates.map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} />)}</div>}
          <div className="mt-5 rounded-lg border border-border/50 bg-muted/15 p-3 text-xs leading-relaxed text-muted-foreground"><p className="flex items-center gap-1.5 font-medium text-foreground"><ShieldAlert className="h-3.5 w-3.5 text-warning" />纸面研究边界</p><p className="mt-1">此页仅显示 AQSP 只读快照，研究结果不写入持仓或收益记录。</p></div>
        </GlassCard>
      )}
    </>
  );
}
