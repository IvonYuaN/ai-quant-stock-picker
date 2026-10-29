import type { CandidateCondition, CandidateDiscussion, CandidateWorkspace } from "@/lib/api";
import { candidateActionChecks, candidateChecksView } from "@/lib/candidate-conditions";
import { formatEvidenceTime } from "@/lib/format";
import { Tag } from "@/components/ui/primitives";

export function CandidateActionChecks({ action, result, workspace }: { action: CandidateCondition; result: CandidateDiscussion; workspace: CandidateWorkspace }) {
  const view = candidateActionChecks(action, result, workspace);
  return <div className="aq-candidate-registered" aria-label="这项方案的监控范围">
    {view.prerequisites.length ? <div><p><b>后台核对的前提</b></p><div className="aq-tag-row">{view.prerequisites.map((label, index) => <Tag key={`${label}:${index}`}>{label}</Tag>)}</div></div> : null}
    {view.invalidations.length ? <div><p><b>触发后复核的失效条件</b></p><div className="aq-tag-row">{view.invalidations.map((label, index) => <Tag key={`${label}:${index}`} tone="warn">{label}</Tag>)}</div></div> : null}
    <p className="aq-detail-muted">{view.manual}</p>
  </div>;
}

export function CandidateConditionChecks({ workspace }: { workspace: CandidateWorkspace }) {
  const view = candidateChecksView(workspace);
  if (!view) return null;
  return <section className="aq-candidate-checks" aria-label="候选条件逐项检查"><h4>{view.title}</h4><p>{view.nextStep}</p>
    {view.items.length ? <ul>{view.items.map((row, index) => <li key={`${row.role}:${row.id}:${index}`}>
      <div className="aq-candidate-check-head"><span>{row.label}<small>{row.roleLabel}</small></span><strong className={row.tone === "warn" ? "aq-tone-warn" : undefined}>{row.statusLabel}</strong></div>
      {row.detail || row.source || row.as_of || row.value != null ? <details><summary>依据与时点</summary>
        {row.detail ? <p>{row.detail}</p> : null}
        <small>{row.source || "来源待补"} · {formatEvidenceTime(row.as_of) || "时点待核对"}</small>
      </details> : null}
    </li>)}</ul> : null}
  </section>;
}
