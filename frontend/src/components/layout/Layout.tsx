import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  CalendarDays,
  FlaskConical,
  Globe2,
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
import { formatResearchDate } from "@/lib/research-view";

const NAV_ICONS = [Sparkles, ScrollText, LineChart, UsersRound, Globe2] as const;

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

  const dates = data?.available_dates ?? [];
  const activeDate = selectedDate || data?.selected_date || "";

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
                : countKey === "market"
                ? data?.market_context?.cross_market.length ?? 0
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

          {!collapsed && (
            <section className="vr-sidebar-section mt-7" aria-labelledby="date-index-title">
              <div className="vr-sidebar-label" id="date-index-title"><span>研究日期</span><CalendarDays className="h-3.5 w-3.5" /></div>
              <div className="mt-2 space-y-1.5">
                {loading && <div className="rounded-lg border border-border/50 px-3 py-2 text-xs text-muted-foreground">读取日期索引…</div>}
                {!loading && dates.length === 0 && <div className="rounded-lg border border-dashed border-border/60 px-3 py-2 text-xs text-muted-foreground">暂无日期索引</div>}
                {dates.map((date) => {
                  const label = formatResearchDate(date);
                  const active = date === activeDate;
                  return (
                    <button
                      key={date}
                      type="button"
                      className={cn("vr-date-item", active && "vr-date-item-active")}
                      onClick={() => {
                        selectDate(date);
                      }}
                      aria-pressed={active}
                    >
                      <span className="font-mono text-xs">{label.day}</span>
                      <span className="text-[10px] text-muted-foreground">{label.weekday}</span>
                      {active && <span className="ml-auto text-[10px] text-primary">当前</span>}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground/60">日期来自只读快照索引，正文以当前数据源返回为准。</p>
            </section>
          )}
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
