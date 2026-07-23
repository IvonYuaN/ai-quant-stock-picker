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
import { FORMAL_RESEARCH_SECTIONS, resolveResearchView, TEST_VARIANTS_SECTION_ID, type ResearchViewId } from "@/lib/research-layout";
import type { AqspAgentResult, AqspCandidate, AqspMessage, AqspPhase, AqspSnapshot, AqspVariant } from "@/lib/api";
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
import { useLocation } from "react-router-dom";
import { variantHoldingName, variantMoney, variantPercent, variantStrategyLogic } from "@/lib/variant-view";

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
      {phases.length > 0 && <span className="aqsp-status-muted">阶段 {phases.filter((phase) => ["已产出", "消息已更新"].includes(phase.status)).length}/{phases.length}</span>}
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

function PhaseLane({ snapshot }: { snapshot: AqspSnapshot }) {
  const phases = snapshot.phases ?? [];
  const premarketNewsReady = snapshot.messages.length > 0 && !["失败", "无可用消息", "历史消息已排除"].includes(snapshot.message_status);
  return (
    <div className="aqsp-phase-lane" aria-label="盘前盘中盘后数据状态">
        {MARKET_PHASES.map((phase) => {
        const record = phases.find((item) => phaseForLabel(item, phase.keywords));
        const newsStatus = phase.id === "pre" && premarketNewsReady ? (record?.status === "消息已更新" ? "消息已更新" : "消息已产出") : "未产出";
        const outputRecord = record && record.candidate_count > 0 ? record : undefined;
        const status = outputRecord?.status || newsStatus;
        return (
          <div className="aqsp-phase" key={phase.id}>
            <div><b>{phase.label}</b><span>{status}</span></div>
            {outputRecord ? <small>候选 {outputRecord.candidate_count} · 批次重叠 {outputRecord.overlap_symbols}</small> : phase.id === "pre" && premarketNewsReady ? <small>{snapshot.messages.length} 条消息已进入今日证据区</small> : record?.status === "待复盘" ? <small>{record.candidate_count} 只候选等待收盘复核</small> : <small>独立数据段，未与当天结果合并</small>}
          </div>
        );
      })}
    </div>
  );
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
      <p className="aqsp-provenance">数据源：{candidate.data_source || "未记录"} · 行情新鲜度：{candidate.freshness || "未记录"}</p>
      {(candidate.technical_metrics ?? []).length === 0 && <p className="aqsp-warning-text">技术指标未记录，暂不作技术结论</p>}
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
  const bucketLabels: Record<string, string> = { bullish: "看多证据", bearish: "看空证据", event_fundamental: "事件/基本面", technical: "技术证据", risk_counterevidence: "风险/反证", uncertainty: "不确定性" };
  const buckets = Object.entries(result.viewpoint_buckets ?? {}).filter(([, points]) => points.length > 0).slice(0, 6);
  return <article className="aqsp-card aqsp-debate-card"><div className="aqsp-card-head"><div className="aqsp-agent-title"><span className="aqsp-agent-mark"><Bot className="h-4 w-4" /></span><div><h3>{result.display_name || result.symbol || "对象未记录"}</h3><span className="aqsp-code">{result.symbol || "代码未记录"}</span></div></div><span className={cn("aqsp-badge", conclusion ? "aqsp-badge-ok" : "aqsp-badge-warn")}>{conclusion ? "已汇总" : "不完整"}</span></div><div className="aqsp-discussion"><p className="aqsp-label"><UsersRound className="h-3.5 w-3.5" />讨论过程</p><p>{process || "过程未记录"}</p>{result.active_roles.length > 0 && <div className="aqsp-tags">{result.active_roles.map((role) => <span className="aqsp-tag" key={role}>{role}</span>)}</div>}{rounds.length > 0 && <ol>{rounds.map((round, index) => <li key={`${index}-${round}`}>第 {index + 1} 轮：{round}</li>)}</ol>}</div>{buckets.length > 0 && <div className="aqsp-viewpoint-buckets"><p className="aqsp-label">独立证据</p>{buckets.map(([bucket, points]) => <div key={bucket}><b>{bucketLabels[bucket] || bucket}</b><span>{points.slice(0, 2).join("；")}</span></div>)}</div>}<div className="aqsp-votes"><span>支持 <b>{result.bull_count}</b></span><span>保留 <b>{result.neutral_count}</b></span><span>风险 <b>{result.bear_count}</b></span></div>{(result.disagreement_points?.length || result.uncertainty_points?.length) ? <div className="aqsp-debate-foot">{result.disagreement_points?.slice(0, 2).map((item) => <span key={item}><CircleAlert className="h-3.5 w-3.5" />分歧：{item}</span>)}{result.uncertainty_points?.slice(0, 2).map((item) => <span key={item}><CircleAlert className="h-3.5 w-3.5" />不确定：{item}</span>)}</div> : null}<div className="aqsp-debate-conclusion"><p className="aqsp-label">汇总结论</p><strong>{conclusion || "暂无结论"}</strong></div>{(result.primary_risk_gate || result.next_trigger) && <div className="aqsp-debate-foot">{result.primary_risk_gate && <span><ShieldAlert className="h-3.5 w-3.5" />风险：{result.primary_risk_gate}</span>}{result.next_trigger && <span><Sparkles className="h-3.5 w-3.5" />下一验证：{result.next_trigger}</span>}</div>}</article>;
}

