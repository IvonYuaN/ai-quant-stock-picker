// 候选池：当日候选名单 + 逐票完整证据链 + 并排对比。
//
// 2026-11-09 从「今日」页拆出独立模块。此前这些内容被塞进证据折叠区，
// 今日页越滚越长、信息东一块西一块；拆成模块页后左栏可直接点进来，
// 页内信息全部平铺（不靠折叠藏）。
import { useState } from "react";
import { Target } from "lucide-react";
import { HeroState, LoadingState } from "@/components/ui/primitives";
import { CandidateSection } from "@/components/aqsp/sections/CandidateSection";
import { CandidateDrawer } from "@/components/aqsp/CandidateDrawer";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";

export function CandidatesPage() {
  const { data, loading, error, switching } = useWorkspaceSnapshot();
  const [picked, setPicked] = useState<string | null>(null);
  // 日期未对齐时不给任何"当前日期"的展示模型，从根上杜绝张冠李戴。
  const view = data && !switching ? buildDailyView(data) : null;
  const section = view?.sections.find((item) => item.id === "candidates");

  if (error && !data) {
    return (
      <div className="aq-page">
        <HeroState tone="warn" title="读不到候选快照" detail={error} />
      </div>
    );
  }
  if (!view || !section) {
    return (
      <div className="aq-page">
        <LoadingState label={switching ? "正在切换研究日期…" : "正在读取当日候选…"} />
      </div>
    );
  }

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Target aria-hidden="true" /> AQSP · 研究模块
          </p>
          <div className="aq-title-row">
            <h1>候选池</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">
            当日候选名单、逐票证据链与并排对比；证据齐了才进纸面复核。
          </p>
        </div>
      </header>

      <CandidateSection view={view} section={section} onPick={setPicked} />
      <CandidateDrawer view={view} symbol={picked} onClose={() => setPicked(null)} />
      {loading ? <p className="aq-hint" role="status">读取中…</p> : null}
    </div>
  );
}
