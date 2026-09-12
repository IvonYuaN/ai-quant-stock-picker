// 今日页顶部的市场环境条：一眼看到"今天外面什么脸色"。
//
// 这是判断"能不能动"的前提，此前完全缺失。取不到数据时**不占位**（返回 null），
// 避免用一片空白暗示"市场没有行情"。
import { Link } from "react-router-dom";
import { ArrowRight, Globe2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge, StatePanel } from "@/components/ui/primitives";
import { changeClass, formatSignedPct } from "@/lib/format";
import { marketStripSummary } from "@/lib/market-view";
import { useMarketData } from "./useMarketData";

export function MarketStrip() {
  const { view, loading, failedSections } = useMarketData();
  const summary = marketStripSummary(view);

  if (loading && !summary) {
    return <StatePanel>正在读取市场环境（指数 / 涨跌家数 / 隔夜外围）…</StatePanel>;
  }

  // 部分源失败**必须说出来**，哪怕主条还能显示 ——
  // 否则用户看到的是一条"看起来完整"的环境条，却不知道涨跌家数/情绪其实没取到。
  const failureNote =
    failedSections.length > 0 ? (
      <StatePanel tone="warn">
        市场环境部分源未取到：{failedSections.join("、")}。以下仅为已取到的部分，不是完整环境。
      </StatePanel>
    ) : null;

  if (!summary) return failureNote;

  return (
    <>
      {failureNote}
    <div className="aq-market-strip" aria-label="市场环境">
      <div className="aq-market-strip-main">
        <div className="aq-index-chips">
          {summary.indices.map((item) => (
            <span className="aq-index-chip" key={item.key}>
              <b>{item.name}</b>
              <span className="aq-num">{item.price.toFixed(2)}</span>
              <span className={cn("aq-num", changeClass(item.changePct))}>{formatSignedPct(item.changePct)}</span>
            </span>
          ))}
        </div>

        {summary.breadth ? (
          <div className="aq-breadth-chips">
            <Badge tone={summary.tone}>{summary.headline}</Badge>
            <span className="aq-breadth-item">
              涨停 <b className="aq-tone-up">{summary.breadth.ztReal || summary.breadth.zt}</b>
            </span>
            <span className="aq-breadth-item">
              跌停 <b className="aq-tone-down">{summary.breadth.dtReal || summary.breadth.dt}</b>
            </span>
            {summary.breadth.active ? (
              <span className="aq-breadth-item">
                活跃度 <b>{summary.breadth.active}</b>
              </span>
            ) : null}
            {summary.breadth.speculation ? (
              <span className="aq-breadth-item">
                题材 <b>{summary.breadth.speculation}</b>
              </span>
            ) : null}
          </div>
        ) : null}

        {summary.global.length > 0 ? (
          <div className="aq-global-chips">
            <Globe2 aria-hidden="true" />
            {summary.global.slice(0, 5).map((item) => (
              <span className="aq-global-chip" key={item.key}>
                <b>{item.name}</b>
                <span className={cn("aq-num", changeClass(item.changePct))}>{formatSignedPct(item.changePct)}</span>
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <Link className="aq-strip-link" to="/market">
        市场环境
        <ArrowRight aria-hidden="true" />
      </Link>
    </div>
    </>
  );
}
