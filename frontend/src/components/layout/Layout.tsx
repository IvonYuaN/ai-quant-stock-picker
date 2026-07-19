import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  FlaskConical,
  LineChart,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Sparkles,
  Sun,
  UsersRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { RESEARCH_NAV_ITEMS, TEST_VARIANTS_SECTION_ID } from "@/lib/research-layout";
import { useDarkMode } from "@/hooks/useDarkMode";
import { AqspWorkspaceProvider, useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";

const NAV_ICONS = [Sparkles, ScrollText, LineChart, UsersRound] as const;

export function Layout() {
  return <AqspWorkspaceProvider><WorkspaceLayout /></AqspWorkspaceProvider>;
}

function WorkspaceLayout() {
  const { pathname, hash } = useLocation();
  const { dark, toggle } = useDarkMode();
  const { data, loading, selectedDate, selectDate } = useAqspSnapshot();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("aqsp-sidebar") === "collapsed");

  useEffect(() => {
    localStorage.setItem("aqsp-sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);

  useEffect(() => {
    if (data?.selected_date && !data.available_dates.includes(selectedDate)) {
      selectDate(data.selected_date);
    }
  }, [data, selectedDate, selectDate]);

  return (
    <div className="vr-shell">
      <aside className={cn("vr-sidebar glass", collapsed && "vr-sidebar-collapsed")}>
        <div className="vr-brand">
          <div className="flex items-start justify-between gap-2">
            <Link to="/daily-review#overview" className="flex min-w-0 items-center gap-2.5">
              <span className="vr-brand-mark"><LineChart className="h-5 w-5" /></span>
              {!collapsed && <span className="truncate text-base font-bold">AQSP</span>}
            </Link>
            <button
              type="button"
              onClick={() => setCollapsed((value) => !value)}
              className="vr-icon-button shrink-0"
              title={collapsed ? "展开侧栏" : "收起侧栏"}
              aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
              aria-expanded={!collapsed}
            >
              {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </button>
          </div>
          {!collapsed && <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">把当天研究收敛成可复核的工作台。</p>}
        </div>

        <div className={cn("vr-sidebar-scroll", collapsed && "px-1.5")}>
          {!collapsed && (
            <div className="vr-sidebar-section">
              <div className="vr-sidebar-label"><span>研究内容</span><span className="text-muted-foreground/50">{RESEARCH_NAV_ITEMS.length} 模块</span></div>
            </div>
          )}
          <nav className="space-y-1" aria-label="研究内容">
            {RESEARCH_NAV_ITEMS.map(({ id: targetHash, label, description, countKey }, index) => {
              const Icon = NAV_ICONS[index];
              const count = countKey === "conclusion"
                ? 1
                : countKey === "messages"
                  ? data?.messages.length ?? 0
                  : countKey === "candidates"
                    ? data?.candidates.length ?? 0
                    : data?.debates.length ?? 0;
              const to = `/daily-review#${targetHash}`;
              const active = pathname === "/daily-review" && (hash === `#${targetHash}` || (!hash && targetHash === "overview"));
              return (
                <Link
                  key={to}
                  to={to}
                  onClick={() => undefined}
                  title={collapsed ? `${label} · ${description}` : undefined}
                  className={cn("vr-nav-item", active && "vr-nav-item-active", collapsed && "justify-center px-2")}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed && <span className="min-w-0"><span className="flex items-center gap-2 truncate font-medium"><span className="truncate">{label}</span><span className="ml-auto font-mono text-[10px] text-muted-foreground">{count}</span></span><span className="block truncate text-[10px] text-muted-foreground">{description}</span></span>}
                </Link>
              );
            })}
          </nav>

          {!collapsed && <div className="vr-sidebar-section mt-7"><div className="vr-sidebar-label"><span>独立实验区</span><span className="text-muted-foreground/50">不入推荐</span></div></div>}
          <nav className="mt-1 space-y-1" aria-label="独立实验区">
            <Link
              to={`/daily-review#${TEST_VARIANTS_SECTION_ID}`}
              onClick={() => undefined}
              title={collapsed ? "测试与变体 · 不参与正式推荐" : undefined}
              className={cn("vr-nav-item", hash === `#${TEST_VARIANTS_SECTION_ID}` && "vr-nav-item-active", collapsed && "justify-center px-2")}
            >
              <FlaskConical className="h-4 w-4 shrink-0" />
              {!collapsed && <span className="min-w-0"><span className="block truncate font-medium">测试与变体</span><span className="block truncate text-[10px] text-muted-foreground">不参与正式推荐</span></span>}
            </Link>
          </nav>

        </div>

        <div className="vr-sidebar-footer">
          <div className="flex items-center justify-between gap-2">
            <button onClick={toggle} className="vr-icon-button" title={dark ? "切换亮色" : "切换暗色"}>
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              {!collapsed && <span>{dark ? "亮色" : "暗色"}</span>}
            </button>
            <div className="flex items-center gap-1">
              <span className="vr-data-status" title="AQSP 数据状态">
                <span className={cn("vr-data-status-dot", loading ? "vr-data-status-loading" : data ? "vr-data-status-ready" : "vr-data-status-empty")} />
                {!collapsed && <span>{loading ? "读取中" : data ? "数据已接入" : "暂无数据"}</span>}
              </span>
            </div>
          </div>
          {!collapsed && <p className="mt-2 text-[10px] text-muted-foreground/55">AQSP · 只读研究</p>}
        </div>
      </aside>

      <main className="vr-main">
        <div className="vr-content"><Outlet /></div>
      </main>
    </div>
  );
}