function VariantHoldingList({
  label,
  holdings,
  date,
  candidateNames,
}: {
  label: string;
  holdings: AqspVariant["holdings"] | null;
  date?: string;
  candidateNames: ReadonlyMap<string, string>;
}) {
  const count = holdings == null ? "未记录" : holdings.length === 0 ? "空仓" : `${holdings.length} 只`;
  return <div className="aqsp-variant-position"><div className="aqsp-variant-position-head"><b>{label}</b><span>{date ? `${date} · ` : ""}{count}</span></div>{holdings == null ? <p className="aqsp-variant-muted">{label.includes("昨日") || label.includes("前一") ? "对比持仓未记录，无法比较换票" : "当前持仓未记录"}</p> : holdings.length === 0 ? <p className="aqsp-variant-muted">明确空仓</p> : <div className="aqsp-variant-position-list">{holdings.map((holding, index) => <div key={holding.symbol}><div className="aqsp-holding-identity"><span className={cn("aqsp-holding-rank", index === 0 ? "aqsp-holding-rank-primary" : "")}>{index === 0 ? "主仓" : "次仓"}</span><strong>{variantHoldingName(holding, candidateNames)}</strong><span className="aqsp-code">{holding.symbol}</span></div><span className="aqsp-holding-metrics">{holding.quantity} 股 · 市值 {variantMoney(holding.market_value)} · 浮盈 {variantMoney(holding.unrealized_pnl)}</span></div>)}</div>}</div>;
}

