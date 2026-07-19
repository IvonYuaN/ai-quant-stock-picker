import {
  AlertCircle,
  ArrowRight,
  Bot,
  CalendarDays,
  Check,
  CircleAlert,
  Clock3,
  ExternalLink,
  FlaskConical,
  MessageSquareText,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  UsersRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { FORMAL_RESEARCH_SECTIONS, TEST_VARIANTS_SECTION_ID } from "@/lib/research-layout";
import type { AqspAgentResult, AqspCandidate, AqspMessage, AqspSnapshot } from "@/lib/api";
import {
  debateProcessText,
  dedupeResearchText,
  formatResearchDate,
  isCurrentEmptyObservation,
  latestReviewDate,
  gatePresentation,
  messageSourceUrl,
  sameResearchText,
  snapshotConclusion,
} from "@/lib/research-view";
import { formatAqspTime, isAqspSnapshotStale, useWorkspaceSnapshot } from "./useAqspSnapshot";

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

function StatusLine({ snapshot }: { snapshot: AqspSnapshot }) {
  const universe = snapshot.universe;
  const phases = snapshot.phases ?? [];
  const coverage = universe?.coverage_pct == null ? "—" : `${(universe.coverage_pct * 100).toFixed(1)}%`;
  return (
    <div className="aqsp-status-line" aria-label="数据概况">
      <span><b>{snapshot.candidates.length}</b>候选</span>
      <span><b>{snapshot.messages.length}</b>消息</span>
      <span><b>{snapshot.debates.length}</b>复核</span>
      <span><b>{coverage}</b>全池周期覆盖</span>
      {phases.length > 0 && <span className="aqsp-status-muted">阶段 {phases.filter((phase) => phase.status !== "未产出").length}/{phases.length}</span>}
    </div>
  );
}

function GateState({ snapshot }: { snapshot: AqspSnapshot }) {
  const gate = snapshot.recommendation_gate;
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

function MarketContext({ snapshot }: { snapshot: AqspSnapshot }) {
  const context = snapshot.market_context;
  if (!context) return <EmptyState title="暂无产业链传导记录" detail="当前消息没有结构化的跨市场或产业链验证信息。" />;
  const lines = unique(context.summary_lines, 4);
  const links = context.cross_market.slice(0, 4);
  return <div className="aqsp-context"><div className="aqsp-subhead"><h3>市场与产业链关联</h3><span>{links.length} 条</span></div>{context.overview && <p className="aqsp-card-summary">{context.overview}</p>}{lines.length > 0 && <ul className="aqsp-context-list">{lines.map((line) => <li key={line}>{line}</li>)}</ul>}{links.length > 0 && <div className="aqsp-link-list">{links.map((link) => <div key={`${link.rule_id}-${link.source_title}`}><b>{link.theme || link.rule_id}</b><span>{link.summary || link.action || "待验证"}</span></div>)}</div>}{context.warnings.length > 0 && <p className="aqsp-warning-text">数据告警：{context.warnings.slice(0, 2).join("；")}</p>}</div>;
}

function DebateCard({ result }: { result: AqspAgentResult }) {
  const process = debateProcessText(result);
  const conclusion = result.conclusion.trim();
  const rounds = dedupeResearchText(result.round_summaries ?? []).filter((item) => !sameResearchText(item, conclusion) && !sameResearchText(item, process)).slice(0, 3);
  return <article className="aqsp-card aqsp-debate-card"><div className="aqsp-card-head"><div className="aqsp-agent-title"><span className="aqsp-agent-mark"><Bot className="h-4 w-4" /></span><div><h3>{result.display_name || result.symbol || "对象未记录"}</h3><span className="aqsp-code">{result.symbol || "代码未记录"}</span></div></div><span className={cn("aqsp-badge", conclusion ? "aqsp-badge-ok" : "aqsp-badge-warn")}>{conclusion ? "已汇总" : "不完整"}</span></div><div className="aqsp-discussion"><p className="aqsp-label"><UsersRound className="h-3.5 w-3.5" />讨论过程</p><p>{process || "过程未记录"}</p>{result.active_roles.length > 0 && <div className="aqsp-tags">{result.active_roles.map((role) => <span className="aqsp-tag" key={role}>{role}</span>)}</div>}{rounds.length > 0 && <ol>{rounds.map((round, index) => <li key={`${index}-${round}`}>第 {index + 1} 轮：{round}</li>)}</ol>}</div><div className="aqsp-votes"><span>支持 <b>{result.bull_count}</b></span><span>保留 <b>{result.neutral_count}</b></span><span>风险 <b>{result.bear_count}</b></span></div><div className="aqsp-debate-conclusion"><p className="aqsp-label">汇总结论</p><strong>{conclusion || "暂无结论"}</strong></div>{(result.primary_risk_gate || result.next_trigger) && <div className="aqsp-debate-foot">{result.primary_risk_gate && <span><ShieldAlert className="h-3.5 w-3.5" />风险：{result.primary_risk_gate}</span>}{result.next_trigger && <span><Sparkles className="h-3.5 w-3.5" />下一验证：{result.next_trigger}</span>}</div>}</article>;
}

function TestVariantsPanel({ snapshot }: { snapshot?: AqspSnapshot }) {
  const historical = snapshot?.meta?.historical ?? false;
  return <section id={TEST_VARIANTS_SECTION_ID} className="aqsp-lab" aria-label="测试与变体"><div className="aqsp-section-head"><div><p className="aqsp-eyebrow"><FlaskConical className="h-3.5 w-3.5" />独立区域</p><h2>测试与变体</h2></div><span>不进入正式结论</span></div><div className="aqsp-lab-snapshot">{snapshot ? <><span>读取快照：{snapshot.selected_date || "日期未记录"}</span><span>生成于：{formatAqspTime(snapshot.generated_at)}</span><span className={cn("aqsp-badge", historical ? "aqsp-badge-warn" : "aqsp-badge-ok")}>{historical ? "历史日期" : "当前数据"}</span></> : <span>等待正式快照</span>}</div><div className="aqsp-lab-grid"><div><b>正式研究</b><span>{snapshot ? "跟随当前选择日期" : "等待快照"}</span></div><div><b>历史回测</b><span>只用于参数验证</span></div><div><b>实验变体</b><span>不改写当前评分</span></div></div></section>;
}

function SectionHead({ number, title, count }: { number: string; title: string; count: string }) {
  return <div className="aqsp-section-head"><div><p className="aqsp-eyebrow">{number}</p><h2>{title}</h2></div><span>{count}</span></div>;
}

function LoadingState() { return <div className="aqsp-state"><RefreshCw className="h-4 w-4 animate-spin text-primary" />正在读取当前研究数据</div>; }
function ErrorState({ error, onRefresh }: { error: string; onRefresh: () => void }) { return <div className="aqsp-state aqsp-state-warn"><AlertCircle className="h-4 w-4 shrink-0" /><span>读取失败：{error}</span><button type="button" onClick={onRefresh} title="重新读取"><RefreshCw className="h-4 w-4" /></button></div>; }

export function AqspResearchWorkspace() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  if (loading && !data) return <><LoadingState /><TestVariantsPanel /></>;
  if (error && !data) return <><ErrorState error={error} onRefresh={refresh} /><TestVariantsPanel /></>;
  if (!data) return <><EmptyState title="当前没有研究快照" detail="等待正式 AQSP 任务产出，当前不显示历史内容。" /><TestVariantsPanel /></>;

  const conclusion = snapshotConclusion(data);
  return <div className="aqsp-page">
    <header className="aqsp-header"><div><p className="aqsp-eyebrow">AQSP · 短线研究</p><div className="aqsp-title-row"><h1>当天研究</h1><strong>{data.selected_date || "日期未记录"}</strong></div><SnapshotMeta snapshot={data} /></div><button type="button" className="aqsp-refresh" onClick={refresh} disabled={loading} title="刷新研究数据"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新</button></header>
    <DatePicker snapshot={data} />
    <div className="aqsp-formal-grid">
      <section id="overview" className="aqsp-module aqsp-module-overview"><SectionHead number={FORMAL_RESEARCH_SECTIONS[0].number} title={FORMAL_RESEARCH_SECTIONS[0].label} count="独立结论" /><div className="aqsp-summary-conclusion"><Sparkles className="h-5 w-5 shrink-0 text-primary" /><div><strong>{conclusion || "当天结论未记录"}</strong>{data.summaries.slice(1, 3).map((line) => <p key={line}>{line}</p>)}</div></div><StatusLine snapshot={data} /><GateState snapshot={data} /><EmptyToday snapshot={data} /></section>
      <section id="messages" className="aqsp-module aqsp-module-messages"><SectionHead number={FORMAL_RESEARCH_SECTIONS[1].number} title={FORMAL_RESEARCH_SECTIONS[1].label} count={`${data.messages.length} 条`} />{data.messages.length === 0 ? <EmptyState title="当天没有有效消息" detail="没有可核验来源时，系统不补写消息或产业链推断。" /> : <div className="aqsp-list">{data.messages.map((message, index) => <MessageCard key={`${message.title}-${message.published_at}-${index}`} message={message} />)}</div>}<MarketContext snapshot={data} /></section>
      <section id="candidates" className="aqsp-module aqsp-module-candidates"><SectionHead number={FORMAL_RESEARCH_SECTIONS[2].number} title={FORMAL_RESEARCH_SECTIONS[2].label} count={`${data.candidates.length} 个`} />{data.candidates.length === 0 ? <EmptyState title="当天没有候选" detail="当前没有通过数据质量与短线筛选的对象，不用历史候选填充。" /> : <div className="aqsp-list">{data.candidates.map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} />)}</div>}</section>
      <section id="discussion" className="aqsp-module aqsp-module-discussion"><SectionHead number={FORMAL_RESEARCH_SECTIONS[3].number} title={FORMAL_RESEARCH_SECTIONS[3].label} count={`${data.debates.length} 条`} />{data.debates.length === 0 ? <EmptyState title="当天没有有效讨论" detail="没有可核验的分歧和风险条件时，不显示推断内容。" /> : <div className="aqsp-list">{data.debates.map((result) => <DebateCard key={result.symbol} result={result} />)}</div>}</section>
    </div>
    <TestVariantsPanel snapshot={data} />
  </div>;
}

export function AqspDailySnapshot() { return <AqspResearchWorkspace />; }
