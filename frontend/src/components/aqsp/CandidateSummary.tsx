// 今日候选摘要：总览页只给一张可判断的紧凑名单，全量证据链在「候选池」模块。
//
// 2026-11-09：原先这里直接嵌了整个候选区（名单 + 详情卡 + 逐票证据链 + 对比），
// 总览页因此又长又散。摘要表只保留判断需要的字段，其余交给候选池页。
import { Link } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { SectionHeader } from "@/components/ui/primitives";
import { statusLabel } from "@/lib/display-labels";
import { stockResearchHref } from "@/lib/research-route";
import type { DailyView } from "@/lib/daily-view";

const PREVIEW_LIMIT = 8;

/** 同一列若多行文案完全相同，表内不重复长句：统一「同上」，真实文案在表下给一次。 */
function sameAcross(values: readonly string[]): { same: boolean; text: string } {
  const uniq = [...new Set(values.map((value) => value.trim()).filter(Boolean))];
  if (uniq.length === 1 && values.length > 1) return { same: true, text: uniq[0] };
  return { same: false, text: "" };
}

export function CandidateSummary({
  view,
  onPick,
}: {
  view: DailyView;
  onPick?: (symbol: string) => void;
}) {
  const rows = view.candidates.slice(0, PREVIEW_LIMIT);
  const rest = view.candidates.length - rows.length;
  // 当天的状态与下一观察常常逐只相同，逐行重复同一句话等于噪音。
  const statusSame = sameAcross(rows.map((row) => statusLabel(row.status)));
  const nextSame = sameAcross(rows.map((row) => row.nextStep || ""));
  return (
    <section id="candidates" className="aq-section" aria-label="今日候选摘要">
      <SectionHeader
        number="01 · 候选"
        title="今日候选摘要"
        description="按评分排序的当日候选；证据链与并排对比在「候选」模块里展开。"
        count={`${view.candidates.length} 只`}
      />
      {rows.length === 0 ? (
        <p className="aq-note">当天没有候选。切换上方日期可回看历史快照。</p>
      ) : (
        <>
          <div className="aq-table-wrap">
            <table className="aq-table aq-summary-table">
              <thead>
                <tr>
                  <th>候选</th>
                  <th className="aq-num">评分</th>
                  <th>状态</th>
                  <th className="aq-num">消息</th>
                  <th className="aq-num">复核</th>
                  <th>下一观察</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.symbol}
                    onClick={onPick ? () => onPick(row.symbol) : undefined}
                    style={onPick ? { cursor: "pointer" } : undefined}
                  >
                    <td>
                      <Link to={stockResearchHref(row.symbol, view.date)} onClick={(event) => event.stopPropagation()}>
                        <strong>{row.name}</strong>
                      </Link>
                      <span className="aq-code">{row.symbol}</span>
                    </td>
                    <td className="aq-num">{row.scoreText}</td>
                    <td>{statusSame.same ? "同上" : statusLabel(row.status)}</td>
                    <td className="aq-num">{row.messageCount}</td>
                    <td className="aq-num">
                      {row.debateRounds !== null ? `${row.debateRounds} 轮` : "待复核"}
                    </td>
                    <td>{nextSame.same ? "同上" : (row.nextStep || "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="aq-table-notes">
            {statusSame.text ? (
              <p><b>状态（各行相同）</b><span>{statusSame.text}</span></p>
            ) : null}
            {nextSame.text ? (
              <p><b>下一观察（各行相同）</b><span>{nextSame.text}</span></p>
            ) : null}
          </div>
          <div className="aq-row-actions">
            <Link className="aq-btn aq-btn-primary" to="/candidates">
              进入候选模块 <ArrowRight aria-hidden="true" />
            </Link>
            {rest > 0 ? <span className="aq-hint">另有 {rest} 只在候选模块里</span> : null}
          </div>
        </>
      )}
    </section>
  );
}
