import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { BookOpen, FileText, NotebookPen, RefreshCw } from "lucide-react";
import { ClosingReviewPanel } from "@/components/aqsp/ClosingReviewPanel";
import { Badge, EmptyState, LoadingState, StatePanel, Tag } from "@/components/ui/primitives";
import {
  api,
  type CandidateDecision,
  type CandidateObservation,
  type CandidateObservationReviewInput,
  type DecisionHistoryPayload,
  type ReviewRecord,
  type SignalReviewSource,
  type SignalReviewPayload,
  type TradeJournal,
} from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";

const OUTCOME_LABELS = {
  condition_confirmed: "观察条件得到确认",
  condition_invalidated: "观察条件已失效",
  not_reached: "期限内未到观察条件",
  unclear: "结果暂不明确",
} as const;

const QUALITY_LABELS = {
  sound: "过程合理",
  mixed: "部分合理",
  weak: "过程有偏差",
  unrated: "暂不评价",
} as const;

function DecisionHistoryIndex({ decisions }: { decisions: CandidateDecision[] }) {
  const [payload, setPayload] = useState<DecisionHistoryPayload | null>(null);
  const [symbol, setSymbol] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loadedFilter, setLoadedFilter] = useState<string | null>(null);
  const requestSequence = useRef(0);
  const filterKey = `${symbol.trim()}\u001f${kind}\u001f${status.trim()}`;

  const load = useCallback(() => {
    const sequence = ++requestSequence.current;
    const query = new URLSearchParams();
    if (symbol.trim()) query.set("symbol", symbol.trim());
    if (kind) query.set("kind", kind);
    if (status.trim()) query.set("status", status.trim());
    setLoading(true);
    setError("");
    api.decisionHistory(query.toString() ? `?${query.toString()}` : "")
      .then((result) => {
        if (requestSequence.current !== sequence) return;
        setPayload(result);
        setLoadedFilter(filterKey);
      })
      .catch((reason) => {
        if (requestSequence.current === sequence) setError(reason instanceof Error ? reason.message : "读取决策历史失败");
      })
      .finally(() => {
        if (requestSequence.current === sequence) setLoading(false);
      });
  }, [filterKey, kind, status, symbol]);

  useEffect(() => {
    setError("");
    const timer = window.setTimeout(load, 250);
    return () => {
      window.clearTimeout(timer);
      requestSequence.current += 1;
    };
  }, [load]);

  const kindLabel = (value: string, subkind: string) => value === "candidate" ? "候选" : value === "position" ? "持仓" : value === "trade" ? "成交" : value === "research" && subkind === "variant_decision" ? "研究变体" : value === "research" ? "个股研究" : "正式信号";
  const sourceState = (source: "candidate" | "position" | "trade_receipts" | "formal_signal_reviews" | "stock_research_reports" | "variant_decisions") =>
    payload?.sources[source] ?? { available: false, error: "当前后端未提供该来源", count: 0 };
  const statusLabel = (value: string) => ({
    active: "进行中", stopped: "已停止", transferred: "已转持仓", transferred_unlinked: "持仓待核",
    defer: "暂缓", reject: "暂不考虑", adopted: "已采用观察", archived: "已清仓历史",
    buy: "买入", sell: "卖出",
    saved: "已保存",
    continue_validation: "继续验证",
  }[value] || value || "未标注");
  return <section className="aq-section" id="decision-history">
    <header className="aq-section-head"><div><h2>我的决策索引</h2><p>按当前租户只读汇总候选、持仓、实际成交和正式信号复盘；原领域仍是唯一写入事实源。</p></div><Badge>{payload?.total ?? 0} 条</Badge></header>
    <div className="aq-candidate-review-form-grid">
      <label>股票代码<input className="aq-input" value={symbol} maxLength={6} onChange={(event) => setSymbol(event.target.value.replace(/\D/g, ""))} placeholder="六位代码" /></label>
      <label>记录类型<select className="aq-input" value={kind} onChange={(event) => setKind(event.target.value)}><option value="">全部类型</option><option value="candidate">候选</option><option value="position">持仓</option><option value="trade">成交</option><option value="research">研究</option><option value="signal">正式信号</option></select></label>
      <label>状态<select className="aq-input" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">全部状态</option><option value="active">进行中</option><option value="stopped">已停止</option><option value="transferred">已转持仓</option><option value="transferred_unlinked">持仓待核</option><option value="defer">暂缓</option><option value="reject">暂不考虑</option><option value="adopted">已采用观察</option><option value="continue_validation">继续验证</option><option value="archived">已清仓历史</option><option value="buy">买入</option><option value="sell">卖出</option><option value="validated">已结算信号</option><option value="pending">未结算信号</option><option value="saved">已保存</option></select></label>
    </div>
    {error ? <StatePanel tone="warn">{error}<button type="button" className="aq-btn" onClick={load}>重试</button></StatePanel> : null}
    {payload ? <div className="aq-tag-row">
      {(["candidate", "position", "trade_receipts", "stock_research_reports", "variant_decisions", "formal_signal_reviews"] as const).map((source) => { const state = sourceState(source); return <Tag key={source} tone={state.available ? "ok" : "warn"}>{source === "formal_signal_reviews" ? "正式复盘" : source === "trade_receipts" ? "实际成交" : source === "stock_research_reports" ? "个股研究" : source === "variant_decisions" ? "变体决定" : source === "candidate" ? "候选" : "持仓"}：{state.available ? String(state.count) + " 条" : "不可用"}</Tag>; })}
    </div> : null}
    {payload ? (["candidate", "position", "trade_receipts", "stock_research_reports", "variant_decisions", "formal_signal_reviews"] as const).filter((source) => !sourceState(source).available).map((source) => <p className="aq-note" role="status" key={source}>{source === "formal_signal_reviews" ? "正式信号复盘" : source === "trade_receipts" ? "实际成交" : source === "stock_research_reports" ? "个股研究报告" : source === "variant_decisions" ? "研究变体决定" : source === "candidate" ? "候选决定" : "持仓计划"}未并入索引：{sourceState(source).error || "来源暂不可用"}</p>) : null}
    {(loading || loadedFilter !== filterKey) && !error ? <LoadingState label="更新当前筛选的决策历史…" /> : payload?.items.length && loadedFilter === filterKey ? <div className="aq-card-grid">{payload.items.map((item) => {
      const candidateDecision = item.subkind === "decision" ? decisions.find((decision) => decision.id === item.id) : undefined;
      const observationHref = item.subkind === "observation" ? `/review#candidate-observation-${encodeURIComponent(item.id)}` : "";
      const href = observationHref || item.href;
      return <article className="aq-card aq-candidate-review" key={`${item.kind}:${item.id}`}>
        <header className="aq-card-head"><div><h3>{item.name || item.symbol}</h3><span className="aq-code">{item.symbol ? `${item.symbol} · ` : ""}{kindLabel(item.kind, item.subkind)} · {formatEvidenceTime(item.decision_at) || "时间未记录"}</span></div><Badge tone={item.kind === "trade" || item.status === "reject" ? "neutral" : item.status === "active" ? "ok" : "warn"}>{statusLabel(item.status)}</Badge></header>
        {item.kind === "candidate" && item.subkind === "observation" ? <>
          <p className="aq-note">候选观察来源 {item.source_snapshot_id || "未记录"} · 完整条件与复盘在候选观察记录中查看。</p>
          <Link className="aq-btn" to={href}>查看候选复盘记录</Link>
        </> : item.kind === "candidate" && candidateDecision ? <>
          <p>{candidateDecision.note}</p>
          <details><summary>查看当时判断与状态历史</summary>
            <p><b>问题：</b>{candidateDecision.personal_context.personal_intent || "未记录"} · <b>期限：</b>{candidateDecision.personal_context.time_horizon || "未记录"}</p>
            {candidateDecision.personal_context.personal_hypothesis ? <p><b>本人假设：</b>{candidateDecision.personal_context.personal_hypothesis}</p> : null}
            {candidateDecision.personal_context.counterargument ? <p><b>反方理由：</b>{candidateDecision.personal_context.counterargument}</p> : null}
            <p>来源快照：{candidateDecision.source_snapshot_id}</p>
            {candidateDecision.audit.length > 1 ? <p>决定历史：{candidateDecision.audit.map((event) => event.action === "observation_adopted" ? "后来采用观察条件" : event.action === "update" ? "修订决定" : "记录决定").join(" → ")}</p> : null}
          </details>
          <Link className="aq-btn" to={href}>打开个股研究</Link>
          {candidateDecision.adopted_observation_id ? <Link className="aq-btn" to="/monitor">查看观察跟进</Link> : null}
        </> : <>
          <p>{item.summary || "没有摘要"}</p><p className="aq-note">{item.detail || "没有补充说明"} · 来源身份 {item.source_snapshot_id || "未记录"}</p>
          <Link className="aq-btn" to={href}>{item.kind === "position" ? "打开持仓计划" : item.kind === "trade" ? "打开对应持仓" : item.subkind === "variant_decision" ? "打开研究验证" : item.kind === "research" ? "打开个股研究" : "打开正式复盘"}</Link>
        </>}
      </article>;
    })}</div> : !loading && !error && loadedFilter === filterKey ? <EmptyState title="当前筛选没有决策记录" detail="候选、持仓、成交与正式复盘各自保留原始事实源；来源不可用时不会用其他数据补齐。" /> : null}
  </section>;
}

