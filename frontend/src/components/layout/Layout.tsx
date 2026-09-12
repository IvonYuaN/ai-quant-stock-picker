import { Suspense, useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Download, LineChart, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun, Upload } from "lucide-react";
import { Toaster, toast } from "sonner";
import { cn } from "@/lib/utils";
import { NAV_LINES } from "@/lib/ia";
import { useDarkMode } from "@/hooks/useDarkMode";
import { AqspWorkspaceProvider, useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { downloadVault, importVault } from "@/lib/vault";
import { LoadingState } from "@/components/ui/primitives";
import { CommandPalette } from "@/components/ui/CommandPalette";

/** 输入框里打字时不要抢快捷键 */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.tagName === "INPUT" ||
    target.tagName === "TEXTAREA" ||
    target.tagName === "SELECT" ||
    target.isContentEditable
  );
}

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
  const { data, loading } = useAqspSnapshot();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("aqsp-sidebar") === "collapsed",
  );
  const [paletteOpen, setPaletteOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    localStorage.setItem("aqsp-sidebar", collapsed ? "collapsed" : "expanded");
  }, [collapsed]);

  // 全局快捷键：Cmd/Ctrl+K 打开命令面板；不在输入时按 / 也可打开
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((value) => !value);
        return;
      }
      if (event.key === "/" && !isTyping(event.target)) {
        event.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 注：日期对不齐的兜底已移到 useAqspSnapshot 内部（快照 404 时清空 URL 上的 date）。
  // 这里不要再回填 —— 日期现在是 URL 状态，回填会把「实时快照」改写成显式日期，
  // 而且那次导航会丢掉 hash（今日页的页签活在 hash 上），把用户弹回默认页签。

  return (
    <div className="aq-shell">
      <aside className={cn("aq-sidebar", collapsed && "aq-sidebar-collapsed")}>
        <div className="aq-brand">
          <div className="aq-brand-row">
            <Link to="/today" className="aq-brand-link">
              <span className="aq-brand-mark">
                <LineChart aria-hidden="true" />
              </span>
              {!collapsed ? <span className="aq-brand-name">AQSP</span> : null}
            </Link>
            <button
              type="button"
              onClick={() => setCollapsed((value) => !value)}
              className="aq-icon-btn"
              title={collapsed ? "展开侧栏" : "收起侧栏"}
              aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
              aria-expanded={!collapsed}
            >
              {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
            </button>
          </div>
          {!collapsed ? <p className="aq-brand-sub">AI 自动产出 + 个人选股管理</p> : null}
        </div>

        <button
          type="button"
          className="aq-search-trigger"
          onClick={() => setPaletteOpen(true)}
          title="搜索代码 / 页面（⌘K 或 /）"
        >
          <Search aria-hidden="true" />
          {!collapsed ? <span>搜索代码或页面</span> : null}
          {!collapsed ? <kbd>⌘K</kbd> : null}
        </button>

        <nav className={cn("aq-nav", collapsed && "aq-nav-collapsed")}>
          {NAV_LINES.map((line) => (
            <div className="aq-nav-group" key={line.id}>
              {!collapsed ? (
                <div className="aq-nav-label">
                  <span>{line.title}</span>
                  <span className="aq-nav-label-sub">{line.subtitle}</span>
                </div>
              ) : null}
              {line.items.map((item) => {
                const Icon = item.icon;
                const active = pathname === item.to;
                return (
                  <Link
                    key={item.to}
                    to={item.to}
                    title={collapsed ? `${item.label} · ${item.desc}` : undefined}
                    className={cn("aq-nav-item", active && "aq-nav-item-active", collapsed && "aq-nav-item-collapsed")}
                  >
                    <Icon aria-hidden="true" />
                    {!collapsed ? (
                      <span className="aq-nav-text">
                        <span className="aq-nav-title">{item.label}</span>
                        <span className="aq-nav-desc">{item.desc}</span>
                      </span>
                    ) : null}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="aq-sidebar-foot">
          {!collapsed ? (
            <div className="aq-vault-row">
              <button
                type="button"
                className="aq-icon-btn"
                title="导出本机私有数据（自选 / 笔记）"
                onClick={() => {
                  downloadVault();
                  toast("已导出备份文件");
                }}
              >
                <Download aria-hidden="true" />
                <span>导出备份</span>
              </button>
              <button
                type="button"
                className="aq-icon-btn"
                title="导入备份文件"
                onClick={() => fileRef.current?.click()}
              >
                <Upload aria-hidden="true" />
                <span>导入</span>
              </button>
              <input
                ref={fileRef}
                type="file"
                accept="application/json"
                hidden
                onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (!file) return;
                  const result = importVault(await file.text());
                  if (result.ok) toast(`已导入 ${result.keys} 项，刷新后生效`);
                  else toast.error(`导入失败：${result.reason}`);
                  event.target.value = "";
                }}
              />
            </div>
          ) : null}

          <div className="aq-sidebar-foot-row">
            <button
              type="button"
              onClick={toggle}
              className="aq-icon-btn"
              title={dark ? "切换亮色" : "切换暗色"}
            >
              {dark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}
              {!collapsed ? <span>{dark ? "亮色" : "暗色"}</span> : null}
            </button>
            <span className="aq-data-status" title="AQSP 数据状态">
              <span
                className={cn(
                  "aq-dot",
                  loading ? "aq-dot-loading" : data ? "aq-dot-ready" : "aq-dot-empty",
                )}
              />
              {!collapsed ? <span>{loading ? "读取中" : data ? "数据已接入" : "暂无数据"}</span> : null}
            </span>
          </div>

          {!collapsed ? <p className="aq-sidebar-note">AQSP · 只读研究</p> : null}
        </div>
      </aside>

      <main className="aq-main">
        <div className="aq-content">
          {/* 路由是懒加载的（见 router.tsx）：切页时先显示加载态，避免闪一下空白页框 */}
          <Suspense fallback={<LoadingState label="正在加载页面…" />}>
            <Outlet />
          </Suspense>
        </div>
      </main>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

      {/* 提示条跟随应用主题，避免亮色模式下弹出深色浮层。 */}
      <Toaster
        position="bottom-right"
        theme={dark ? "dark" : "light"}
        richColors
        closeButton
        duration={3500}
      />
    </div>
  );
}
