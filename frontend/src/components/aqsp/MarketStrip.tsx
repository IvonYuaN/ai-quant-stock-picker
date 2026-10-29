// 今日页顶部的市场环境条：一眼看到"今天外面什么脸色"。
//
// 市场依据缺失时明确保留未知，不能用空白暗示环境没有风险。
import { Link } from "react-router-dom";
import { ArrowRight, Globe2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { StatePanel } from "@/components/ui/primitives";
import { changeClass, formatEvidenceTime, formatSignedPct } from "@/lib/format";
import { marketStripSummary } from "@/lib/market-view";
import { MARKET_SECTION_LABELS } from "@/lib/market-load";
import { useMarketData } from "./useMarketData";

export function MarketStrip() {
  const { view, loading, failedSections, pendingSections } = useMarketData();
  const summary = marketStripSummary(view);
  const leadingIndustries = view?.industryTop.filter((item) => item.changePct !== null).slice(0, 3) ?? [];
  const laggingIndustries = view?.industryBottom.filter((item) => item.changePct !== null).slice(0, 3) ?? [];
  const hasIndustryRows = leadingIndustries.length > 0 || laggingIndustries.length > 0;
  const industryRequestFailed = failedSections.includes("行业涨跌榜");
  const industryPending = pendingSections.includes("industry");
  const headline = pendingSections.includes("overview") && !summary?.breadth
    ? "市场宽度仍在读取 · 先看已取得的依据" : summary?.headline;

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

  if (!summary) return <div className="aq-market-block">{failureNote || <StatePanel tone="warn">市场环境依据暂缺，尚不能据此判断大盘和板块是否支持参与。<Link to="/market">查看市场来源</Link></StatePanel>}</div>;

  return (
    <div className="aq-market-block">
      {failureNote}
    <div className="aq-market-strip" aria-label="市场与行业方向">
      <div className="aq-market-strip-main">
        <div className="aq-market-direction-head">
          <div><b>先看市场和行业</b><span>{headline}</span></div>
          <Link className="aq-strip-link" to="/market">展开市场证据 <ArrowRight aria-hidden="true" /></Link>
        </div>
        {pendingSections.length > 0 ? <small role="status">仍在读取：{pendingSections.map((key) => MARKET_SECTION_LABELS[key]).join("、")}；已取得的依据先显示。</small> : null}
        <div className="aq-index-chips">
          {summary.indices.map((item) => (
            <span className="aq-index-chip" key={item.key}>
              <b>{item.name}</b>
              <span className="aq-num">{item.price.toFixed(2)}</span>
              <span className={cn("aq-num", changeClass(item.changePct))}>{formatSignedPct(item.changePct)}</span>
            </span>
          ))}
        </div>
        {summary.indices.length > 0 ? <small className="aq-market-source-times">当前行情：指数 {view?.indicesUpdated ? formatEvidenceTime(view.indicesUpdated) : "源时间未标注"} · 与下方候选研究日期分开读取</small> : null}

        {summary.breadth ? (
          <div className="aq-breadth-chips">
            <span className="aq-breadth-item">
              上涨 <b className="aq-tone-up">{summary.breadth.up}</b>
            </span>
            <span className="aq-breadth-item">
              下跌 <b className="aq-tone-down">{summary.breadth.down}</b>
            </span>
            <span className="aq-breadth-item">
              涨停 <b className="aq-tone-up">{(summary.breadth.ztReal || summary.breadth.zt) ?? "—"}</b>
            </span>
            <span className="aq-breadth-item">
              跌停 <b className="aq-tone-down">{(summary.breadth.dtReal || summary.breadth.dt) ?? "—"}</b>
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
        {summary.breadth ? <small className="aq-market-source-times">市场宽度源行情 {formatEvidenceTime(summary.breadth.asOf)} · {summary.breadth.source} · 已覆盖 {summary.breadth.total} 只</small> : null}

        {hasIndustryRows ? <div className="aq-industry-directions" aria-label="行业涨跌前列">
          <div><b>涨幅榜前列</b>{leadingIndustries.map((item) => <span key={item.code}>{item.name}<strong className={changeClass(item.changePct)}>{formatSignedPct(item.changePct)}</strong></span>)}</div>
          <div><b>跌幅榜前列</b>{laggingIndustries.map((item) => <span key={item.code}>{item.name}<strong className={changeClass(item.changePct)}>{formatSignedPct(item.changePct)}</strong></span>)}</div>
          <small>{view?.industryUpdated ? `行业源行情 ${formatEvidenceTime(view.industryUpdated)}` : "行业榜源行情时间未标注；暂不据此判断当前板块强弱"}</small>
        </div> : industryRequestFailed || industryPending ? null : <p className="aq-industry-missing" role="status">行业涨跌榜本次未返回；板块方向依据待补，指数涨跌不能替代行业证据。</p>}

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

    </div>
    </div>
  );
}