function FormalSignalReview() {
  const [signals, setSignals] = useState<SignalReviewPayload | null>(null);
  const [reviews, setReviews] = useState<ReviewRecord[]>([]);
  const [reviewsAvailable, setReviewsAvailable] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [signalQuery, setSignalQuery] = useState("");
  const [rating, setRating] = useState(3);
  const [tags, setTags] = useState("");
  const [notes, setNotes] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const [signalResult, reviewResult] = await Promise.allSettled([api.signals(), api.reviews()]);
    if (signalResult.status === "fulfilled") setSignals(signalResult.value);
    else setError(signalResult.reason instanceof Error ? signalResult.reason.message : "读取正式信号失败");
    if (reviewResult.status === "fulfilled") {
      setReviews(reviewResult.value);
      setReviewsAvailable(true);
    } else {
      setReviewsAvailable(false);
      setError((previous) => [previous, reviewResult.reason instanceof Error ? reviewResult.reason.message : "读取正式信号复盘失败"].filter(Boolean).join("；"));
    }
    setLoading(false);
  }, []);
  useEffect(() => { void load(); }, [load]);

  const selected = signals?.signals.find((signal) => signal.id === selectedId) ?? null;
  const existing = selected ? reviews.find((review) => review.signal_id === selected.id) ?? null : null;
  const reviewableSignals = (signals?.signals ?? []).filter((signal) => /^\d{6}$/.test(signal.symbol));
  const filteredSignals = reviewableSignals.filter((signal) =>
    `${signal.signal_date} ${signal.symbol} ${signal.name} ${signal.strategies.join(" ")}`.toLowerCase().includes(signalQuery.trim().toLowerCase()),
  );

  function choose(signalId: string) {
    setSelectedId(signalId);
    const linked = reviews.find((review) => review.signal_id === signalId);
    setRating(linked?.rating ?? 3);
    setTags(linked?.tags.join(", ") ?? "");
    setNotes(linked?.notes ?? "");
    setSaved("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setSaving(true);
    setError("");
    setSaved("");
    const input = {
      signal_id: selected.id,
      date: selected.signal_date,
      symbol: selected.symbol,
      rating,
      tags: [...new Set(tags.split(/[,，、]/).map((tag) => tag.trim()).filter(Boolean))],
      notes: notes.trim(),
    };
    try {
      if (existing) await api.updateReview(existing.id, { rating: input.rating, tags: input.tags, notes: input.notes });
      else await api.createReview(input);
      setSaved("复盘已保存到正式信号记录");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存正式信号复盘失败");
    } finally {
      setSaving(false);
    }
  }

  const statusLabel = (signal: SignalReviewSource) => signal.win === null
    ? "未结算"
    : `${signal.win ? "正收益" : "非正收益"}${signal.return_pct === null ? "" : ` · ${signal.return_pct.toFixed(2)}%`}`;

  return <section className="aq-section">
    <header className="aq-section-head"><div><h2>正式信号复盘</h2><p>选择正式信号，记录本人评分、标签和当时复盘；未结算信号保持“未结算”，不推测结果。</p></div><button type="button" className="aq-btn" onClick={() => void load()} disabled={loading}><RefreshCw aria-hidden="true" />刷新</button></header>
    {signals?.data_source_health.ledger === "stale" ? <StatePanel tone="warn">正式信号台账已停滞，最新信号日 {signals.data_source_health.latest_signal_date || "未记录"}；可复盘历史记录，但这不代表当前扫描状态。</StatePanel> : null}
    {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
    {loading && !signals ? <LoadingState label="读取正式信号与复盘…" /> : reviewableSignals.length ? <>
      <div className="aq-candidate-review-form-grid">
        <label>查找信号<input className="aq-input" value={signalQuery} onChange={(event) => setSignalQuery(event.target.value)} placeholder="日期、代码、名称或策略" /></label>
        <label>选择正式信号<select className="aq-input" value={selectedId} onChange={(event) => choose(event.target.value)}>
          <option value="">{filteredSignals.length ? `选择一条信号 · ${filteredSignals.length} 条` : "没有匹配的信号"}</option>
          {filteredSignals.map((signal) => <option key={signal.id} value={signal.id}>{signal.signal_date} · {signal.name || signal.symbol} ({signal.symbol}) · {statusLabel(signal)}{reviews.some((review) => review.signal_id === signal.id) ? " · 已复盘" : " · 待复盘"}</option>)}
        </select></label>
      </div>
      {selected ? <section className="aq-candidate-review-context">
        <h3>{selected.name || selected.symbol} <span className="aq-code">{selected.symbol}</span></h3>
        <p>信号日 {selected.signal_date} · {selected.strategies.join("、") || "未记录策略"} · {statusLabel(selected)}</p>
        <p>{selected.reasons.join("；") || "台账没有保存信号理由"}</p>
        {existing ? <p>已有个人复盘：{existing.rating}/5 · {existing.tags.join("、") || "无标签"}</p> : <p>这条信号尚无个人复盘。</p>}
      </section> : null}
      {selected ? <form className="aq-candidate-review-form" onSubmit={(event) => void save(event)}>
        <h3>{existing ? "更新正式信号复盘" : "记录正式信号复盘"} · {selected.name || selected.symbol} · {selected.signal_date}</h3>
        <div className="aq-candidate-review-form-grid">
          <label>本人评分<select className="aq-input" value={rating} onChange={(event) => setRating(Number(event.target.value))}>{[1, 2, 3, 4, 5].map((value) => <option key={value} value={value}>{value} / 5</option>)}</select></label>
          <label>复盘标签<input className="aq-input" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="用逗号分开，例如：等待不足，证据完整" /></label>
          <label className="aq-candidate-review-lesson">当时的判断与事后核对<textarea className="aq-input" value={notes} maxLength={5000} onChange={(event) => setNotes(event.target.value)} /></label>
        </div>
        {saved ? <StatePanel>{saved}</StatePanel> : null}
        <button type="submit" className="aq-btn aq-btn-primary" disabled={saving || !reviewsAvailable}>{saving ? "保存中…" : !reviewsAvailable ? "复盘记录不可用" : existing ? "更新复盘" : "保存复盘"}</button>
      </form> : null}
      {!filteredSignals.length && signalQuery.trim() ? <EmptyState title="没有匹配的个股信号" detail="可按信号日、六位代码、名称或策略查找。扫描运行事件不属于个股信号，不进入复盘选择。" /> : null}
    </> : !loading ? <EmptyState title="没有可复盘的个股信号" detail={signals?.signals.length ? "台账当前只有扫描运行事件，没有可关联到个股的六位代码信号。" : signals?.reason || "正式台账没有带信号编号的个股记录；候选观察和市场收评仍可单独复盘。"} /> : null}
  </section>;
}

function CandidateReviewRecord({
  item,
  onSaved,
}: {
  item: CandidateObservation;
  onSaved: (updated: CandidateObservation) => void;
}) {
  const [outcome, setOutcome] = useState<CandidateObservationReviewInput["outcome"]>(item.review?.outcome ?? "unclear");
  const [quality, setQuality] = useState<CandidateObservationReviewInput["process_quality"]>(item.review?.process_quality ?? "unrated");
  const [lesson, setLesson] = useState(item.review?.lesson ?? "");
  const [receiptIds, setReceiptIds] = useState(item.review?.receipt_ids.join(", ") ?? "");
  const [confirmSameDayOrder, setConfirmSameDayOrder] = useState(item.review?.confirm_same_day_order ?? false);
  const [confirmCrossPosition, setConfirmCrossPosition] = useState(item.review?.confirm_cross_position ?? false);
  const [journal, setJournal] = useState<TradeJournal | null>(null);
  const [journalLoading, setJournalLoading] = useState(false);
  const [journalLoaded, setJournalLoaded] = useState(false);
  const [journalError, setJournalError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function loadJournal(force = false) {
    if ((!force && journalLoaded) || journalLoading) return;
    setJournalLoading(true);
    setJournalError("");
    try {
      setJournal(await api.tradeJournal(item.symbol));
      setJournalLoaded(true);
    } catch (reason) {
      setJournalError(reason instanceof Error ? reason.message : "读取真实成交记录失败");
    } finally {
      setJournalLoading(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const input: CandidateObservationReviewInput = {
      expected_version: item.revision,
      outcome,
      process_quality: quality,
      lesson: lesson.trim(),
      receipt_ids: receiptIds.split(/[\n,，]/).map((value) => value.trim()).filter(Boolean),
      confirm_same_day_order: confirmSameDayOrder,
      confirm_cross_position: confirmCrossPosition,
    };
    try {
      onSaved(await api.reviewCandidateObservation(item.id, input));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存复盘失败");
    } finally {
      setBusy(false);
    }
  }

  const context = item.personal_context;
  const analysis = item.analysis_snapshot;
  const decisionDate = item.created_at.slice(0, 10);
  const selectableReceipts = (journal?.receipts ?? []).filter((receipt) => receipt.date >= decisionDate);
  const selectedReceipts = selectableReceipts.filter((receipt) => receiptIds.split(/[\n,，]/).map((value) => value.trim()).includes(receipt.receipt_id));
  const requiresSameDayConfirmation = selectedReceipts.some((receipt) => receipt.date === decisionDate);
  const requiresPositionConfirmation = selectedReceipts.some((receipt) => !item.transferred_to_position_id || receipt.position_id !== item.transferred_to_position_id);
  return (
    <article className="aq-card aq-candidate-review" id={`candidate-observation-${item.id}`} key={item.id}>
      <details onToggle={(event) => { if (event.currentTarget.open) void loadJournal(); }}>
        <summary className="aq-candidate-review-summary">
          <span><b>{item.name || item.symbol}</b> <span className="aq-code">{item.symbol}</span></span>
          <span>{item.review ? "已复盘" : "待复盘"} · {item.state === "active" ? "观察中" : item.state === "transferred" ? "已转持仓" : item.state === "transferred_unlinked" ? "已转持仓 · 身份待核" : "已停止"}</span>
        </summary>
        <div className="aq-candidate-review-body">
          <dl className="aq-candidate-review-meta">
            <div><dt>研究日</dt><dd>{analysis?.generated_at ? formatEvidenceTime(analysis.generated_at) : formatEvidenceTime(item.source_generated_at)}</dd></div>
            <div><dt>行情时点</dt><dd>{formatEvidenceTime(item.source_as_of)}</dd></div>
            <div><dt>观察条件</dt><dd>{item.comparator === "at_or_above" ? "达到或高于" : "达到或低于"} {item.price.toFixed(2)} 元</dd></div>
            <div><dt>状态</dt><dd>{item.state === "active" ? "观察中" : item.state === "transferred" ? "已转持仓" : item.state === "transferred_unlinked" ? "已转持仓 · 仓位身份待核" : "已停止"}</dd></div>
          </dl>
          {item.transferred_to_position_id ? <p><Link to={`/my/holdings/${encodeURIComponent(item.transferred_to_position_id)}/plan`}>继续查看对应持仓计划</Link></p> : null}

          <section className="aq-candidate-review-context">
            <h3>当时的个人判断</h3>
            <p><b>要解决的问题：</b>{context?.personal_intent || "未记录"}</p>
            <p><b>观察期限：</b>{context?.time_horizon || "未记录"}</p>
            {context?.personal_hypothesis ? <p><b>本人假设：</b>{context.personal_hypothesis}</p> : null}
            {context?.counterargument ? <p><b>反方理由：</b>{context.counterargument}</p> : null}
          </section>

          <section className="aq-candidate-review-context">
            <h3>采用条件</h3>
            <p><b>等什么：</b>{item.condition}</p>
            <p><b>何时取消：</b>{item.invalidation}</p>
            <p><b>来源：</b>{analysis?.model || "AI 分析"} · {analysis?.analysis_id || item.source_analysis_id}</p>
          </section>

          {analysis ? <details className="aq-candidate-review-snapshot">
            <summary>查看当时完整分析和证据快照</summary>
            <p>{analysis.conclusion || "当时未记录有效结论"}</p>
            {analysis.evidence_ids?.length ? <p>引用证据：{analysis.evidence_ids.join("、")}</p> : null}
            {analysis.missing_evidence?.length ? <p>当时缺证：{analysis.missing_evidence.join("、")}</p> : null}
            {analysis.actions?.length ? <ul>{analysis.actions.map((action, index) => <li key={`${index}:${action.condition}`}>{action.action}：{action.condition}；失效：{action.invalidation}</li>)}</ul> : null}
            {analysis.evidence ? <pre>{JSON.stringify(analysis.evidence, null, 2)}</pre> : null}
          </details> : <StatePanel tone="warn">旧记录没有完整分析快照，仅保留当时观察条件和来源编号。</StatePanel>}

          {item.review ? <section className="aq-candidate-review-saved">
            <h3>最近一次复盘 · {formatEvidenceTime(item.review.reviewed_at)}</h3>
            <p>{OUTCOME_LABELS[item.review.outcome]} · {QUALITY_LABELS[item.review.process_quality]}</p>
            <p>{item.review.lesson}</p>
            {item.review.receipt_ids.length ? <p>关联成交凭据：{item.review.receipt_ids.join("、")}</p> : null}
          </section> : null}

          {item.state === "active" ? <StatePanel tone="warn">该观察仍在后台监控。终局复盘前请先停止观察，避免后续到价通知与已保存结果冲突。<Link className="aq-btn" to="/monitor">前往监控停止观察</Link></StatePanel> : <form className="aq-candidate-review-form" onSubmit={(event) => void save(event)}>
            <h3>{item.review ? "更新个人复盘" : "记录个人复盘"}</h3>
            <div className="aq-candidate-review-form-grid">
              <label>观察结果<select className="aq-input" value={outcome} onChange={(event) => setOutcome(event.target.value as CandidateObservationReviewInput["outcome"])}>
                {Object.entries(OUTCOME_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
              <label>判断过程<select className="aq-input" value={quality} onChange={(event) => setQuality(event.target.value as CandidateObservationReviewInput["process_quality"])}>
                {Object.entries(QUALITY_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select></label>
              <label className="aq-candidate-review-lesson">回看当时判断，留下下一次可用的经验<textarea className="aq-input" value={lesson} maxLength={1000} onChange={(event) => setLesson(event.target.value)} required /></label>
              <div className="aq-candidate-review-receipts">
                <button type="button" className="aq-btn" aria-expanded={journalLoaded || journalLoading} onClick={() => void loadJournal(true)} disabled={journalLoading}><RefreshCw aria-hidden="true" />{journalLoading ? "读取成交…" : journalLoaded ? "刷新实际成交" : "重新读取成交"}</button>
                <small>只引用本租户该股票、候选采用日期当日或之后的真实成交，不复制成交事实。</small>
                {journalError ? <StatePanel tone="warn">{journalError}</StatePanel> : null}
                {journalLoaded && selectableReceipts.length === 0 ? <p className="aq-note">该股票没有晚于候选采用日期的成交记录。</p> : null}
                {selectableReceipts.map((receipt) => <label className="aq-candidate-receipt-option" key={receipt.receipt_id}><input type="checkbox" checked={receiptIds.split(/[\n,，]/).map((value) => value.trim()).includes(receipt.receipt_id)} onChange={(event) => setReceiptIds((previous) => { const values = previous.split(/[\n,，]/).map((value) => value.trim()).filter(Boolean); return event.target.checked ? [...new Set([...values, receipt.receipt_id])].join(",") : values.filter((value) => value !== receipt.receipt_id).join(","); })} /><span>{receipt.date} · {receipt.direction === "buy" ? "买入" : "卖出"} {receipt.shares} 股 × {receipt.price.toFixed(2)} 元</span><code>{receipt.receipt_id}</code></label>)}
                {requiresSameDayConfirmation ? <label className="aq-action-plan-confirm"><input type="checkbox" checked={confirmSameDayOrder} onChange={(event) => setConfirmSameDayOrder(event.target.checked)} />我确认这些同日成交发生在采用观察条件之后</label> : null}
                {requiresPositionConfirmation ? <label className="aq-action-plan-confirm"><input type="checkbox" checked={confirmCrossPosition} onChange={(event) => setConfirmCrossPosition(event.target.checked)} />我确认这些成交属于该候选决策对应或关联的持仓</label> : null}
              </div>
            </div>
            {error ? <StatePanel tone="warn">{error}</StatePanel> : null}
            <button type="submit" className="aq-btn aq-btn-primary" disabled={busy || !lesson.trim() || (requiresSameDayConfirmation && !confirmSameDayOrder) || (requiresPositionConfirmation && !confirmCrossPosition)}><BookOpen aria-hidden="true" />{busy ? "保存中…" : "保存复盘"}</button>
          </form>}
        </div>
      </details>
    </article>
  );
}

export function ReviewWorkbenchPage() {
  const [items, setItems] = useState<CandidateObservation[]>([]);
  const [decisions, setDecisions] = useState<CandidateDecision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    setLoading(true);
    api.candidateObservations().then((payload) => {
      setItems(payload.items.slice().reverse());
      setDecisions((payload.decisions ?? []).slice().sort((left, right) => right.updated_at.localeCompare(left.updated_at)));
      setError("");
    }).catch((reason) => setError(reason instanceof Error ? reason.message : "读取个人候选复盘失败"))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  return <div className="aq-page">
    <header className="aq-page-head"><div><p className="aq-eyebrow"><BookOpen aria-hidden="true" />AQSP · 研究复盘</p><div className="aq-title-row"><h1>复盘</h1><Badge tone="neutral">个人判断与结果</Badge></div><p className="aq-page-sub">对照当时保存的证据、本人判断和后来结果；成交事实继续以成交账本为准。</p></div><button type="button" className="aq-btn" onClick={load} disabled={loading}><RefreshCw aria-hidden="true" />刷新记录</button></header>
    <section className="aq-section"><h2>市场收盘复盘</h2><ClosingReviewPanel /></section>
    <DecisionHistoryIndex decisions={decisions} />
    <FormalSignalReview />
    <section className="aq-section">
      <header className="aq-section-head"><div><h2>个人候选决策</h2><p>候选研究时的个人问题、期限、反方观点、AI依据、采用条件和复盘经验保存在同一条记录中。</p></div><Badge>{items.length} 条</Badge></header>
      {error ? <StatePanel tone="warn">{error}<button type="button" className="aq-btn" onClick={load}>重试</button></StatePanel> : null}
      {loading && items.length === 0 ? <LoadingState label="读取个人决策记录…" /> : items.length ? <div className="aq-card-grid">{items.map((item) => <CandidateReviewRecord key={item.id} item={item} onSaved={(updated) => setItems((current) => current.map((row) => row.id === updated.id ? updated : row))} />)}</div> : !loading ? <EmptyState title="还没有候选决策记录" detail="从策略候选里写下个人问题与观察期限，确认采用条件后，这里会保留完整依据供事后复盘。" /> : null}
    </section>
    <section className="aq-section"><header className="aq-section-head"><div><h2>其他研究记录</h2><p>自由笔记和正式信号复盘仍保留各自的数据来源。</p></div></header><div className="aq-row-actions"><Link className="aq-btn aq-btn-primary" to="/my/notes"><NotebookPen aria-hidden="true" />我的笔记</Link><Link className="aq-btn" to="/my/reports"><FileText aria-hidden="true" />研究资料</Link><Link className="aq-btn" to="/backtest"><BookOpen aria-hidden="true" />正式信号表现</Link></div></section>
  </div>;
}
