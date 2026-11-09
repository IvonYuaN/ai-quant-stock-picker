// 收盘复盘：当日总结与明日待办。
//
// 2026-11-09 从「今日」页的标签面板拆出独立模块。
import { ClipboardCheck } from "lucide-react";
import { ClosingReviewPanel } from "@/components/aqsp/ClosingReviewPanel";

export function ReviewPage() {
  return (
    <div className="aq-page aq-module-page">
      <header className="aq-page-head">
        <div>
          <p className="aq-eyebrow">
            <ClipboardCheck aria-hidden="true" /> AQSP · 复盘模块
          </p>
          <div className="aq-title-row">
            <h1>收盘复盘</h1>
          </div>
          <p className="aq-page-sub">当日结论回看与明日待办。</p>
        </div>
      </header>

      <ClosingReviewPanel />
    </div>
  );
}
