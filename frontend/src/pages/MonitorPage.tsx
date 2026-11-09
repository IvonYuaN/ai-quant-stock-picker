// 监控：持仓风险提醒、后台跟进状态与候选观察记录。
//
// 2026-11-09 新增模块（此前分散在今日页的折叠区与两个零散入口）。
// 三块内容全部平铺：风险提醒、持仓跟进、候选观察。
import { useEffect, useState } from "react";
import { BellRing } from "lucide-react";
import { LoadingState, SectionHeader } from "@/components/ui/primitives";
import { AlertInbox } from "@/components/aqsp/AlertInbox";
import { CandidateObservationList } from "@/components/aqsp/CandidateObservationList";
import { api, type FollowupObservation } from "@/lib/api";
import { formatEvidenceTime } from "@/lib/format";

const STATE_LABEL: Record<string, string> = {
  waiting: "等待条件",
  price_watch: "到价待复核",
  below_watch: "跌破观察线",
  paused: "已暂停",
  needs_review: "待复核",
  evidence_unavailable: "证据缺失",
  missing_prices: "缺报价",
  exited: "已了结",
  unplanned: "未纳入方案",
};

export function MonitorPage() {
  const [followups, setFollowups] = useState<FollowupObservation[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .positionFollowups()
      .then((value) => { if (alive) setFollowups(value.items ?? []); })
      .catch((reason: unknown) => {
        if (alive) setError(reason instanceof Error ? reason.message : "读取失败");
      });
    return () => { alive = false; };
  }, []);

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow"><BellRing aria-hidden="true" /> AQSP · 决策模块</p>
          <div className="aq-title-row">
            <h1>监控</h1>
          </div>
          <p className="aq-page-sub">持仓风险、后台跟进与候选观察的完整记录；到价只表示需要复核，不代表成交。</p>
        </div>
      </header>

      <section className="aq-section" aria-label="持仓风险提醒">
        <SectionHeader number="01 · 风险提醒" title="持仓风险判定" description="按 L1/L5/L6 规则判定的完整提醒记录。" />
        <AlertInbox />
      </section>

      <section className="aq-section" aria-label="后台跟进">
        <SectionHeader number="02 · 后台跟进" title="持仓方案的跟进状态" description="每条持仓方案当前处在什么状态、记录时点是什么。" />
        {error ? <p className="aq-note">读取失败：{error}</p> : null}
        {!followups ? <LoadingState label="正在读取跟进状态…" /> : null}
        {followups && followups.length === 0 ? <p className="aq-note">当前没有需要跟进的持仓方案。</p> : null}
        {followups && followups.length > 0 ? (
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>持仓</th>
                  <th>状态</th>
                  <th>标题</th>
                  <th>记录时点</th>
                  <th>报价时点</th>
                </tr>
              </thead>
              <tbody>
                {followups.map((item) => (
                  <tr key={item.position_id}>
                    <td><strong>{item.name}</strong> <span className="aq-code">{item.symbol}</span></td>
                    <td>{STATE_LABEL[item.state] ?? item.state}</td>
                    <td>{item.title}</td>
                    <td>{formatEvidenceTime(item.observed_at) || "未记录"}</td>
                    <td>{item.quote_as_of ? formatEvidenceTime(item.quote_as_of) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="aq-section" aria-label="候选观察">
        <SectionHeader number="03 · 候选观察" title="观察条件与复核记录" description="保存下来的观察条件、复核时间与后台重检结果。" />
        <CandidateObservationList />
      </section>
    </div>
  );
}
