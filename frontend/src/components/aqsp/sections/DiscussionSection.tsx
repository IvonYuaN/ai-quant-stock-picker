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
import { roleLabel } from "@/lib/display-labels";
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


/** 同一列若多行文案完全相同，不重复长句：统一「同上」，真实文案在表下给一次。 */
function sameAcross(values: readonly string[]): { same: boolean; text: string } {
  const uniq = [...new Set(values.map((value) => value.trim()).filter(Boolean))];
  if (uniq.length === 1 && values.length > 1) return { same: true, text: uniq[0] };
  return { same: false, text: "" };
}

function DebateCard({ results, showFootNotes, showNoDisagreement }: { results: readonly AqspAgentResult[]; showFootNotes: boolean; showNoDisagreement: boolean }) {
  // 同一批候选常由同一份讨论结论覆盖（跨市叙事一致时尤其如此）。
  // 内容完全相同的票合并成一张卡并标明共享，避免同一段话重复 5 遍。
  const result = results[0];
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
  const shared = results.length > 1;

  return (
    <Card accent="info" className="aq-debate-card">
      <div className="aq-card-head">
        <div className="aq-card-title aq-agent-title">
          <span className="aq-agent-mark">
            <Bot aria-hidden="true" />
          </span>
          <div>
            <h3>
              {shared
                ? `${result.display_name || result.symbol || "对象未记录"} 等 ${results.length} 只`
                : (result.display_name || result.symbol || "对象未记录")}
            </h3>
            <span className="aq-code">
              {shared ? results.map((item) => item.symbol).filter(Boolean).join(" · ") : (result.symbol || "代码未记录")}
            </span>
          </div>
        </div>
        <Badge tone={hasDisagreement ? "ok" : "warn"}>{hasDisagreement ? "有个股分歧" : "待补个股反驳"}</Badge>
      </div>

      {shared ? (
        <p className="aq-note">这 {results.length} 只候选共用同一份讨论结论（逐票差异化复核未产生差异）。</p>
      ) : null}

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
                  <Tag key={role}>{roleLabel(role)}</Tag>
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
          ) : showNoDisagreement ? (
            <p className="aq-tone-warn aq-note">
              没有个股级反驳证据，当前只保留规则复核，不宣称形成有效复合讨论。
            </p>
          ) : null}
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

      {showFootNotes && (result.primary_risk_gate || result.next_trigger) ? (
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
        <>
          {(() => {
            // 风险与下一验证常常逐票一字不差 —— 提到网格上方给一次，卡内不再重复。
            const groups = groupDebates(view.debates);
            const noDisagreementCount = groups.filter(
              (group) => !(group.results[0].disagreement_points?.length ?? 0),
            ).length;
            const riskSame = sameAcross(groups.map((group) => group.results[0].primary_risk_gate ?? ""));
            const nextSame = sameAcross(groups.map((group) => group.results[0].next_trigger ?? ""));
            const shared = riskSame.same || nextSame.same;
            return (
              <>
                {shared ? (
                  <div className="aq-table-notes">
                    {riskSame.text ? <p><b>共性风险</b><span>{riskSame.text}</span></p> : null}
                    {nextSame.text ? <p><b>共性下一验证</b><span>{nextSame.text}</span></p> : null}
                  </div>
                ) : null}
                {/* 多个角色都缺反驳证据时，这句固定说明在区头给一次就够了。 */}
                {noDisagreementCount > 1 ? (
                  <p className="aq-note aq-tone-warn">
                    {noDisagreementCount} 个角色没有个股级反驳证据，当前只保留规则复核，不宣称形成有效复合讨论。
                  </p>
                ) : null}
                <div className="aq-card-grid">
                  {groups.map((group) => (
                    <DebateCard
                      key={group.key}
                      results={group.results}
                      showFootNotes={!shared}
                      showNoDisagreement={noDisagreementCount <= 1}
                    />
                  ))}
                </div>
              </>
            );
          })()}
        </>
      )}
    </section>
  );
}

/**
 * 把结论 / 风险 / 下一验证完全一致的讨论并成一组。
 * 分组键只取"这只票自己的判断"，不含过程文案 —— 过程本来就允许逐票不同，
 * 但结论、风险与下一验证若一字不差，就说明这几只票实际共享同一份判断。
 */
function groupDebates(results: readonly AqspAgentResult[]): Array<{ key: string; results: AqspAgentResult[] }> {
  const groups = new Map<string, AqspAgentResult[]>();
  for (const result of results) {
    const key = [result.conclusion.trim(), result.primary_risk_gate ?? "", result.next_trigger ?? ""].join("||");
    const bucket = groups.get(key) ?? [];
    bucket.push(result);
    groups.set(key, bucket);
  }
  return [...groups.entries()].map(([key, items]) => ({ key, results: items }));
}
