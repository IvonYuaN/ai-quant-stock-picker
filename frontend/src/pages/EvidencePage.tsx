// 研究依据：这个结论凭什么 —— 消息与讨论复核、门禁与策略、冻结参数与口径。
//
// 2026-11-09 从「今日」页的标签面板拆出独立模块。用户要的是"每次点击都能获得
// 足够的信息和密度"，所以证据层单独成页，一屏看完，不藏在标签后面。
import { Scale } from "lucide-react";
import { HeroState, LoadingState, SectionHeader } from "@/components/ui/primitives";
import { DecisionPanel } from "@/components/aqsp/DecisionPanel";
import { MessageSection } from "@/components/aqsp/sections/MessageSection";
import { DiscussionSection } from "@/components/aqsp/sections/DiscussionSection";
import { useWorkspaceSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { buildDailyView } from "@/lib/daily-view";
import { formatAqspTime } from "@/components/aqsp/useAqspSnapshot";

export function EvidencePage() {
  const { data, error, switching } = useWorkspaceSnapshot();
  const view = data && !switching ? buildDailyView(data) : null;
  const messages = view?.sections.find((item) => item.id === "messages");
  const discussion = view?.sections.find((item) => item.id === "discussion");

  if (error && !data) {
    return (
      <div className="aq-page">
        <HeroState tone="warn" title="读不到研究快照" detail={error} />
      </div>
    );
  }
  if (!view || !data) {
    return (
      <div className="aq-page">
        <LoadingState label={switching ? "正在切换研究日期…" : "正在读取研究依据…"} />
      </div>
    );
  }

  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <Scale aria-hidden="true" /> AQSP · 复盘模块
          </p>
          <div className="aq-title-row">
            <h1>研究依据</h1>
            <strong>{view.date}</strong>
          </div>
          <p className="aq-page-sub">
            消息证据、多空复核、门禁判定与冻结参数 —— 判断前先把依据摊开看。
          </p>
        </div>
      </header>

      <section className="aq-section" aria-label="口径与冻结参数">
        <SectionHeader
          number="01 · 口径"
          title="当日口径与冻结参数"
          description="评分、策略依据与参数身份沿用当日首次冻结；现价、新鲜度与消息按最新证据更新。"
          count={view.frozenAt ? formatAqspTime(view.frozenAt) : "未冻结"}
        />
        <p className="aq-tag-row">
          <span className="aq-chip aq-tag-primary">{view.parameterSnapshot.label}</span>
          <span className="aq-chip">{view.frozenAt ? `候选已冻结 ${formatAqspTime(view.frozenAt)}` : "冻结信息尚未生成"}</span>
        </p>
      </section>

      {messages ? <MessageSection view={view} section={messages} /> : null}
      {discussion ? <DiscussionSection view={view} section={discussion} /> : null}

      <section className="aq-section" aria-label="门禁与策略">
        <SectionHeader
          number="04 · 门禁"
          title="门禁判定与策略口径"
          description="门禁不通过时下面的候选不可作为买入信号；这一层独立成页，避免被前文淹没。"
        />
        <DecisionPanel snapshot={data} />
      </section>
    </div>
  );
}
