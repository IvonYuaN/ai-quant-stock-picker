import type { AqspSnapshot } from "@/types/aqsp";
import { researchValidationContext } from "@/lib/research-validation-context";
import { formatAqspTime } from "./useAqspSnapshot";

/** Read-only explanation beside the existing discussion/observation workspace. */
export function ResearchValidationContext({ snapshot, symbol, now }: { snapshot: AqspSnapshot; symbol: string; now?: number }) {
  const view = researchValidationContext(snapshot, symbol, now);
  return <div aria-label="策略验证与候选研究状态">
    <p className="aq-card-summary">{view.summary}</p>
    {view.sourceRun ? <p className="aq-detail-muted">{view.sourceRun.status}。</p> : null}
    <details className="aq-drawer">
      <summary className="aq-drawer-summary">查看验证依据与缺口</summary>
      <div className="aq-drawer-body">
        <dl className="aq-research-kv">
          <div><dt>历史策略验证 · 截至 {view.history.date}</dt><dd>{view.history.status}</dd></div>
          {view.refresh ? <div><dt>本次验证刷新 · 目标截至 {view.refresh.endDate}</dt><dd>{view.refresh.status}</dd><p className="aq-detail-muted">{view.refresh.detail}</p><p className="aq-detail-muted">运行记录：{formatAqspTime(view.refresh.generatedAt)}</p></div> : null}
          <div><dt>候选研究日 · {view.candidate.date}</dt><dd>{view.candidate.discussion}</dd><p className="aq-detail-muted">{view.candidate.validation}</p></div>
          <div><dt>正式推荐资格</dt><dd>{view.recommendation.status}</dd></div>
        </dl>
        <p>{view.nextStep}</p>
        {view.recommendation.reasons[0] ? <p className="aq-detail-muted">{view.recommendation.reasons[0]}</p> : null}
        <p>{view.history.detail}</p>
        <p className="aq-detail-muted">{view.parameterDetail}</p>
        <p className="aq-detail-muted">快照生成：{formatAqspTime(snapshot.generated_at)}；候选数据取回：{formatAqspTime(view.candidate.sourceTime)}。取回时间不代表历史验证截至日。</p>
        {view.sourceRun ? <p className="aq-detail-muted">{view.sourceRun.detail} 研究任务记录：{formatAqspTime(view.sourceRun.generatedAt)}。</p> : null}
        {view.gaps.length ? <><p className="aq-label">还缺什么</p><ul className="aq-reason-list">{view.gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul></> : null}
        {view.recommendation.reasons.length > 1 ? <><p className="aq-label">其他门禁记录</p><ul className="aq-reason-list">{view.recommendation.reasons.slice(1).map((reason) => <li key={reason}>{reason}</li>)}</ul></> : null}
      </div>
    </details>
  </div>;
}
