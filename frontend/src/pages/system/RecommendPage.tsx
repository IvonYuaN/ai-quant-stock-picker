// 今日推荐：系统线。展示当天通过推荐门禁的候选。
// 门禁不过时明确说明原因——宁可空着，也不用历史候选凑数（AGENTS 红线）。
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { cn } from "@/lib/utils";

export function RecommendPage() {
  const { data, loading } = useAqspSnapshot();
  const gate = data?.recommendation_gate;
  const candidates = data?.candidates ?? [];
  const blocked = gate ? !gate.recommendation_allowed : false;

  return (
    <div className="aqsp-page">
      <div className="vr-line-head">
        <div>
          <h1>今日推荐</h1>
          <p>{data?.selected_date ? `数据日期 ${data.selected_date}` : "等待快照"}</p>
        </div>
        <span className="vr-chip">公共只读 · 所有人一致</span>
      </div>

      {loading && !data ? <div className="vr-state-panel">正在读取推荐数据</div> : null}

      {data ? (
        <div className={cn("vr-gate-block", !blocked && "vr-gate-open")}>
          {blocked ? <AlertTriangle className="h-4 w-4 shrink-0" /> : <CheckCircle2 className="h-4 w-4 shrink-0" />}
          <div>
            <strong>{gate?.status || "门禁状态未记录"}</strong>
            {gate?.reasons?.length ? <div>{gate.reasons.join("；")}</div> : null}
          </div>
        </div>
      ) : null}

      {data && candidates.length === 0 ? (
        <div className="vr-state-panel">当天没有候选，不展示历史内容。</div>
      ) : null}

      {candidates.length > 0 ? (
        <table className="vr-table">
          <thead>
            <tr>
              <th>代码 / 名称</th>
              <th>评分</th>
              <th>研究状态</th>
              <th>下一步</th>
            </tr>
          </thead>
          <tbody>
            {candidates.map((c) => (
              <tr key={c.symbol}>
                <td>
                  <strong>{c.display_name || c.symbol}</strong>
                  <div className="vr-chip">{c.symbol}</div>
                </td>
                <td>{c.score.toFixed(2)}</td>
                <td>{c.research_status || "—"}</td>
                <td>{c.next_step || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}
