import { useEffect, useRef, useState } from "react";
import { api, type ActionWorksheet, type PlanParameters, type ReviewChoice } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { planSteps, worksheetExpired } from "@/lib/position-plan";
import { PositionDiscussionPanel } from "./PositionDiscussionPanel";
import { ALERTS_CHANGED_EVENT } from "@/lib/operation";

type Draft = Record<"quantity" | "sell_price" | "buy_price" | "commission_rate" | "min_commission" | "stamp_tax_rate" | "slippage_rate", string>;
const EMPTY: Draft = { quantity: "", sell_price: "", buy_price: "", commission_rate: "", min_commission: "", stamp_tax_rate: "", slippage_rate: "" };
const titles = { hold: "持有：等待验证与退出条件", reduce: "减仓：分批退出计划", t_watch: "做 T：先卖后买的条件计划" };

export function PositionActionPlan({ eventId, choice }: { eventId: string; choice: ReviewChoice }) {
  const [data, setData] = useState<ActionWorksheet | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [tick, setTick] = useState(Date.now());
  const dirty = useRef(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  useEffect(() => { const timer = window.setInterval(() => setTick(Date.now()), 15_000); return () => clearInterval(timer); }, []);
  useEffect(() => {
    const version = ++generation.current;
    inFlight.current = true; setBusy(true);
    api.alertActionPlan(eventId).then((next) => {
      if (version !== generation.current) return;
      setData(next); setError("");
      if (next.review_plan.choice && next.review_plan.choice !== choice) window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
      if (!dirty.current) {
        const stored = next.review_plan.choice === choice ? next.review_plan.parameters : undefined;
        const high = next.basis.references.find((row) => row.label === "本交易日已见高点") ?? next.basis.references.find((row) => row.label === "最近完成日高点");
        const low = next.basis.references.find((row) => row.label === "本交易日已见低点") ?? next.basis.references.find((row) => row.label === "最近完成日低点");
        setDraft(stored ? { quantity: String(stored.quantity), sell_price: String(stored.sell_price), buy_price: stored.buy_price == null ? "" : String(stored.buy_price), commission_rate: String(stored.commission_rate * 100), min_commission: String(stored.min_commission), stamp_tax_rate: String(stored.stamp_tax_rate * 100), slippage_rate: String(stored.slippage_rate * 100) } : { ...EMPTY, sell_price: high ? high.price.toFixed(2) : "", buy_price: low ? low.price.toFixed(2) : "" });
        setConfirmed(stored?.fees_confirmed === true);
      }
    }).catch((reason) => { if (version === generation.current) setError(reason instanceof Error ? reason.message : "计划读取失败"); })
      .finally(() => { if (version === generation.current) { setBusy(false); inFlight.current = false; } });
    return () => { generation.current++; };
  }, [eventId, choice]);
  async function refresh() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    const version = ++generation.current;
    try { const next = await api.alertActionPlan(eventId); if (generation.current === version) { setData(next); setTick(Date.now()); if (next.review_plan.choice && next.review_plan.choice !== choice) window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT)); } }
    catch (reason) { if (generation.current === version) setError(reason instanceof Error ? reason.message : "刷新失败，输入已保留"); }
    finally { inFlight.current = false; if (generation.current === version) setBusy(false); }
  }
  async function save() {
    if (!data || inFlight.current) return;
    const required = Object.keys(draft).filter((key) => choice === "t_watch" || key !== "buy_price") as (keyof Draft)[];
    if (required.some((key) => !draft[key].trim() || !Number.isFinite(Number(draft[key]))) || !confirmed) { setError("请填完数量、价格和全部费用假设，并勾选确认；不清楚的费用请先核对券商。"); return; }
    const parameters: PlanParameters = { quantity: Number(draft.quantity), sell_price: Number(draft.sell_price), buy_price: choice === "t_watch" ? Number(draft.buy_price) : null, commission_rate: Number(draft.commission_rate) / 100, min_commission: Number(draft.min_commission), stamp_tax_rate: Number(draft.stamp_tax_rate) / 100, slippage_rate: Number(draft.slippage_rate) / 100, fees_confirmed: confirmed };
    inFlight.current = true; setBusy(true); setError(""); setSaved(false);
    const version = ++generation.current;
    try {
      await api.alertReviewPlan(eventId, choice, parameters, data.position_snapshot);
      const next = await api.alertActionPlan(eventId);
      if (generation.current !== version) return;
      setData(next); dirty.current = false; setSaved(true); setTick(Date.now());
      window.dispatchEvent(new Event(ALERTS_CHANGED_EVENT));
    } catch (reason) { if (generation.current === version) setError(reason instanceof Error ? reason.message : "测算保存失败，输入已保留"); }
    finally { inFlight.current = false; if (generation.current === version) setBusy(false); }
  }
  const expired = !!data && worksheetExpired(data.basis, tick);
  const blocked = !data || data.review_plan.choice !== choice || !!data.basis.quote_issue || expired || (choice === "t_watch" && (data.basis.joint_weakness || !data.basis.sellable_shares));
  function field(key: keyof Draft, label: string) {
    return <label>{label}<input aria-label={label} disabled={busy} type="number" min="0" step={key === "quantity" ? "1" : "any"} value={draft[key]} onChange={(e) => { dirty.current = true; setSaved(false); setDraft({ ...draft, [key]: e.target.value }); }} /></label>;
  }
  const steps = data ? planSteps(choice, data.basis, { sell_price: Number(draft.sell_price), buy_price: Number(draft.buy_price), quantity: Number(draft.quantity) }) : [];
  return <section className="aq-action-plan" aria-label={titles[choice]}>
    <div className="aq-action-plan-heading"><h4>{titles[choice]}</h4><button className="aq-btn" type="button" disabled={busy} onClick={() => void refresh()}>{busy ? "核对中…" : "刷新计划依据"}</button></div>
    {error ? <p role="alert">{error}。输入已保留，可刷新重试。</p> : null}
    {data ? <>
      <p className="aq-note">行情 {formatEvidenceTime(data.basis.quote_as_of)} · {data.basis.quote_source} · 现价 {data.basis.price?.toFixed(2) ?? "待补"} · 可卖底仓 {data.basis.sellable_shares ?? "待核对"} 股</p>
      {data.basis.quote_issue || expired ? <p role="status" className="aq-action-plan-warning">{data.basis.quote_issue || "计划依据需刷新"}；以下仅保留历史参考，暂停保存新测算。</p> : null}
      {data.basis.joint_weakness ? <p className="aq-action-plan-warning">三维同日走弱，暂停新增做 T 测算，优先复核风险处置。</p> : null}
      <PositionDiscussionPanel key={JSON.stringify([eventId, choice, data.position_snapshot, data.review_plan.parameters])} eventId={eventId} choice={choice} worksheet={data} disabled={busy || expired || !!data.basis.quote_issue || data.review_plan.choice !== choice || dirty.current || !!data.scenario_issue} />
      <ol>{steps.slice(0, 2).map((step) => <li key={step}>{step}</li>)}</ol><details className="aq-action-plan-conditions"><summary>暂停、费用与退出条件</summary><ul>{steps.slice(2).map((step) => <li key={step}>{step}</li>)}</ul></details>
      <details><summary>观察价位依据（不是已验证买卖信号）</summary>{data.basis.references.length ? data.basis.references.map((row) => <p key={row.label}>{row.label} {row.price.toFixed(2)} · {row.source} · {formatEvidenceTime(row.as_of)}</p>) : <p>缺少有效高低点，不能生成条件价；请刷新行情。可在数据完整后自行调整观察价。</p>}</details>
      {choice !== "hold" ? <form onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <p className="aq-note">价位预填自上方观察高低点，可自行调整；数量和费用需你确认。测算不记录买卖。</p>
        <div className="aq-action-plan-fields">{field("quantity", "本批数量（股）")}{field("sell_price", "假设卖出价（元）")}{choice === "t_watch" ? field("buy_price", "假设回补价（元）") : null}</div>
        <details className="aq-action-plan-fees" key={confirmed ? "confirmed" : "unconfirmed"} open={confirmed ? undefined : true}><summary>费用假设 · {confirmed ? "已确认，可调整" : "待填写确认"}</summary><div className="aq-action-plan-fields">{field("commission_rate", "双边佣金率（%）")}{field("min_commission", "单边最低佣金（元）")}{field("stamp_tax_rate", "卖出税费率（%）")}{field("slippage_rate", "单边滑点（%）")}</div></details>
        <label className="aq-action-plan-confirm"><input type="checkbox" disabled={busy} checked={confirmed} onChange={(e) => { setConfirmed(e.target.checked); dirty.current = true; setSaved(false); }} />已核对费用假设（显式填 0 表示不计该项），不代表实际成交费用</label>
        <button type="submit" className="aq-btn" disabled={busy || blocked}>保存并测算本批计划</button>
        {saved ? <p role="status">计划已保存并重新测算，持仓未变更。</p> : null}
      </form> : null}
      {data.scenario_issue ? <p role="status" className="aq-action-plan-warning">{data.scenario_issue}</p> : null}
      {data.scenario && data.review_plan.choice === choice && !dirty.current ? <div className="aq-action-plan-result"><h4>情景测算 · {expired || data.basis.quote_issue ? "旧依据，需刷新" : "以本批假设成交为前提"}</h4>
        {choice === "t_watch" && !data.scenario.round_trip_positive ? <p className="aq-action-plan-warning">净差价不为正，这笔做 T 没有降低经济成本。</p> : null}
        <dl><div><dt>{choice === "t_watch" ? "本批净差价" : "卖出净回收"}</dt><dd>{data.scenario.net_cash.toFixed(2)} 元</dd></div>{data.scenario.realized_pnl != null ? <div><dt>本批预计已实现盈亏</dt><dd>{data.scenario.realized_pnl.toFixed(2)} 元</dd></div> : null}<div><dt>预计往返/单边成本</dt><dd>{data.scenario.estimated_fees.toFixed(2)} 元</dd></div><div><dt>完成后持股</dt><dd>{data.scenario.remaining_shares} 股</dd></div>{data.scenario.cost_reduction_per_share != null ? <div><dt>本批每股经济成本减少</dt><dd>{data.scenario.cost_reduction_per_share.toFixed(4)} 元/股</dd></div> : null}<div><dt>测算经济成本</dt><dd>{data.scenario.economic_cost?.toFixed(4) ?? "已无剩余仓位"}{data.scenario.economic_cost != null ? " 元/股" : ""}</dd></div></dl>
        <p className="aq-note">{data.scenario.note}</p></div> : null}
    </> : !error ? <p role="status">正在读取真实持仓与行情依据…</p> : null}
  </section>;
}
