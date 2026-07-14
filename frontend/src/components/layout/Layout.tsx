import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  Activity,
  CalendarDays,
  ChevronsLeft,
  ChevronsRight,
  FileSearch,
  Github,
  LineChart,
  Moon,
  Radar,
  Sun,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useDarkMode } from "@/hooks/useDarkMode";
import { useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";

const APP_VERSION = "v0.1.3";
const REPO_URL = "https://github.com/simonlin1212/Vibe-Research";

const NAV = [
  { to: "/daily-review", icon: Activity, label: "今日研究", description: "结论与证据" },
  { to: "/intel", icon: Radar, label: "消息核验", description: "来源与传导" },
  { to: "/paper-research", icon: FileSearch, label: "纸面研究", description: "候选与复核" },
];

function formatDate(date: string): { day: string; weekday: string } {
  const value = new Date(`${date}T00:00:00+08:00`);
  if (Number.isNaN(value.getTime())) return { day: date, weekday: "" };
  return {
    day: new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(value),
    weekday: new Intl.DateTimeFormat("zh-CN", { weekday: "short" }).format(value),
  };
}

export function Layout() {
  const { pathname } = useLocation();
  const { dark, toggle } = useDarkMode();
  const { data, loading } = useAqspSnapshot();
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("vr-sidebar") === "collapsed");
  const [selectedDate, setSelectedDate] = useState(() => localStorage.getItem("vr-selected-date") || "");

  useEffect(() => {
    localStorage.setItem("vr-sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);

  useEffect(() => {
    if (data?.selected_date && !data.available_dates.includes(selectedDate)) {
      setSelectedDate(data.selected_date);
      localStorage.setItem("vr-selected-date", data.selected_date);
    }
  }, [data, selectedDate]);

  const dates = data?.available_dates ?? [];
  const activeDate = selectedDate || data?.selected_date || "";

  return (
    <div className="vr-shell">
      <aside className={cn("vr-sidebar glass", collapsed && "vr-sidebar-collapsed")}>
        <div className="vr-brand">
          <Link to="/daily-review" className="flex min-w-0 items-center gap-2.5">
            <span className="vr-brand-mark"><LineChart className="h-5 w-5" /></span>
            {!collapsed && <span className="truncate text-base font-bold tracking-tight">Vibe-Research</span>}
          </Link>
          {!collapsed && <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">把当天研究收敛成可复核的工作台。</p>}
        </div>

        <div className={cn("vr-sidebar-scroll", collapsed && "px-1.5")}>
          {!collapsed && (
            <div className="vr-sidebar-section">
              <div className="vr-sidebar-label"><span>工作区</span><span className="text-muted-foreground/50">3</span></div>
            </div>
          )}
          <nav className="space-y-1" aria-label="研究工作区">
            {NAV.map(({ to, icon: Icon, label, description }) => {
              const active = pathname === to;
              return (
                <Link
                  key={to}
                  to={to}
                  title={collapsed ? `${label} · ${description}` : undefined}
                  className={cn("vr-nav-item", active && "vr-nav-item-active", collapsed && "justify-center px-2")}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  {!collapsed && <span className="min-w-0"><span className="block truncate font-medium">{label}</span><span className="block truncate text-[10px] text-muted-foreground">{description}</span></span>}
                </Link>
              );
            })}
          </nav>

          {!collapsed && (
            <section className="vr-sidebar-section mt-7" aria-labelledby="date-index-title">
              <div className="vr-sidebar-label" id="date-index-title"><span>研究日期</span><CalendarDays className="h-3.5 w-3.5" /></div>
              <div className="mt-2 space-y-1.5">
                {loading && <div className="rounded-lg border border-border/50 px-3 py-2 text-xs text-muted-foreground">读取日期索引…</div>}
                {!loading && dates.length === 0 && <div className="rounded-lg border border-dashed border-border/60 px-3 py-2 text-xs text-muted-foreground">暂无日期索引</div>}
                {dates.map((date) => {
                  const label = formatDate(date);
                  const active = date === activeDate;
                  return (
                    <button
                      key={date}
                      type="button"
                      className={cn("vr-date-item", active && "vr-date-item-active")}
                      onClick={() => {
                        setSelectedDate(date);
                        localStorage.setItem("vr-selected-date", date);
                        window.dispatchEvent(new CustomEvent("vr-date-change", { detail: date }));
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
              <a href={REPO_URL} target="_blank" rel="noreferrer" className="vr-icon-button" title="GitHub"><Github className="h-4 w-4" /></a>
              <button onClick={() => setCollapsed((value) => !value)} className="vr-icon-button" title={collapsed ? "展开侧栏" : "收起侧栏"}>
                {collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
              </button>
            </div>
          </div>
          {!collapsed && <p className="mt-2 text-[10px] text-muted-foreground/55">{APP_VERSION} · 只读研究工作台</p>}
        </div>
      </aside>

      <main className="vr-main">
        <div className="vr-content"><Outlet /></div>
      </main>
    </div>
  );
}
