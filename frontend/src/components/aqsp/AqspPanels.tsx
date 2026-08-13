import {
  AlertCircle,
  ArrowRight,
  CalendarDays,
  Check,
  CircleAlert,
  Clock3,
  ExternalLink,
  FlaskConical,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { TEST_VARIANTS_SECTION_ID } from "@/lib/research-layout";
import type { AqspAgentResult, AqspCandidate, AqspMessage, AqspPhase, AqspSnapshot, AqspVariant } from "@/lib/api";
import {
  formatResearchDate,
  isCurrentEmptyObservation,
  latestReviewDate,
  gatePresentation,
  messageSourceUrl,
  sameResearchText,
} from "@/lib/research-view";
import { formatAqspTime, isAqspSnapshotStale, useWorkspaceSnapshot } from "./useAqspSnapshot";
import { useLocation } from "react-router-dom";
import { variantHoldingsLabel, variantMoney, variantPercent, variantStrategyText } from "@/lib/variant-view";

function unique(values: readonly string[] | undefined, limit = 4): string[] {
  return Array.from(new Set((values ?? []).map((value) => value.trim()).filter(Boolean))).slice(0, limit);
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="aqsp-empty" role="status">
      <CircleAlert className="h-4 w-4 shrink-0" />
      <div><strong>{title}</strong><p>{detail}</p></div>
    </div>
  );
}

function SnapshotMeta({ snapshot }: { snapshot: AqspSnapshot }) {
  const stale = isAqspSnapshotStale(snapshot);
  const historical = snapshot.meta?.historical ?? false;
  const freshness = snapshot.meta?.freshness;
  return (
    <div className="aqsp-meta">
      <span>{snapshot.selected_date || "日期未记录"}</span>
      <span>更新 {formatAqspTime(snapshot.generated_at)}</span>
      <span className={cn("aqsp-badge", historical || stale ? "aqsp-badge-warn" : "aqsp-badge-ok")}>
        {historical ? "历史日期" : stale ? "当前快照已过期" : "当前数据"}
      </span>
      {freshness?.candidates === "fresh" && <span className="aqsp-badge aqsp-badge-ok">行情新鲜</span>}
      {freshness?.messages === "stale" && <span className="aqsp-badge aqsp-badge-warn">消息滞后</span>}
    </div>
  );
}

