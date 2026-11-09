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

export function CandidateSummary({
  view,
  onPick,
}: {
  view: DailyView;
  onPick?: (symbol: string) => void;
}) {
  const rows = view.candidates.slice(0, PREVIEW_LIMIT);
  const rest = view.candidates.length - rows.length;
  return (
    <section id="candidates" className="aq-section" aria-label="今日候选摘要">
      <SectionHeader
        number="01 · 候选"
        title="今日候选摘要"
        description="按评分排序的当日候选；证据链与并排对比在「候选池」模块里展开。"
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
                    <td>{statusLabel(row.status)}</td>
                    <td className="aq-num">{row.messageCount}</td>
                    <td className="aq-num">
                      {row.debateRounds !== null ? `${row.debateRounds} 轮` : "待复核"}
                    </td>
                    <td>{row.nextStep || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="aq-row-actions">
            <Link className="aq-btn aq-btn-primary" to="/candidates">
              查看候选池 <ArrowRight aria-hidden="true" />
            </Link>
            {rest > 0 ? <span className="aq-hint">另有 {rest} 只在候选池里</span> : null}
          </div>
        </>
      )}
    </section>
  );
}
