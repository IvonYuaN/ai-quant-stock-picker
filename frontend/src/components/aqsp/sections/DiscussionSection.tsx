// 讨论复核区块：多 Agent 委员会结论 + 可展开的讨论过程与个股证据。
//
// 版面原则：结论优先，过程下沉到抽屉。当天速读只需"结论 + 有没有个股分歧"，
// 细节留给需要复核的时候展开。
import { Bot, CircleAlert, ShieldAlert, Sparkles, UsersRound } from "lucide-react";
import { Badge, Card, EmptyState, SectionHeader, Tag } from "@/components/ui/primitives";
import {
  cleanDebateRoundText,
  debateProcessText,
  dedupeResearchText,
  sameResearchText,
} from "@/lib/research-view";
import { symbolNames, unique, type DailyView, type SectionView } from "@/lib/daily-view";
import type { AqspAgentResult } from "@/types/aqsp";

const BUCKET_LABELS: Readonly<Record<string, string>> = {
  bullish: "看多证据",
  bearish: "看空证据",
  event_fundamental: "事件/基本面",
  technical: "个股技术",
  strategy: "命中策略",
  risk_counterevidence: "风险/反证",
  uncertainty: "不确定性",
};

function DebateCard({ result }: { result: AqspAgentResult }) {
  const conclusion = result.conclusion.trim();
  const process = debateProcessText(result);
  const rounds = dedupeResearchText((result.round_summaries ?? []).map(cleanDebateRoundText))
    .filter((item) => !sameResearchText(item, conclusion) && !sameResearchText(item, process))
    .slice(0, 3);
  const buckets = Object.entries(result.viewpoint_buckets ?? {})
    .filter(([, points]) => points.length > 0)
    .slice(0, 6);
  const disagreement = unique(result.disagreement_points, 3);
  const hasDisagreement = disagreement.length > 0;

  return (
    <Card accent="info" className="aq-debate-card">
      <div className="aq-card-head">
        <div className="aq-card-title aq-agent-title">
          <span className="aq-agent-mark">
            <Bot aria-hidden="true" />
          </span>
          <div>
            <h3>{result.display_name || result.symbol || "对象未记录"}</h3>
            <span className="aq-code">{result.symbol || "代码未记录"}</span>
          </div>
        </div>
        <Badge tone={hasDisagreement ? "ok" : "warn"}>{hasDisagreement ? "有个股分歧" : "待补个股反驳"}</Badge>
      </div>

      <div className="aq-conclusion-block">
        <p className="aq-label">委员会结论</p>
        <strong>{conclusion || "暂无结论"}</strong>
      </div>

      <details className="aq-drawer">
        <summary className="aq-drawer-summary">
          <UsersRound aria-hidden="true" />
          多 Agent 过程与证据
        </summary>
        <div className="aq-drawer-body">
          <div className="aq-discussion">
            <p className="aq-label">
              <UsersRound aria-hidden="true" />
              讨论过程
            </p>
            <p>{process || "过程未记录"}</p>
            {result.active_roles.length > 0 ? (
              <div className="aq-tag-row">
                {result.active_roles.map((role) => (
                  <Tag key={role}>{role}</Tag>
                ))}
              </div>
            ) : null}
            {rounds.length > 0 ? (
              <ol className="aq-round-list">
                {rounds.map((round, index) => (
                  <li key={`${index}-${round}`}>第 {index + 1} 轮：{round}</li>
                ))}
              </ol>
            ) : null}
          </div>

          {buckets.length > 0 ? (
            <div className="aq-buckets">
              <p className="aq-label">个股证据</p>
              {buckets.map(([bucket, points]) => (
                <div key={bucket}>
                  <b>{BUCKET_LABELS[bucket] ?? bucket}</b>
                  <span>{points.slice(0, 2).join("；")}</span>
                </div>
              ))}
            </div>
          ) : null}

          {hasDisagreement ? (
            <div className="aq-disagreement">
              {disagreement.map((item) => (
                <span key={item}>
                  <CircleAlert aria-hidden="true" />
                  分歧：{item}
                </span>
              ))}
            </div>
          ) : (
            <p className="aq-tone-warn aq-note">
              没有个股级反驳证据，当前只保留规则复核，不宣称形成有效复合讨论。
            </p>
          )}
        </div>
      </details>

      <div className="aq-stat-row">
        <span>
          参与角色 <b>{result.active_roles.length}</b>
        </span>
        <span>
          复核轮次 <b>{result.round_count}</b>
        </span>
        <span>
          个股分歧 <b>{disagreement.length}</b>
        </span>
      </div>

      {result.primary_risk_gate || result.next_trigger ? (
        <div className="aq-foot-notes">
          {result.primary_risk_gate ? (
            <span>
              <ShieldAlert aria-hidden="true" />
              风险：{result.primary_risk_gate}
            </span>
          ) : null}
          {result.next_trigger ? (
            <span>
              <Sparkles aria-hidden="true" />
              下一验证：{result.next_trigger}
            </span>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

export function DiscussionSection({ view, section }: { view: DailyView; section: SectionView }) {
  const candidates = view.candidates.map((row) => ({ symbol: row.symbol, display_name: row.name }));
  const pending = view.researchChain
    ? symbolNames(view.researchChain.pending_review_symbols, candidates)
    : "";

  return (
    <section id={section.id} className="aq-section">
      <SectionHeader
        number={section.number}
        title={section.label}
        description={section.description}
        count={section.count}
      />

      {view.debates.length === 0 ? (
        <EmptyState
          title={section.empty?.title ?? "当天讨论未启动"}
          detail={
            <>
              <p>{pending ? `待复核标的：${pending}。` : "当前候选尚未进入有效复核。"}</p>
              <p>{section.empty?.detail}</p>
              {view.researchChain?.blocker ? <p>链路状态：{view.researchChain.blocker}</p> : null}
            </>
          }
          icon={ShieldAlert}
        />
      ) : (
        <div className="aq-card-grid">
          {view.debates.map((result) => (
            <DebateCard key={result.symbol} result={result} />
          ))}
        </div>
      )}
    </section>
  );
}