function DatePicker({ snapshot }: { snapshot: AqspSnapshot }) {
  const { loading, selectedDate, selectDate } = useWorkspaceSnapshot();
  const activeDate = selectedDate || snapshot.selected_date;
  return (
    <div className="aqsp-date-picker" aria-label="研究日期">
      <CalendarDays className="h-4 w-4 shrink-0 text-primary" />
      <span className="aqsp-date-label">研究日期</span>
      <div className="aqsp-date-list">
        {snapshot.available_dates.map((date) => {
          const label = formatResearchDate(date);
          const active = date === activeDate;
          return (
            <button key={date} type="button" className={cn("aqsp-date", active && "aqsp-date-active")} onClick={() => selectDate(date)} disabled={loading && active} aria-pressed={active}>
              <b>{label.day}</b><span>{label.weekday}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function EmptyToday({ snapshot }: { snapshot: AqspSnapshot }) {
  const { selectDate } = useWorkspaceSnapshot();
  if (!isCurrentEmptyObservation(snapshot)) return null;
  const previous = latestReviewDate(snapshot);
  return (
    <div className="aqsp-observation" role="status">
      <Clock3 className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <strong>当天暂无实时产物</strong>
        <p>当前页面不使用历史数据代替当天候选。下一次任务产出后会更新这里。</p>
        {previous && <button type="button" onClick={() => selectDate(previous)}>查看最近日期：{formatResearchDate(previous).day}</button>}
      </div>
    </div>
  );
}

const MARKET_PHASES = [
  { id: "pre", label: "盘前", keywords: ["盘前", "pre_market", "pre-market"] },
  { id: "intraday", label: "盘中", keywords: ["盘中", "intraday"] },
  { id: "post", label: "盘后", keywords: ["盘后", "post_market", "post-market"] },
] as const;

function phaseForLabel(phase: AqspPhase, keywords: readonly string[]) {
  const text = `${phase.task_id} ${phase.label}`.toLowerCase();
  return keywords.some((keyword) => text.includes(keyword.toLowerCase())) ? phase : undefined;
}

function PhaseConclusions({ snapshot }: { snapshot: AqspSnapshot }) {
  const phases = snapshot.phases ?? [];
  return <div className="aqsp-phase-conclusions" aria-label="当天分阶段结论">{MARKET_PHASES.map((phase) => {
    const record = phases.find((item) => phaseForLabel(item, phase.keywords));
    const summary = snapshot.summaries.find((line) => line.startsWith(`${phase.label}：`)) || `${phase.label}：未记录`;
    return <article key={phase.id}><header><b>{phase.label}</b><span>{record?.status || "未产出"}</span></header><p>{summary.replace(`${phase.label}：`, "")}</p></article>;
  })}</div>;
}

function ReviewSummary({ result }: { result: AqspAgentResult }) {
  return <article className="aqsp-review-summary">
    <div><b>{result.display_name || result.symbol || "对象未记录"}</b><span>{result.symbol}</span></div>
    <p>{result.conclusion.trim() || "复核未形成可发布结论。"}</p>
    <footer>{result.primary_risk_gate && <span>风险：{result.primary_risk_gate}</span>}{result.next_trigger && <span>验证：{result.next_trigger}</span>}</footer>
  </article>;
}

function DailyReport({ snapshot }: { snapshot: AqspSnapshot }) {
  return <section id="overview" className="aqsp-daily-report">
    <SectionHead number="01" title="当天复盘" count="实时研究" />
    <PhaseConclusions snapshot={snapshot} />
    <div className="aqsp-report-section"><div className="aqsp-report-heading"><h3>候选与规则依据</h3><span>{snapshot.candidates.length} 个</span></div>{snapshot.candidates.length === 0 ? <EmptyState title="当天没有候选" detail="当前没有通过数据质量与筛选条件的对象，不使用历史结果填充。" /> : <div className="aqsp-list">{snapshot.candidates.slice(0, 3).map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} />)}</div>}</div>
    <div className="aqsp-report-section"><div className="aqsp-report-heading"><h3>复核结论</h3><span>{snapshot.debates.length} 条</span></div>{snapshot.debates.length === 0 ? <EmptyState title="当天没有有效复核" detail="未形成独立分歧和风险条件的讨论不会作为结论展示。" /> : <div className="aqsp-review-list">{snapshot.debates.slice(0, 3).map((result) => <ReviewSummary key={result.symbol} result={result} />)}</div>}</div>
    <div className="aqsp-report-section"><div className="aqsp-report-heading"><h3>当日消息证据</h3><span>{snapshot.messages.length} 条</span></div>{snapshot.messages.length === 0 ? <EmptyState title="当天没有有效消息" detail="没有可核验来源时，系统不补写消息推断。" /> : <div className="aqsp-list">{snapshot.messages.slice(0, 2).map((message, index) => <MessageCard key={`${message.title}-${message.published_at}-${index}`} message={message} />)}</div>}</div>
    <GateState snapshot={snapshot} />
    <EmptyToday snapshot={snapshot} />
  </section>;
}

function GateState({ snapshot }: { snapshot: AqspSnapshot }) {
  const gate = snapshot.recommendation_gate;
  if (snapshot.candidates.length === 0) {
    return <div className="aqsp-gate aqsp-gate-warn"><Clock3 className="h-4 w-4 shrink-0" /><span>当天暂无候选，等待盘前或盘中任务产出；不使用历史结果替代。</span></div>;
  }
  const presentation = gatePresentation(gate);
  if (presentation === "ready") {
    return <div className="aqsp-gate aqsp-gate-ok"><Check className="h-4 w-4 shrink-0" /><span>当前结果可进入纸面复核，不自动下单。</span></div>;
  }
  if (presentation === "unavailable") {
    return <div className="aqsp-gate aqsp-gate-warn"><ShieldAlert className="h-4 w-4 shrink-0" /><span>推荐状态未记录，当前只显示可核验数据。</span></div>;
  }
  const reason = gate?.reasons[0] ?? "当前结果未放行";
  const label = reason.startsWith("freshness_not_ready") ? "实时数据新鲜度未达标" : reason.startsWith("circuit_breaker") ? "组合保护处于冷却状态" : "当前结果仅供观察";
  return <div className="aqsp-gate aqsp-gate-warn"><ShieldAlert className="h-4 w-4 shrink-0" /><span>{label}。当前为研究展示，不进入正式推荐或纸面复核。</span></div>;
}

function CandidateCard({ candidate }: { candidate: AqspCandidate }) {
  return (
    <article className="aqsp-card">
      <div className="aqsp-card-head">
        <div><h3>{candidate.display_name || "名称未记录"}</h3><span className="aqsp-code">{candidate.symbol || "代码未记录"}</span></div>
        <div className="aqsp-score"><b>{Number.isFinite(candidate.score) ? candidate.score.toFixed(1) : "—"}</b><span>评分</span></div>
      </div>
      <div className="aqsp-tags"><span className="aqsp-tag aqsp-tag-primary">{candidate.research_status || "状态未记录"}</span><span className="aqsp-tag">{candidate.evidence_status || "证据未记录"}</span></div>
      {candidate.context && <p className="aqsp-card-summary">{candidate.context}</p>}
      {(candidate.technical_metrics ?? []).length > 0 && <div className="aqsp-metrics">{candidate.technical_metrics?.map((metric) => <div key={metric.key}><span>{metric.label}</span><b>{metric.value}</b></div>)}</div>}
      {(candidate.score_breakdown ?? []).length > 0 && <p className="aqsp-score-breakdown"><b>评分依据</b>{candidate.score_breakdown?.slice(0, 4).join(" · ")}</p>}
      {candidate.deterministic_reasons.length > 0 && <ul className="aqsp-reasons">{candidate.deterministic_reasons.slice(0, 3).map((reason) => <li key={reason}><Check className="h-3.5 w-3.5 shrink-0 text-success" />{reason}</li>)}</ul>}
      {candidate.next_step && <p className="aqsp-next"><ArrowRight className="h-3.5 w-3.5 shrink-0" />下一观察：{candidate.next_step}</p>}
      {(candidate.data_source || candidate.freshness) && <p className="aqsp-provenance">数据源：{candidate.data_source || "未记录"} · {candidate.freshness || "新鲜度未记录"}</p>}
    </article>
  );
}

function MessageCard({ message }: { message: AqspMessage }) {
  const sectors = unique(message.affected_sectors, 4);
  const path = unique(message.transmission_path, 4);
  const sourceUrl = messageSourceUrl(message);
  const summary = sameResearchText(message.title, message.summary) ? "" : message.summary;
  return (
    <article className="aqsp-card aqsp-message-card">
      <div className="aqsp-message-top"><div className="aqsp-tags"><span className="aqsp-tag aqsp-tag-primary">{message.category || "消息"}</span>{message.event_type && <span className="aqsp-tag">{message.event_type}</span>}{message.impact && <span className={cn("aqsp-tag", message.impact === "利空" ? "aqsp-tag-bad" : message.impact === "利好" ? "aqsp-tag-good" : "")}>{message.impact}</span>}</div><time>{formatAqspTime(message.published_at)}</time></div>
      <h3 className="aqsp-message-title"><MessageSquareText className="h-4 w-4 shrink-0 text-primary" />{message.title || "消息标题未记录"}</h3>
      {summary && <p className="aqsp-card-summary">{summary}</p>}
      {sectors.length > 0 && <p className="aqsp-inline"><b>影响板块</b>{sectors.join(" · ")}</p>}
      {(path.length > 0 || message.transmission_hypothesis) && <div className="aqsp-transmission"><b>产业链传导</b>{path.length > 0 && <p>{path.join(" → ")}</p>}{message.transmission_hypothesis && <span>{message.transmission_hypothesis}</span>}</div>}
      {message.validation_signals?.length ? <p className="aqsp-signal"><b>确认</b>{unique(message.validation_signals, 2).join("；")}</p> : null}
      {message.invalidation_signals?.length ? <p className="aqsp-signal aqsp-signal-warn"><b>失效</b>{unique(message.invalidation_signals, 2).join("；")}</p> : null}
      {sourceUrl && <a className="aqsp-source" href={sourceUrl} target="_blank" rel="noreferrer"><ExternalLink className="h-3.5 w-3.5" />查看来源{message.source ? ` · ${message.source}` : ""}</a>}
    </article>
  );
}

function TestVariantsPanel({ snapshot }: { snapshot?: AqspSnapshot }) {
  const historical = snapshot?.meta?.historical ?? false;
  const variants = snapshot?.variants ?? [];
  const variantHistory = variants.some((variant) => variant.data_mode.includes("historical"));
  return <section id={TEST_VARIANTS_SECTION_ID} className="aqsp-lab" aria-label="测试与变体">
    <div className="aqsp-section-head"><div><p className="aqsp-eyebrow"><FlaskConical className="h-3.5 w-3.5" />独立区域</p><h2>测试与变体</h2></div><span>不进入正式结论</span></div>
    <div className="aqsp-lab-snapshot">{snapshot ? <><span>数据区间：{variants[0]?.start_date || "—"} 至 {variants[0]?.end_date || "—"}</span><span>每套账户：100,000 元</span><span className={cn("aqsp-badge", historical || variantHistory ? "aqsp-badge-warn" : "aqsp-badge-ok")}>{historical || variantHistory ? "历史回测 · 仅验证" : "当前实验结果"}</span></> : <span>等待正式快照</span>}</div>
    {variants.length === 0 ? <EmptyState title="变体结果尚未产出" detail="实验结果独立于正式候选，产出后会显示在这里。" /> : <div className="aqsp-variant-grid">{variants.map((variant: AqspVariant) => {
      const pnl = variant.total_pnl;
      const holdings = variant.holdings;
      return <article className="aqsp-variant-card" key={variant.variant_id}>
        <div className="aqsp-variant-head"><div><h3>{variant.label || variant.variant_id}</h3><span>{variant.variant_id}{variant.rank ? ` · 回测第 ${variant.rank} 名` : ""}</span></div><strong className={pnl == null || pnl >= 0 ? "aqsp-variant-positive" : "aqsp-variant-negative"}>{variantMoney(pnl)}</strong></div>
        <p className="aqsp-variant-strategy"><b>交易策略</b>{variantStrategyText(variant.strategy, variant.variant_id)}</p>
        <div className="aqsp-variant-account">
          <div><span>初始资金</span><b>{variantMoney(variant.initial_cash)}</b></div>
          <div><span>现金</span><b>{variantMoney(variant.cash)}</b></div>
          <div><span>账户权益</span><b>{variantMoney(variant.final_equity)}</b></div>
          <div><span>总盈亏</span><b className={pnl != null && pnl < 0 ? "aqsp-variant-negative" : "aqsp-variant-positive"}>{variantMoney(pnl)}</b></div>
          <div><span>收益率</span><b>{variantPercent(variant.return_pct)}</b></div>
        </div>
        <div className="aqsp-variant-holdings"><b>持仓 · {variantHoldingsLabel(holdings)}</b>{holdings?.map((holding) => <span key={holding.symbol}>{holding.symbol} {holding.quantity} 股 · 市值 {variantMoney(holding.market_value)} · 浮盈 {variantMoney(holding.unrealized_pnl)}</span>)}</div>
        <p className="aqsp-variant-rules">成交 {variant.filled_orders} · 拒绝 {variant.rejected_orders} · {(variant.hard_rules ?? []).join(" · ") || "硬成交规则未记录"}</p>
      </article>;
    })}</div>}
  </section>;
}

function SectionHead({ number, title, count }: { number: string; title: string; count: string }) {
  return <div className="aqsp-section-head"><div><p className="aqsp-eyebrow">{number}</p><h2>{title}</h2></div><span>{count}</span></div>;
}

function LoadingState() { return <div className="aqsp-state"><RefreshCw className="h-4 w-4 animate-spin text-primary" />正在读取当前研究数据</div>; }
function ErrorState({ error, onRefresh }: { error: string; onRefresh: () => void }) { return <div className="aqsp-state aqsp-state-warn"><AlertCircle className="h-4 w-4 shrink-0" /><span>读取失败：{error}</span><button type="button" onClick={onRefresh} title="重新读取"><RefreshCw className="h-4 w-4" /></button></div>; }

export function AqspResearchWorkspace() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  const { hash } = useLocation();
  const showVariants = hash === `#${TEST_VARIANTS_SECTION_ID}`;
  if (loading && !data) return <div className="aqsp-page">{showVariants ? <TestVariantsPanel /> : <LoadingState />}</div>;
  if (error && !data) return <div className="aqsp-page">{showVariants ? <TestVariantsPanel /> : <ErrorState error={error} onRefresh={refresh} />}</div>;
  if (!data) return <div className="aqsp-page">{showVariants ? <TestVariantsPanel /> : <EmptyState title="当前没有研究快照" detail="等待正式 AQSP 任务产出，当前不显示历史内容。" />}</div>;
  return <div className="aqsp-page">
    <header className="aqsp-header"><div><p className="aqsp-eyebrow">AQSP · 短线研究</p><div className="aqsp-title-row"><h1>当天研究</h1><strong>{data.selected_date || "日期未记录"}</strong></div><SnapshotMeta snapshot={data} /></div><button type="button" className="aqsp-refresh" onClick={refresh} disabled={loading} title="刷新研究数据"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新</button></header>
    <DatePicker snapshot={data} />
    <div className="aqsp-formal-grid">
      <main className="aqsp-active-view" aria-live="polite">
        {showVariants ? <TestVariantsPanel snapshot={data} /> : <DailyReport snapshot={data} />}
      </main>
    </div>
  </div>;
}

export function AqspDailySnapshot() { return <AqspResearchWorkspace />; }
