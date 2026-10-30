import type { CandidateDiscussion, PositionDiscussion } from "@/lib/api";
import { discussionFailureReasons } from "@/lib/discussion-failures";

export function DiscussionFailures({ result }: { result: PositionDiscussion | CandidateDiscussion }) {
  const failures = discussionFailureReasons(result);
  if (!failures.length) return null;
  const content = <><ul>{failures.map((row) => <li key={row.label}><b>{row.label}：</b>{row.detail}</li>)}</ul><p className="aq-note">本次未采用这些意见，原方案未变。无需修改持仓或费用来重试分析。</p></>;
  return result.summary ? <details className="aq-discussion-failures"><summary>{failures.length} 类原因影响分析</summary>{content}</details>
    : <section className="aq-discussion-failures" aria-label="本次分析未完成的原因"><h5>分析未完成的原因</h5>{content}</section>;
}
