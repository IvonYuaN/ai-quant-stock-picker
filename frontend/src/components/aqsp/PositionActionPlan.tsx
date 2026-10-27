import { useEffect, useRef, useState } from "react";
import { type ActionWorksheet, type PlanParameters, type PlanTarget, type ReviewChoice } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { beginnerPlan, planJourney, planSteps, worksheetExpired } from "@/lib/position-plan";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { PositionDiscussionPanel } from "./PositionDiscussionPanel";
import { draftNeedsReview, readPlanWorksheet, savePlanChoice } from "@/lib/position-workspace";
import { ALERTS_CHANGED_EVENT, requestOperation } from "@/lib/operation";

type Draft = Record<"quantity" | "sell_price" | "buy_price" | "commission_rate" | "min_commission" | "stamp_tax_rate" | "slippage_rate", string>;
const EMPTY: Draft = { quantity: "", sell_price: "", buy_price: "", commission_rate: "", min_commission: "", stamp_tax_rate: "", slippage_rate: "" };
const titles = { hold: "持有：等待验证与退出条件", reduce: "减仓：分批退出计划", t_watch: "做 T：先卖后买的条件计划" };

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
  const [modelMounted, setModelMounted] = useState(false);
  const [tick, setTick] = useState(Date.now());
  const dirty = useRef(false);
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
      editedPrices.current = { sell_price: !!stored, buy_price: !!stored };
      setDraft(stored ? { quantity: String(stored.quantity), sell_price: String(stored.sell_price), buy_price: stored.buy_price == null ? "" : String(stored.buy_price), commission_rate: String(stored.commission_rate * 100), min_commission: String(stored.min_commission), stamp_tax_rate: String(stored.stamp_tax_rate * 100), slippage_rate: String(stored.slippage_rate * 100) } : { ...EMPTY, sell_price: high?.price.toFixed(2) ?? "", buy_price: low?.price.toFixed(2) ?? "" });
      setConfirmed(stored?.fees_confirmed === true); setDraftRevision(next.revision);
    } else {
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
  async function confirmObservation() {
    if (!data || disabled || data.archived || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const version = ++generation.current;
    try {
      const next = await savePlanChoice(planTarget, data, "hold");
      if (generation.current === version) { setData(next); setDraftRevision(next.revision); dirty.current = false; report.current?.(next); setTick(Date.now()); window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); }
    } catch (reason) { if (generation.current === version) setError(reason instanceof Error ? reason.message : "确认失败，请重试"); }
    finally { inFlight.current = false; if (generation.current === version) setBusy(false); }
  }
  const expired = !!data && worksheetExpired(data.basis, tick);
  const steps = data ? planSteps(choice, data.basis, { sell_price: Number(draft.sell_price), buy_price: Number(draft.buy_price), quantity: Number(draft.quantity) }) : [];
  const initialSimple = data ? beginnerPlan(choice, data.basis, { sell_price: Number(draft.sell_price), buy_price: Number(draft.buy_price) }) : null;
  const personalPaused = choice === "t_watch" && !!data?.basis.personal_pause_t;
  const personalReason = data?.route_review?.routes.find((route) => route.choice === "t_watch")?.reason || data?.constraints_issue || "个人条件已变化，先重新核对是否有时间跟进做 T。";
  const simple = personalPaused && initialSimple ? { next: personalReason, stop: "先更新个人条件或选择其他处理方式，不继续沿用原做 T 试算。", paused: true } : initialSimple;
  const needsReview = !!data && draftNeedsReview(draftRevision, data);
  const baseJourney = data ? planJourney(choice, data, { sell_price: Number(draft.sell_price), buy_price: Number(draft.buy_price) }, tick) : null;
  const journey = personalPaused && baseJourney && !baseJourney.saleReview ? { label: "个人条件下先暂停做 T", detail: personalReason, attention: true, saleReview: false } : baseJourney;
  const blocked = !data || !!data.archived || needsReview || data.review_plan.choice !== choice || !!simple?.paused || expired;
  const discussionBlocked = busy || blocked || dirty.current || !!data?.scenario_issue;
  const discussionBlockReason = needsReview ? "个人条件、持仓或计划版本已变化，请先核对并保留草稿。" : busy ? "正在更新，请稍等。" : blocked ? "先处理上方暂停原因，更新行情后再分析。" : dirty.current ? "你修改了试算条件，请先在上方保存并试算，再让 AI 分析。" : data?.scenario_issue ? "先修正上方试算问题，再分析。" : "";
  function field(key: keyof Draft, label: string) {
    return <label>{label}<input aria-label={label} disabled={busy} type="number" min="0" step={key === "quantity" ? "1" : "any"} value={draft[key]} onChange={(e) => { if (key === "sell_price" || key === "buy_price") editedPrices.current[key] = true; dirty.current = true; setSaved(false); setDraft({ ...draft, [key]: e.target.value }); }} /></label>;
  }
  const showResult = data?.scenario && data.review_plan.choice === choice && !dirty.current && !needsReview && !data.scenario_issue;
  if (data?.archived) return <section className="aq-action-plan" aria-label="历史处理计划"><h3>这笔持仓已清仓</h3><p>原方案与成交仅作为历史记录，不再产生当前操作步骤。</p>{data.recorded_sale ? <p>{data.recorded_sale.date} 已记录卖出 {data.recorded_sale.shares} 股，成交价 {data.recorded_sale.price.toFixed(2)} 元。</p> : null}</section>;
  return <section className="aq-action-plan" aria-label={titles[choice]}>
    {error ? <p role="alert">{error}。输入已保留；如已提交但未收到结果，请先更新核对。</p> : null}
    {needsReview && !(choice === "hold" && journey?.saleReview) ? <div className="aq-refresh-warning" role="status"><strong>个人条件、持仓或计划已变化</strong><span>旧试算和分析暂停使用，填写内容已保留。核对最新情况后可继续修改并重新试算。</span><button className="aq-btn" type="button" disabled={busy || !data?.revision} onClick={() => { if (choice === "hold") void confirmObservation(); else { setDraftRevision(data?.revision); dirty.current = true; setSaved(false); } }}>{choice === "hold" ? "核对最新情况并继续观察" : "已核对最新情况，保留草稿继续"}</button></div> : null}
    {data && simple ? <>
      <div className="aq-next-action" aria-live="polite">
        <span className="aq-beginner-label">{journey?.saleReview ? "成交后的下一步" : "现在走到哪一步"}</span>
        <h4>{journey?.label}</h4>
        <p className="aq-journey-fact">{journey?.detail}</p>
        {journey?.saleReview ? <><p className="aq-next-then">{choice === "t_watch" ? "先核对剩余持仓和这笔卖出的用途，再重新试算；只有实际买回后，才回录买入，不能把试算当成已完成。" : "先检查剩余仓位的风险；如需继续减仓，按当前余仓重新试算。"}</p>{choice === "hold" ? <button className="aq-btn" type="button" disabled={busy} onClick={() => void confirmObservation()}>已核对余仓，继续观察</button> : choice === "t_watch" ? <button className="aq-btn" type="button" onClick={() => requestOperation({ direction: "buy", code: data.symbol })}>我已实际买回 · 录入成交</button> : null}{data.basis.quote_issue || expired ? <p role="status">{data.basis.quote_issue || "行情已旧，先更新后再试算。"}</p> : null}</> : <>
          {!expired && !simple.paused && !journey?.attention ? <><p className="aq-next-then"><b>接下来：</b>{simple.next}</p>{simple.then ? <p className="aq-next-then">{simple.then}</p> : null}</> : null}
          <p className="aq-next-cancel"><b>{choice === "t_watch" ? "什么时候不做了" : "什么时候重新考虑"}</b>{expired ? "旧价位只作参考，更新后再继续。" : simple.stop}</p>
        </>}
        <div className="aq-plan-meta"><span>{formatEvidenceTime(data.basis.quote_as_of)} · 可卖 {data.basis.sellable_shares ?? "待核对"} 股</span><button className="aq-btn" type="button" disabled={busy} onClick={() => void refresh()}>{busy ? "更新中…" : "更新行情"}</button></div>
        <p className="aq-note">页面可见时每分钟读取。后台只跟进已保存的观察价，检查时点见上方记录。{editedPrices.current.sell_price || editedPrices.current.buy_price ? "含你设定的观察价。" : "当前试算参考已发生的高低点，保存前不作为后台观察线。"}</p>
      </div>
      {choice === "t_watch" ? <p className="aq-note">做 T 是“先卖、后买”的差价尝试。操作前先试算费用；可能买不回来，也可能继续亏损。</p> : null}
      {choice !== "hold" ? <details className="aq-plan-calculator" ref={calculator}>
        <summary>{showResult ? "调整这次试算" : choice === "t_watch" ? "算一下：这次做 T 能减少多少亏损" : "算一下：卖出后还剩多少"}</summary>
        <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <p className="aq-note">这里只试算，不记录成交。价格可调整，股数由你填写。</p>{data.quantity_bounds ? <p className="aq-note">当前可卖 {data.quantity_bounds.sellable_shares} 股；做 T 回补至少 {data.quantity_bounds.buy_lot} 股。{data.quantity_bounds.note}</p> : null}
          <div className="aq-action-plan-fields">{field("quantity", "这次股数")}{field("sell_price", "打算卖到多少元")}{choice === "t_watch" ? field("buy_price", "打算多少元买回") : null}</div>
          <details className="aq-action-plan-fees">
            <summary>{confirmed ? "交易费用 · 已确认" : "交易费用 · 试算前需填写一次"}</summary>
            <p className="aq-note">按你的券商收费填写；填 0 表示本次不计该项。</p>
            <div className="aq-action-plan-fields">{field("commission_rate", "每边佣金（%）")}{field("min_commission", "每边最低佣金（元）")}{field("stamp_tax_rate", "卖出税费（%）")}{field("slippage_rate", "预留成交价偏差（%）")}</div>
            <label className="aq-action-plan-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={(e) => { setConfirmed(e.target.checked); dirty.current = true; setSaved(false); }} />我已核对这些费用</label>
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
      <details className="aq-plan-help" onToggle={(event) => { if (event.currentTarget.open) setModelMounted(true); }}><summary>还拿不准？让 AI 帮你分析</summary>{modelMounted ? <PositionDiscussionPanel key={JSON.stringify([planTarget, choice, data.position_snapshot, data.review_plan.parameters])} target={planTarget} choice={choice} worksheet={data} disabled={discussionBlocked} disabledReason={discussionBlockReason} /> : null}</details>
      <details className="aq-plan-help"><summary>查看价位来源和完整条件</summary><p className="aq-note">这些价格来自已发生的高低点，只作观察参考，不代表到价就该买卖。</p>{data.basis.references.map((row) => <p key={row.label}>{row.label} {row.price.toFixed(2)} 元 · {row.source} · {formatEvidenceTime(row.as_of)}</p>)}<ul>{steps.map((step) => <li key={step}>{step}</li>)}</ul></details>
    </> : !error ? <p role="status">正在读取持仓和行情…</p> : null}
  </section>;
}
