import { PositionSaleReview } from "./PositionSaleReview";
import { PositionConditionChecks } from "./PositionConditionChecks";
import { useEffect, useRef, useState } from "react";
import { api, type ActionWorksheet, type PlanParameters, type PlanTarget, type ReviewChoice } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { beginnerPlan, planJourney, planSteps, worksheetExpired } from "@/lib/position-plan";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { PositionDiscussionPanel } from "./PositionDiscussionPanel";
import { PositionChangeReview } from "./StrategyReviewContextPanel";
import { draftNeedsReview, readPlanWorksheet, savePlanChoice, worksheetGuard } from "@/lib/position-workspace";
import { ALERTS_CHANGED_EVENT, requestOperation } from "@/lib/operation";
import { strategyWatchState, WATCH_COMPARATORS } from "@/lib/strategy-watch";
import { positionChangeView } from "@/lib/position-change-review";

type Draft = Record<"quantity" | "sell_price" | "buy_price" | "commission_rate" | "min_commission" | "stamp_tax_rate" | "slippage_rate", string>;
const EMPTY: Draft = { quantity: "", sell_price: "", buy_price: "", commission_rate: "", min_commission: "", stamp_tax_rate: "", slippage_rate: "" };
const titles = { hold: "持有：等待验证与退出条件", reduce: "减仓：分批退出计划", t_watch: "做 T：先卖后买的条件计划" };

export function calculationDraftRequiresReview(hasDraft: boolean, revision: string | undefined, worksheet: ActionWorksheet): boolean {
  return hasDraft && draftNeedsReview(revision, worksheet);
}

