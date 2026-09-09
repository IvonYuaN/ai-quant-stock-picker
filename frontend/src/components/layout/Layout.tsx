import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import {
  Download,
  LineChart,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
  Upload,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { NAV_LINES } from "@/lib/ia";
import { useDarkMode } from "@/hooks/useDarkMode";
import { AqspWorkspaceProvider, useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { downloadVault, importVault } from "@/lib/vault";

export function Layout() {
  return (
    <AqspWorkspaceProvider>
      <WorkspaceLayout />
    </AqspWorkspaceProvider>
  );
}

function WorkspaceLayout() {
  const { pathname } = useLocation();
  const { dark, toggle } = useDarkMode();
  const { data, loading, selectedDate, selectDate } = useAqspSnapshot();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("aqsp-sidebar") === "collapsed",
  );
  const [vaultHint, setVaultHint] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

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
            <Link to="/system/review" className="flex min-w-0 items-center gap-2.5">
              <span className="vr-brand-mark">
                <LineChart className="h-5 w-5" />
              </span>
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
          {!collapsed && (
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              AI 自动产出 + 个人选股管理
            </p>
          )}
        </div>

        <div className={cn("vr-sidebar-scroll", collapsed && "px-1.5")}>
          {NAV_LINES.map((line) => (
            <div key={line.id} style={{ marginBottom: "1.25rem" }}>
              {!collapsed && (
                <div className="vr-sidebar-section">
                  <div className="vr-sidebar-label">
                    <span>{line.title}</span>
                    <span className="text-muted-foreground/50">{line.subtitle}</span>
                  </div>
                </div>
              )}
              <nav className="mt-1 space-y-1" aria-label={line.title}>
                {line.items.map((item) => {
                  const Icon = item.icon;
                  const active = pathname === item.to;
                  return (
                    <Link
                      key={item.to}
                      to={item.to}
                      title={collapsed ? `${item.label} · ${item.desc}` : undefined}
                      className={cn(
                        "vr-nav-item",
                        active && "vr-nav-item-active",
                        collapsed && "justify-center px-2",
                      )}
                    >
                      <Icon className="h-4 w-4 shrink-0" />
                      {!collapsed && (
                        <span className="min-w-0">
                          <span className="block truncate font-medium">{item.label}</span>
                          <span className="block truncate text-[10px] text-muted-foreground">
                            {item.desc}
                          </span>
                        </span>
                      )}
                    </Link>
                  );
                })}
              </nav>
            </div>
          ))}
        </div>

        <div className="vr-sidebar-footer">
          {!collapsed && (
            <div className="vr-row" style={{ marginBottom: "0.5rem" }}>
              <button
                type="button"
                className="vr-icon-button"
                title="导出本机私有数据（自选 / 笔记）"
                onClick={() => {
                  downloadVault();
                  setVaultHint("已导出备份文件");
                }}
              >
                <Download className="h-4 w-4" />
                <span>导出备份</span>
              </button>
              <button
                type="button"
                className="vr-icon-button"
                title="导入备份文件"
                onClick={() => fileRef.current?.click()}
              >
                <Upload className="h-4 w-4" />
                <span>导入</span>
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="application/json"
                style={{ display: "none" }}
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  const result = importVault(await file.text());
                  setVaultHint(
                    result.ok
                      ? `已导入 ${result.keys} 项，刷新后生效`
                      : `导入失败：${result.reason}`,
                  );
                  event.target.value = "";
                }}
              />
            </div>
          )}
          {!collapsed && vaultHint ? (
            <p className="mb-2 text-[10px] text-muted-foreground">{vaultHint}</p>
          ) : null}
          <div className="flex items-center justify-between gap-2">
            <button
              onClick={toggle}
              className="vr-icon-button"
              title={dark ? "切换亮色" : "切换暗色"}
            >
              {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              {!collapsed && <span>{dark ? "亮色" : "暗色"}</span>}
            </button>
            <div className="flex items-center gap-1">
              <span className="vr-data-status" title="AQSP 数据状态">
                <span
                  className={cn(
                    "vr-data-status-dot",
                    loading
                      ? "vr-data-status-loading"
                      : data
                        ? "vr-data-status-ready"
                        : "vr-data-status-empty",
                  )}
                />
                {!collapsed && (
                  <span>{loading ? "读取中" : data ? "数据已接入" : "暂无数据"}</span>
                )}
              </span>
            </div>
          </div>
          {!collapsed && <p className="mt-2 text-[10px] text-muted-foreground/55">AQSP · 只读研究</p>}
        </div>
      </aside>

      <main className="vr-main">
        <div className="vr-content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