function TestVariantsPanel({ snapshot }: { snapshot?: AqspSnapshot }) {
  const historical = snapshot?.meta?.historical ?? false;
  const variants = snapshot?.variants ?? [];
  const variantHistory = variants.some((variant) => variant.data_mode.includes("historical"));
  const candidateNames = new Map((snapshot?.candidates ?? []).map((candidate) => [candidate.symbol, candidate.display_name]));
  const uniquePortfolioCount = new Set(variants.map((variant) => (variant.holdings ?? []).map((holding) => holding.symbol).sort().join(","))).size;
  const variantUniverse = snapshot?.variant_universe;
  const coverage = variantUniverse?.coverage_pct == null ? "—" : `${variantUniverse.coverage_pct.toFixed(1)}%`;
  const sources = variantUniverse?.sources?.join("、") || "未记录";
  return <section id={TEST_VARIANTS_SECTION_ID} className="aqsp-lab" aria-label="测试与变体">
    <div className="aqsp-section-head"><div><p className="aqsp-eyebrow"><FlaskConical className="h-3.5 w-3.5" />独立区域</p><h2>测试与变体</h2></div><span>{variants.length} 个变体 · {uniquePortfolioCount} 种末端持仓</span></div>
    <div className="aqsp-lab-snapshot">{snapshot ? <><span>数据区间：{variants[0]?.start_date || "—"} 至 {variants[0]?.end_date || "—"}</span><span>每套账户：100,000 元</span><span>样本池：{variantUniverse?.symbol_count || "—"} 只</span><span>{variantUniverse?.board_scope || "板块范围未记录"}</span><span>排除：{variantUniverse?.excluded?.join("、") || "未记录"}</span><span>覆盖率：{coverage}</span><span>来源：{sources}</span><span className={cn("aqsp-badge", historical || variantHistory ? "aqsp-badge-warn" : "aqsp-badge-ok")}>{historical || variantHistory ? "历史回测 · 仅验证" : "当前实验结果"}</span></> : <span>等待正式快照</span>}</div>
    {variants.length === 0 ? <EmptyState title="变体结果尚未产出" detail="实验结果独立于正式候选，产出后会显示在这里。" /> : <div className="aqsp-variant-grid">{variants.map((variant: AqspVariant) => {
      const pnl = variant.total_pnl;
      const holdings = variant.holdings;
      const adjustmentLines = variant.adjustments?.length
        ? variant.adjustments.map((adjustment) => {
            const action = { added: "调入", removed: "调出", increased: "加仓", decreased: "减仓", continued: "继续持有" }[adjustment.action] || adjustment.action;
            const evidence = adjustment.evidence.length > 0 ? `：${adjustment.evidence.join("；")}` : "（成交证据未记录）";
            return `${action} ${adjustment.name || adjustment.symbol}（${adjustment.symbol}）${evidence}`;
          })
        : variant.recent_actions?.length
          ? variant.recent_actions.map((action) => `成交记录：${action}`)
          : ["换票原因未记录，不能仅凭前后持仓推断"];
      return <article className="aqsp-variant-card" key={variant.variant_id}>
        <div className="aqsp-variant-head"><div><h3>{variant.label || "未命名变体"}</h3><span>{variant.rank ? `回测第 ${variant.rank} 名` : "独立纸面账户"}</span></div><strong className={pnl == null || pnl >= 0 ? "aqsp-variant-positive" : "aqsp-variant-negative"}>{variantMoney(pnl)}</strong></div>
        <div className="aqsp-variant-position-grid">
          <VariantHoldingList label={variantHistory ? "回测末日持仓" : "今日纸面持有"} holdings={holdings} date={variant.holdings_date} candidateNames={candidateNames} />
          <VariantHoldingList label={variantHistory ? "前一交易日持仓" : "昨日纸面持有"} holdings={variant.previous_holdings} date={variant.previous_holdings_date} candidateNames={candidateNames} />
        </div>
        <div className="aqsp-variant-reason"><div className="aqsp-variant-position-head"><b>为什么换票</b><span>{variant.adjustments?.length ? "持仓差异" : variant.recent_actions?.length ? "实际成交证据" : "无最近成交"}</span></div>{adjustmentLines.length > 0 ? <ul className="aqsp-variant-change-list">{adjustmentLines.map((line, index) => <li key={`${index}-${line}`}>{line}</li>)}</ul> : <p className="aqsp-variant-muted">没有成交动作，持仓按原策略继续或明确空仓</p>}<p className="aqsp-variant-footnote">成交 {variant.filled_orders} · 拒绝 {variant.rejected_orders} · 仅纸面验证，不自动下单</p></div>
        <p className="aqsp-variant-strategy"><b>策略逻辑</b>{variantStrategyLogic(variant.strategy, variant.variant_id)}</p>
        <div className="aqsp-variant-account">
          <div><span>账户权益</span><b>{variantMoney(variant.final_equity)}</b></div>
          <div><span>现金</span><b>{variantMoney(variant.cash)}</b></div>
          <div><span>收益率</span><b>{variantPercent(variant.return_pct)}</b></div>
          <div><span>总盈亏</span><b className={pnl != null && pnl < 0 ? "aqsp-variant-negative" : "aqsp-variant-positive"}>{variantMoney(pnl)}</b></div>
        </div>
      </article>;
    })}</div>}
  </section>;
}

function OverviewVariantFallback({ snapshot }: { snapshot: AqspSnapshot }) {
  if (snapshot.candidates.length > 0 || !snapshot.variants?.length) return null;
  return <div className="aqsp-overview-variants"><TestVariantsPanel snapshot={snapshot} /></div>;
}

function SectionHead({ number, title, count }: { number: string; title: string; count: string }) {
  return <div className="aqsp-section-head"><div><p className="aqsp-eyebrow">{number}</p><h2>{title}</h2></div><span>{count}</span></div>;
}

function LoadingState() { return <div className="aqsp-state"><RefreshCw className="h-4 w-4 animate-spin text-primary" />正在读取当前研究数据</div>; }
function ErrorState({ error, onRefresh }: { error: string; onRefresh: () => void }) { return <div className="aqsp-state aqsp-state-warn"><AlertCircle className="h-4 w-4 shrink-0" /><span>读取失败：{error}</span><button type="button" onClick={onRefresh} title="重新读取"><RefreshCw className="h-4 w-4" /></button></div>; }