export function PositionActionPlan({ eventId, target, choice, worksheet, onWorksheet, disabled = false }: {
  eventId?: string; target?: PlanTarget; choice: ReviewChoice; worksheet?: ActionWorksheet; disabled?: boolean; onWorksheet?: (next: ActionWorksheet) => void;
}) {
  const planTarget = target ?? { kind: "alert" as const, id: eventId ?? "" };
  const external = useRef(worksheet); external.current = worksheet;
  const report = useRef(onWorksheet); report.current = onWorksheet;
  const [data, setData] = useState<ActionWorksheet | null>(worksheet ?? null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [draftRevision, setDraftRevision] = useState<string | undefined>(worksheet?.revision);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [requestBusy, setBusy] = useState(false);
  const busy = requestBusy || disabled;
  const [saved, setSaved] = useState(false);
  const [tick, setTick] = useState(Date.now());
  const dirty = useRef(false);
  const hasCalculationDraft = useRef(!!worksheet?.review_plan.parameters);
  const editedPrices = useRef({ sell_price: false, buy_price: false });
  const inFlight = useRef(false);
  const generation = useRef(0);
  const refreshLatest = useRef<() => Promise<void>>(async () => {});
  const calculator = useRef<HTMLDetailsElement>(null);
  const lastSaleKey = useRef("");
  useEffect(() => {
    const key = JSON.stringify(data?.recorded_sale ?? null);
    if (data?.sale_requires_review && key !== lastSaleKey.current) {
      if (calculator.current) calculator.current.open = false;
      setSaved(false);
    }
    lastSaleKey.current = key;
  }, [data?.recorded_sale, data?.sale_requires_review]);
  function sourcePrices(next: ActionWorksheet) {
    const high = next.basis.references.find((row) => row.label === "本交易日已见高点") ?? next.basis.references.find((row) => row.label === "最近完成日高点");
    const low = next.basis.references.find((row) => row.label === "本交易日已见低点") ?? next.basis.references.find((row) => row.label === "最近完成日低点");
    return { high, low };
  }
  function receive(next: ActionWorksheet, initial = false) {
    setData(next); setTick(Date.now());
    const { high, low } = sourcePrices(next);
    if (initial) {
      const stored = next.review_plan.choice === choice ? next.review_plan.parameters : undefined;
      hasCalculationDraft.current = !!stored;
      editedPrices.current = { sell_price: !!stored, buy_price: !!stored };
      setDraft(stored ? { quantity: String(stored.quantity), sell_price: String(stored.sell_price), buy_price: stored.buy_price == null ? "" : String(stored.buy_price), commission_rate: String(stored.commission_rate * 100), min_commission: String(stored.min_commission), stamp_tax_rate: String(stored.stamp_tax_rate * 100), slippage_rate: String(stored.slippage_rate * 100) } : { ...EMPTY, sell_price: high?.price.toFixed(2) ?? "", buy_price: low?.price.toFixed(2) ?? "" });
      setConfirmed(stored?.fees_confirmed === true); setDraftRevision(next.revision);
    } else {
      hasCalculationDraft.current ||= !!next.review_plan.parameters;
      if (!hasCalculationDraft.current) setDraftRevision(next.revision);
      setDraft((previous) => ({ ...previous, sell_price: editedPrices.current.sell_price ? previous.sell_price : high?.price.toFixed(2) ?? "", buy_price: editedPrices.current.buy_price ? previous.buy_price : low?.price.toFixed(2) ?? "" }));
    }
  }
  useEffect(() => {
    if (worksheet) receive(worksheet);
  }, [worksheet]);
  useEffect(() => {
    if (worksheet) return;
    let initial = true;
    return subscribeAlertRefresh(() => { if (initial) { initial = false; return; } void refreshLatest.current(); }, browserAlertRefreshSource());
  }, [!!worksheet]);
  useEffect(() => { const timer = window.setInterval(() => setTick(Date.now()), 15_000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    const version = ++generation.current;
    dirty.current = false; setSaved(false); setError("");
    if (external.current) { receive(external.current, true); return () => { generation.current++; }; }
    inFlight.current = true; setBusy(true);
    readPlanWorksheet(planTarget).then((next) => {
      if (version !== generation.current) return;
      receive(next, true);
      if (next.review_plan.choice && next.review_plan.choice !== choice) window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    }).catch((reason) => { if (version === generation.current) setError(reason instanceof Error ? reason.message : "计划读取失败"); })
      .finally(() => { if (version === generation.current) { setBusy(false); inFlight.current = false; } });
    return () => { generation.current++; };
  }, [planTarget.kind, planTarget.id, choice]);
  async function refresh() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const version = ++generation.current;
    try {
      const next = await readPlanWorksheet(planTarget);
      if (generation.current === version) { receive(next); report.current?.(next); }
    } catch (reason) { if (generation.current === version) setError(reason instanceof Error ? reason.message : "刷新失败，输入已保留"); }
    finally { inFlight.current = false; if (generation.current === version) setBusy(false); }
  }
  async function save() {
    if (!data || disabled || data.archived || inFlight.current || draftNeedsReview(draftRevision, data)) return;
    const required = Object.keys(draft).filter((key) => choice === "t_watch" || key !== "buy_price") as (keyof Draft)[];
    if (required.some((key) => !draft[key].trim() || !Number.isFinite(Number(draft[key]))) || !confirmed) { setError("请填完数量、价格和全部费用假设，并勾选确认；不清楚的费用请先核对券商。"); return; }
    const parameters: PlanParameters = { quantity: Number(draft.quantity), sell_price: Number(draft.sell_price), buy_price: choice === "t_watch" ? Number(draft.buy_price) : null, commission_rate: Number(draft.commission_rate) / 100, min_commission: Number(draft.min_commission), stamp_tax_rate: Number(draft.stamp_tax_rate) / 100, slippage_rate: Number(draft.slippage_rate) / 100, fees_confirmed: confirmed };
    inFlight.current = true; setBusy(true); setError(""); setSaved(false);
    const version = ++generation.current;
    try {
      const next = await savePlanChoice(planTarget, data, choice, parameters);
      if (generation.current !== version) return;
      setData(next); setDraftRevision(next.revision); report.current?.(next); dirty.current = false; editedPrices.current = { sell_price: true, buy_price: choice === "t_watch" }; setSaved(true); setTick(Date.now());
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { if (generation.current === version) setError(reason instanceof Error ? reason.message : "测算保存失败，输入已保留"); }
    finally { inFlight.current = false; if (generation.current === version) setBusy(false); }
  }
  refreshLatest.current = refresh;
  async function stopWatch() {
    if (!data?.position_id || !data.review_plan.watch || data.archived || disabled || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const version = ++generation.current;
    try {
      const guard = worksheetGuard(data);
      const next = await api.stopStrategyWatch(data.position_id, guard.expected_position, guard.expected_revision);
      if (generation.current === version) { receive(next); report.current?.(next); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); }
    } catch (reason) { if (generation.current === version) setError(`${reason instanceof Error ? reason.message : "停止结果尚未确认"}。请更新核对，未自动重复提交`); }
    finally { inFlight.current = false; if (generation.current === version) setBusy(false); }
  }
  const expired = !!data && worksheetExpired(data.basis, tick);
  const steps = data ? planSteps(choice, data.basis, { sell_price: Number(draft.sell_price), buy_price: Number(draft.buy_price), quantity: Number(draft.quantity) }) : [];
  const initialSimple = data ? beginnerPlan(choice, data.basis, { sell_price: Number(draft.sell_price), buy_price: Number(draft.buy_price) }) : null;
  const personalPaused = choice === "t_watch" && !!data?.basis.personal_pause_t;
  const personalReason = data?.route_review?.routes.find((route) => route.choice === "t_watch")?.reason || data?.constraints_issue || "个人条件已变化，先重新核对是否有时间跟进做 T。";
  const simple = personalPaused && initialSimple ? { next: personalReason, stop: "先更新个人条件或选择其他处理方式，不继续沿用原做 T 试算。", paused: true } : initialSimple;
  const needsReview = !!data && draftNeedsReview(draftRevision, data);
  const calculationNeedsReview = !!data && choice !== "hold" && calculationDraftRequiresReview(hasCalculationDraft.current, draftRevision, data);
  const baseJourney = data ? planJourney(choice, data, { sell_price: Number(draft.sell_price), buy_price: Number(draft.buy_price) }, tick) : null;
  const journey = personalPaused && baseJourney && !baseJourney.saleReview ? { label: "个人条件下先暂停做 T", detail: personalReason, attention: true, saleReview: false } : baseJourney;
  const blocked = !data || !!data.archived || needsReview || data.review_plan.choice !== choice || !!simple?.paused || expired;
  const discussionBlocked = busy || !data || !!data.archived || data.review_plan.choice !== choice || expired;
  const discussionBlockReason = busy ? "正在更新，请稍等。" : expired ? "先更新当前依据，再让 AI 分析。" : "";
  const watch = data?.review_plan.watch;
  const watchState = data ? strategyWatchState(data, tick) : null;
  const hasDirectionChangeExplanation = !!data && watchState?.title === "原策略依据发生变化，重新分析" && !!positionChangeView(data)?.changes.length;
  function field(key: keyof Draft, label: string) {
    return <label>{label}<input aria-label={label} disabled={busy} type="number" min="0" step={key === "quantity" ? "1" : "any"} value={draft[key]} onChange={(e) => { if (key === "sell_price" || key === "buy_price") editedPrices.current[key] = true; hasCalculationDraft.current = true; dirty.current = true; setSaved(false); setDraft({ ...draft, [key]: e.target.value }); }} /></label>;
  }
  const showResult = data?.scenario && data.review_plan.choice === choice && !dirty.current && !needsReview && !data.scenario_issue;
  if (data?.archived) return <section className="aq-action-plan" aria-label="历史处理计划"><h3>这笔持仓已清仓</h3><p>原方案与成交仅作为历史记录，不再产生当前操作步骤。</p>{data.recorded_sale ? <p>{data.recorded_sale.date} 已记录卖出 {data.recorded_sale.shares} 股，成交价 {data.recorded_sale.price.toFixed(2)} 元。</p> : null}</section>;
  return <section className="aq-action-plan" aria-label={titles[choice]}>
    {error ? <p role="alert">{error}。输入已保留；如已提交但未收到结果，请先更新核对。</p> : null}
    {data ? <PositionSaleReview worksheet={data} disabled={busy} onWorksheet={(next) => { receive(next); report.current?.(next); }} /> : null}
    {calculationNeedsReview ? <div className="aq-refresh-warning" role="status"><strong>旧费用试算需要重新核对</strong><span>填写内容已保留；核对当前持仓和个人条件后再保存试算。AI 策略是否有效，以各自的分析依据和跟踪状态为准。</span><button className="aq-btn" type="button" disabled={busy || !data?.revision} onClick={() => { setDraftRevision(data?.revision); dirty.current = true; setSaved(false); }}>已核对最新情况，保留草稿继续</button></div> : null}
    {data && simple ? <>
      {personalPaused ? <p className="aq-refresh-warning" role="status">个人条件下先暂停做 T：{personalReason}</p> : null}
      {watch && watchState ? <section className="aq-strategy-watch" aria-label="正在跟踪的策略条件">
        <span className="aq-beginner-label">已采用 · 后台跟踪这项条件</span><h3>{watchState.title}</h3>{!hasDirectionChangeExplanation ? <p>{watchState.detail}</p> : null}
        <PositionChangeReview worksheet={data} />
        <div className="aq-strategy-watch-price"><b>{WATCH_COMPARATORS[watch.comparator]} {watch.price.toFixed(2)} 元</b><span>{watch.action === "buy_review" ? "考虑回补前复核" : watch.action === "sell_review" ? "考虑卖出前复核" : "观察变化"}</span></div>
        <p><b>到价后看：</b>{watch.condition}</p><p><b>取消或重核：</b>{watch.invalidation}</p>
        <small>{watch.price_ref?.label || "原价格引用待核对"} · {watch.price_ref?.source || "来源待核对"} · {formatEvidenceTime(watch.price_ref?.as_of)}</small>
        <PositionConditionChecks worksheet={data} now={tick} />
        <p className="aq-note">到价仍需核对完整买卖前提；通知设置见后台跟进。</p>
        <button type="button" className="aq-btn" disabled={busy} onClick={() => void stopWatch()}>停止这项跟踪</button>
      </section> : data.review_plan.watch_stopped && watchState ? <div className="aq-strategy-empty" role="status"><h3>{watchState.title}</h3><p>{watchState.detail}</p></div> : !data.review_plan.parameters ? <div className="aq-strategy-empty"><h3>路线已选，下一步先明确条件</h3><p>让 AI 结合当前大盘、板块、个股与个人条件，分析什么情况下继续、何时考虑买卖、何时取消。采用后再持续跟踪，不必先填股数和费用。</p></div> : null}
      <div className="aq-strategy-model"><PositionDiscussionPanel key={JSON.stringify([planTarget, choice, data.position_snapshot, data.review_plan.parameters])} target={planTarget} choice={choice} worksheet={data} disabled={discussionBlocked} disabledReason={discussionBlockReason} onWorksheet={(next) => { receive(next); report.current?.(next); }} onComplete={() => void refresh()} /></div>
      {dirty.current ? <p className="aq-note">下方试算草稿尚未保存，不会发送给 AI；本次分析使用已保存条件。</p> : null}
      {!watch && !data.review_plan.watch_stopped && data.review_plan.parameters ? <div className="aq-next-action" aria-live="polite">
        <span className="aq-beginner-label">{journey?.saleReview ? "成交后的下一步" : "现在走到哪一步"}</span>
        <h4>{journey?.label}</h4>
        <p className="aq-journey-fact">{journey?.detail}</p>
        {journey?.saleReview ? <><p className="aq-next-then">{choice === "t_watch" ? "先核对上方余仓，再重新分析并采用观察条件；实际买回后才回录买入，费用试算可选。" : "先核对上方余仓及风险；如需继续减仓，按当前余仓重新分析。"}</p>{choice === "t_watch" ? <button className="aq-btn" type="button" onClick={() => requestOperation({ direction: "buy", code: data.symbol, positionId: data.position_id })}>我已实际买回 · 录入成交</button> : null}{data.basis.quote_issue || expired ? <p role="status">{data.basis.quote_issue || "行情已旧，先更新后再试算。"}</p> : null}</> : <>
          {!expired && !simple.paused && !journey?.attention ? <><p className="aq-next-then"><b>接下来：</b>{simple.next}</p>{simple.then ? <p className="aq-next-then">{simple.then}</p> : null}</> : null}
          <p className="aq-next-cancel"><b>{choice === "t_watch" ? "什么时候不做了" : "什么时候重新考虑"}</b>{expired ? "旧价位只作参考，更新后再继续。" : simple.stop}</p>
        </>}
        <div className="aq-plan-meta"><span>{formatEvidenceTime(data.basis.quote_as_of)} · 可卖 {data.basis.sellable_shares ?? "待核对"} 股</span><button className="aq-btn" type="button" disabled={busy} onClick={() => void refresh()}>{busy ? "更新中…" : "更新行情"}</button></div>
        <p className="aq-note">页面可见时每分钟读取。后台只跟进已保存的观察价，检查时点见上方记录。{editedPrices.current.sell_price || editedPrices.current.buy_price ? "含你设定的观察价。" : "当前试算参考已发生的高低点，保存前不作为后台观察线。"}</p>
      </div> : null}
      {choice === "t_watch" ? <p className="aq-note">做 T 是先卖后买的差价尝试，可能买不回来或继续亏损；需要核对数量与费用时再展开试算。</p> : null}
      {choice !== "hold" ? <details className="aq-plan-calculator" ref={calculator}>
        <summary>{showResult ? "调整这次试算" : choice === "t_watch" ? "算一下：这次做 T 能减少多少亏损" : "算一下：卖出后还剩多少"}</summary>
        <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <p className="aq-note">这里只试算，不记录成交。价格可调整，股数由你填写。</p>{data.quantity_bounds ? <p className="aq-note">当前可卖 {data.quantity_bounds.sellable_shares} 股；做 T 回补至少 {data.quantity_bounds.buy_lot} 股。{data.quantity_bounds.note}</p> : null}
          <div className="aq-action-plan-fields">{field("quantity", "这次股数")}{field("sell_price", "打算卖到多少元")}{choice === "t_watch" ? field("buy_price", "打算多少元买回") : null}</div>
          <details className="aq-action-plan-fees">
            <summary>{confirmed ? "交易费用 · 已确认" : "交易费用 · 试算前需填写一次"}</summary>
            <p className="aq-note">按你的券商收费填写；填 0 表示本次不计该项。</p>
            <div className="aq-action-plan-fields">{field("commission_rate", "每边佣金（%）")}{field("min_commission", "每边最低佣金（元）")}{field("stamp_tax_rate", "卖出税费（%）")}{field("slippage_rate", "预留成交价偏差（%）")}</div>
            <label className="aq-action-plan-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={(e) => { setConfirmed(e.target.checked); hasCalculationDraft.current = true; dirty.current = true; setSaved(false); }} />我已核对这些费用</label>
          </details>
          <button type="submit" className="aq-btn aq-btn-primary" disabled={busy || blocked}>{busy ? "试算中…" : "保存并试算"}</button>
          {blocked ? <p className="aq-note">请先处理上方暂停原因，再试算。</p> : !confirmed ? <p className="aq-note">先展开“交易费用”填写并确认。</p> : null}
          {saved ? <p role="status">已保存，持仓没有变化。</p> : null}
        </form>
      </details> : null}
      {data.scenario_issue && !journey?.saleReview ? <p role="status" className="aq-action-plan-warning">{data.scenario_issue}</p> : null}
      {showResult && data.scenario ? <div className="aq-plan-outcome" role="status">
        <span className="aq-beginner-label">按你填写的价格成交后</span>
        <strong>{choice === "t_watch" ? data.scenario.net_cash === 0 ? "这次做 T 扣除费用后没有赚到差价" : data.scenario.round_trip_positive ? "这次预计多留下 " + data.scenario.net_cash.toFixed(2) + " 元" : "这次做 T 反而会多亏 " + Math.abs(data.scenario.net_cash).toFixed(2) + " 元" : "卖出后还剩 " + data.scenario.remaining_shares + " 股"}</strong>
        <p>{choice === "t_watch" ? "已扣除你填写的费用。它只是这一次的差价，不代表已回本。" : "这次预计盈亏 " + data.scenario.realized_pnl?.toFixed(2) + " 元，已计入你填写的费用。"}</p>
        {expired || data.basis.quote_issue ? <p className="aq-action-plan-warning">行情已旧，更新后再核对这次试算。</p> : null}
        <details><summary>查看计算明细</summary><dl><div><dt>扣除费用</dt><dd>{data.scenario.estimated_fees.toFixed(2)} 元</dd></div><div><dt>净回收 / 净差价</dt><dd>{data.scenario.net_cash.toFixed(2)} 元</dd></div><div><dt>完成后股数</dt><dd>{data.scenario.remaining_shares} 股</dd></div><div><dt>测算经济成本</dt><dd>{data.scenario.economic_cost?.toFixed(4) ?? "无剩余仓位"}{data.scenario.economic_cost == null ? "" : " 元/股"}</dd></div></dl><p className="aq-note">{data.scenario.note}</p></details>
      </div> : null}
      <details className="aq-plan-help"><summary>查看价位来源和完整条件</summary><p className="aq-note">这些价格来自已发生的高低点，只作观察参考，不代表到价就该买卖。</p>{data.basis.references.map((row) => <p key={row.label}>{row.label} {row.price.toFixed(2)} 元 · {row.source} · {formatEvidenceTime(row.as_of)}</p>)}<ul>{steps.map((step) => <li key={step}>{step}</li>)}</ul></details>
    </> : !error ? <p role="status">正在读取持仓和行情…</p> : null}
  </section>;
}
