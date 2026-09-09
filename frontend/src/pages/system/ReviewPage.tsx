// 每日复盘：系统线主页面。
// 一个交易日的研究分三层：结论 → 证据 → 分歧。路由是 /system/review，页内三个小页签只做分段。
import { Link, useLocation } from "react-router-dom";
import { AqspResearchWorkspace } from "@/components/aqsp/AqspPanels";
import { cn } from "@/lib/utils";

const TABS = [
  { hash: "overview", label: "结论" },
  { hash: "messages", label: "证据" },
  { hash: "discussion", label: "分歧" },
] as const;

type TabHash = (typeof TABS)[number]["hash"];

function activeTab(hash: string): TabHash {
  const raw = hash.replace("#", "");
  return TABS.some((tab) => tab.hash === raw) ? (raw as TabHash) : "overview";
}

export function ReviewPage() {
  const { hash } = useLocation();
  const active = activeTab(hash);
  return (
    <div className="aqsp-page">
      <nav className="vr-tabs" aria-label="复盘分段">
        {TABS.map((tab) => (
          <Link
            key={tab.hash}
            to={`/system/review#${tab.hash}`}
            className={cn("vr-tab", active === tab.hash && "vr-tab-active")}
          >
            {tab.label}
          </Link>
        ))}
      </nav>
      <AqspResearchWorkspace view={active} />
    </div>
  );
}