export function AqspResearchWorkspace() {
  const { data, loading, error, refresh } = useWorkspaceSnapshot();
  const { hash } = useLocation();
  const activeView: ResearchViewId = resolveResearchView(hash);
  if (loading && !data) return <div className="aqsp-page">{activeView === TEST_VARIANTS_SECTION_ID ? <TestVariantsPanel /> : <LoadingState />}</div>;
  if (error && !data) return <div className="aqsp-page">{activeView === TEST_VARIANTS_SECTION_ID ? <TestVariantsPanel /> : <ErrorState error={error} onRefresh={refresh} />}</div>;
  if (!data) return <div className="aqsp-page">{activeView === TEST_VARIANTS_SECTION_ID ? <TestVariantsPanel /> : <EmptyState title="当前没有研究快照" detail="等待正式 AQSP 任务产出，当前不显示历史内容。" />}</div>;

  const conclusion = snapshotConclusion(data);
  const formalSections = {
    overview: <section id="overview" className="aqsp-module aqsp-module-overview"><SectionHead number={FORMAL_RESEARCH_SECTIONS[0].number} title={FORMAL_RESEARCH_SECTIONS[0].label} count="独立结论" /><div className="aqsp-summary-conclusion"><Sparkles className="h-5 w-5 shrink-0 text-primary" /><div><strong>{conclusion || "当天结论未记录"}</strong>{data.summaries.slice(1, 3).map((line) => <p key={line}>{line}</p>)}</div></div><StatusLine snapshot={data} /><PhaseLane snapshot={data} /><GateState snapshot={data} /><EmptyToday snapshot={data} /><OverviewVariantFallback snapshot={data} /></section>,
    messages: <section id="messages" className="aqsp-module aqsp-module-messages"><SectionHead number={FORMAL_RESEARCH_SECTIONS[1].number} title={FORMAL_RESEARCH_SECTIONS[1].label} count={`${data.messages.length} 条`} />{data.messages.length === 0 ? <EmptyState title="当天没有有效消息" detail="没有可核验来源时，系统不补写消息或产业链推断。" /> : <div className="aqsp-list">{data.messages.map((message, index) => <MessageCard key={`${message.title}-${message.published_at}-${index}`} message={message} />)}</div>}<MarketContext snapshot={data} /></section>,
    candidates: <section id="candidates" className="aqsp-module aqsp-module-candidates"><SectionHead number={FORMAL_RESEARCH_SECTIONS[2].number} title={FORMAL_RESEARCH_SECTIONS[2].label} count={`${data.candidates.length} 个`} />{data.candidates.length === 0 ? <EmptyState title="当天没有候选" detail="当前没有通过数据质量与短线筛选的对象，不用历史候选填充。" /> : <div className="aqsp-list">{data.candidates.map((candidate) => <CandidateCard key={candidate.symbol} candidate={candidate} />)}</div>}</section>,
    discussion: <section id="discussion" className="aqsp-module aqsp-module-discussion"><SectionHead number={FORMAL_RESEARCH_SECTIONS[3].number} title={FORMAL_RESEARCH_SECTIONS[3].label} count={`${data.debates.length} 条`} />{data.debates.length === 0 ? <EmptyState title="当天没有有效讨论" detail="没有可核验的分歧和风险条件时，不显示推断内容。" /> : <div className="aqsp-list">{data.debates.map((result) => <DebateCard key={result.symbol} result={result} />)}</div>}</section>,
  } as const;
  return <div className="aqsp-page">
    <header className="aqsp-header"><div><p className="aqsp-eyebrow">AQSP · 短线研究</p><div className="aqsp-title-row"><h1>当天研究</h1><strong>{data.selected_date || "日期未记录"}</strong></div><SnapshotMeta snapshot={data} /></div><button type="button" className="aqsp-refresh" onClick={refresh} disabled={loading} title="刷新研究数据"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新</button></header>
    <DatePicker snapshot={data} />
    <div className="aqsp-formal-grid">
      <main className="aqsp-active-view" aria-live="polite">
      {activeView === TEST_VARIANTS_SECTION_ID ? <TestVariantsPanel snapshot={data} /> : formalSections[activeView]}
      </main>
    </div>
  </div>;
}

export function AqspDailySnapshot() { return <AqspResearchWorkspace />; }
