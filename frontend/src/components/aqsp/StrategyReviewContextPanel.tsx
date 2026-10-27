import type { StrategyReviewContext } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";
import { originalReviewPrice, reviewEvidenceText, strategyReviewView } from "@/lib/strategy-review-context";

/** Server context only: no invented changes or implicit adoption. */
export function StrategyReviewContextPanel({ context }: { context?: StrategyReviewContext }) {
  const view = strategyReviewView(context);
  if (!view || !context) return null;
  const original = view.originalVisible ? context.original : null;
  return <details className="aq-strategy-review-context"><summary>原方案与当前依据对比</summary><h5>{view.title}</h5>
    <p>{view.state}</p>
    {original ? <div className="aq-strategy-review-original"><h5>当时采用的方案</h5><p><b>{reviewEvidenceText(original.choice)}</b>{original.saved_at ? ` · 保存于 ${formatEvidenceTime(original.saved_at)}` : " · 保存时点待补"}</p>
      <dl><div><dt>等待条件</dt><dd>{original.condition || "原条件未保存"}</dd></div><div><dt>何时取消或重核</dt><dd>{original.invalidation || "原失效条件未保存"}</dd></div>
        <div><dt>原观察价</dt><dd>{original.comparator ? `${original.comparator === "at_or_above" ? "达到或高于" : "达到或低于"} ` : ""}{originalReviewPrice(context)}<small>仅用于对比；新条件以本次有效价格引用为准。</small></dd></div></dl>
      {!original.snapshot_complete ? <p role="status">原采用时的证据没有完整保存，缺失部分保持未知。</p> : null}
    </div> : null}
    {view.originalVisible && context.changes.length ? <div className="aq-strategy-review-changes">{context.changes.map((change, index) => <article key={`${change.category}:${index}`}><h5>{change.label}</h5><dl><div><dt>采用时</dt><dd>{reviewEvidenceText(change.before)}</dd></div><div><dt>这次</dt><dd>{reviewEvidenceText(change.after)}</dd></div></dl></article>)}</div> : null}
    {context.missing_evidence.length ? <p>仍缺依据：{context.missing_evidence.join("；")}</p> : null}
    {context.notice ? <p className="aq-note">{context.notice}</p> : null}
  </details>;
}
