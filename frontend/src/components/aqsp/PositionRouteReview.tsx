import { useEffect, useRef, useState } from "react";
import { api, type ActionWorksheet, type ReviewChoice } from "@/lib/api";
import { PLAN_CHOICES, worksheetExpired } from "@/lib/position-plan";
import { browserAlertRefreshSource, subscribeAlertRefresh } from "@/lib/alert-refresh";
import { formatEvidenceTime } from "@/lib/format";
import { PositionDiscussionPanel } from "./PositionDiscussionPanel";

const statuses = { review: "可以继续核对", risk_review: "先核对风险", paused: "当前暂停", unknown: "依据待核实" };
const evidenceLabels: Record<string, string> = { market: "大盘", sector: "板块", stock: "个股" };

export function PositionRouteReview({ eventId, busy, onChoose }: {
  eventId: string; busy: boolean; onChoose: (choice: ReviewChoice) => void;
}) {
  const [worksheet, setWorksheet] = useState<ActionWorksheet | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [retry, setRetry] = useState(0);
  const [tick, setTick] = useState(Date.now());
  const generation = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => {
    let active = true;
    async function load() {
      if (inFlight.current) return;
      inFlight.current = true; setLoading(true);
      const version = ++generation.current;
      try {
        const next = await api.alertActionPlan(eventId);
        if (active && version === generation.current) { setWorksheet(next); setError(""); setTick(Date.now()); }
      } catch (reason) {
        if (active && version === generation.current) setError(reason instanceof Error ? reason.message : "分析依据读取失败");
      } finally { inFlight.current = false; if (active && version === generation.current) setLoading(false); }
    }
    const stop = subscribeAlertRefresh(() => void load(), browserAlertRefreshSource());
    const timer = window.setInterval(() => setTick(Date.now()), 15_000);
    return () => { active = false; generation.current++; stop(); clearInterval(timer); };
  }, [eventId, retry]);
  const review = worksheet?.route_review;
  const expired = !!worksheet && worksheetExpired(worksheet.basis, tick);
  return <div className="aq-route-review" aria-label="选择前的持仓分析">
    {loading && !worksheet ? <p role="status">正在核对这只持仓的当前依据…</p> : null}
    {error ? <div role="alert"><p>{error}。当前分析待确认，可重新读取或先记录观察意向。</p><button className="aq-btn" type="button" disabled={loading} onClick={() => setRetry((value) => value + 1)}>重新读取依据</button></div> : null}
    {review ? <>
      <div className="aq-route-diagnosis"><span className="aq-beginner-label">这只持仓，先看什么</span><strong>{review.title}</strong><p>{review.summary}</p>
        <details><summary>当前证据与缺口</summary>{Object.entries(review.evidence).map(([key, row]) => <p key={key}><b>{evidenceLabels[key]}</b> · {row.summary}<small>{row.source || "缺少可核验来源"} · {formatEvidenceTime(row.as_of) || "时间待核验"}</small></p>)}<p>还缺：{review.missing_evidence.join("、")}</p>{review.recovery_to_cost_pct != null ? <p>按本次有效价计算，回到当前记录成本需上涨约 {review.recovery_to_cost_pct.toFixed(1)}%；这只是价格差额计算，不代表能够回本。</p> : null}</details>
      </div>
      {worksheet ? <PositionDiscussionPanel key={JSON.stringify([eventId, worksheet.position_snapshot])} eventId={eventId} choice={null} worksheet={worksheet} disabled={busy || loading || !!error || expired} disabledReason={expired ? "这批读取依据已旧，更新后再分析。" : "正在核对当前依据，请稍等。"} /> : null}
    </> : null}
    {expired ? <p className="aq-note" role="status">这批分析已旧，等待下一次更新；以下保留上次读取内容。</p> : null}
    <div className="aq-position-plan-options aq-beginner-options aq-route-choices" role="group" aria-label="选择处理方式">{(Object.keys(PLAN_CHOICES) as ReviewChoice[]).map((choice) => {
      const route = review?.routes.find((row) => row.choice === choice);
      return <div className="aq-route-option" key={choice}>
        <div className="aq-route-option-heading"><b>{PLAN_CHOICES[choice].title}</b>{route ? <span>{statuses[route.status]}</span> : null}</div>
        <p>{route?.reason || PLAN_CHOICES[choice].benefit}</p><small>代价：{PLAN_CHOICES[choice].cost}</small>
        {route ? <details><summary>选择前还要核对什么</summary><p>{route.needs.join("；")}</p></details> : null}
        <button type="button" className="aq-btn" disabled={busy} onClick={() => onChoose(choice)}>{route?.status === "paused" ? "仅记录观察意向" : `选择${PLAN_CHOICES[choice].title}`}</button>
      </div>;
    })}</div>
  </div>;
}
