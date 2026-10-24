import { Suspense, useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Download, LineChart, Moon, PanelLeftClose, PanelLeftOpen, Search, Sun, Upload, BellRing, Ellipsis, X } from "lucide-react";
import { Toaster, toast } from "sonner";
import { cn } from "@/lib/utils";
import { NAV_LINES, PRIMARY_LINE } from "@/lib/ia";
import { useDarkMode } from "@/hooks/useDarkMode";
import { useAlertOpenCount } from "@/hooks/useAlertOpenCount";
import { AqspWorkspaceProvider, useAqspSnapshot } from "@/components/aqsp/useAqspSnapshot";
import { downloadVault, importVault } from "@/lib/vault";
import { LoadingState } from "@/components/ui/primitives";
import { CommandPalette } from "@/components/ui/CommandPalette";
import { OperationRecorder } from "@/components/aqsp/OperationRecorder";
import { requestOperation } from "@/lib/operation";
import { hasTemporaryUserIdentity } from "@/lib/api";
import { readLocalPreference, writeLocalPreference } from "@/lib/browser-session";

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
  const { pathname, search } = useLocation();
  const researchDate = new URLSearchParams(search).get("date");
  const navHref = (path: string) => researchDate ? `${path}?date=${encodeURIComponent(researchDate)}` : path;
  const { dark, toggle } = useDarkMode();
  const { data, loading } = useAqspSnapshot();
  const { openCount: openAlerts, refetch: refetchAlerts } = useAlertOpenCount();
  const [collapsed, setCollapsed] = useState(
    () => readLocalPreference("aqsp-sidebar") === "collapsed",
  );
  const [temporaryIdentity] = useState(hasTemporaryUserIdentity);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileToolsOpen, setMobileToolsOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => setMobileToolsOpen(false), [pathname]);

  useEffect(() => {
    writeLocalPreference("aqsp-sidebar", collapsed ? "collapsed" : "expanded");
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
      <header className="aq-mobile-bar">
        <Link to={navHref("/today")} className="aq-mobile-brand"><LineChart aria-hidden="true" /><b>AQSP</b></Link>
        <span>{NAV_LINES.flatMap((line) => line.items).find((item) => item.to === pathname)?.label ?? "个股研究"}</span>
        <div className="aq-mobile-actions"><button type="button" className="aq-btn aq-btn-primary" onClick={() => requestOperation()}>我操作了</button><button type="button" className="aq-icon-btn" aria-label="更多工具" aria-controls="aq-mobile-tools" aria-expanded={mobileToolsOpen} onClick={() => setMobileToolsOpen((value) => !value)}><Ellipsis aria-hidden="true" /></button></div>
      </header>
      <aside className={cn("aq-sidebar", collapsed && "aq-sidebar-collapsed")}>
        <div className="aq-brand">
          <div className="aq-brand-row">
            <Link to={navHref("/today")} className="aq-brand-link">
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
          {!collapsed ? <p className="aq-brand-sub">T+1 持仓决策工作台</p> : null}
        </div>

        {openAlerts > 0 ? (
          <Link
            to="/today"
            className="aq-alert-badge"
            title={`${openAlerts} 只持仓风险待复核，点击查看`}
            onClick={() => void refetchAlerts()}
          >
            <BellRing aria-hidden="true" />
            {!collapsed ? <span>持仓风险</span> : null}
            <span className="aq-alert-badge-count">{openAlerts}</span>
          </Link>
        ) : null}

        <button
          type="button"
          className="aq-btn aq-btn-primary aq-record-trigger"
          onClick={() => requestOperation()}
          title="回录已完成的买入或卖出"
        >
          <LineChart aria-hidden="true" />{!collapsed ? "我操作了" : null}
        </button>

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
                    to={navHref(item.to)}
                    aria-current={active ? "page" : undefined}
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

          {!collapsed ? <p className="aq-sidebar-note">AQSP · 人工决策，不代下单</p> : null}
        </div>
      </aside>

      <input ref={fileRef} type="file" accept="application/json" hidden onChange={async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        try {
          const result = importVault(await file.text());
          if (result.ok) toast(`已导入 ${result.keys} 项，刷新后生效`);
          else toast.error(`导入失败：${result.reason}`);
        } catch { toast.error("读取备份失败，请检查文件后重试"); }
        event.target.value = "";
      }} />

      <main className="aq-main">
        <div className="aq-content">
          {temporaryIdentity ? <p className="aq-refresh-warning" role="status">浏览器限制本机存储，当前页面使用临时独立身份。刷新或关闭页面后无法恢复此身份，也无法自动找回本会话下录入的持仓。请先允许本站存储再录入真实成交。</p> : null}
          {/* 路由是懒加载的（见 router.tsx）：切页时先显示加载态，避免闪一下空白页框 */}
          <Suspense fallback={<LoadingState label="正在加载页面…" />}>
            <Outlet />
          </Suspense>
        </div>
      </main>

      {mobileToolsOpen ? (
        <section className="aq-mobile-tools" id="aq-mobile-tools" aria-label="个人工具">
          <div className="aq-mobile-tools-head"><strong>个人工具</strong><button type="button" className="aq-icon-btn" aria-label="关闭个人工具" onClick={() => setMobileToolsOpen(false)}><X aria-hidden="true" /></button></div>
          <button type="button" className="aq-mobile-tool" onClick={() => { setMobileToolsOpen(false); setPaletteOpen(true); }}><Search aria-hidden="true" /><span>搜索代码</span></button>
          <button type="button" className="aq-mobile-tool" onClick={toggle}>{dark ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />}<span>{dark ? "切换亮色" : "切换暗色"}</span></button>
          <button type="button" className="aq-mobile-tool" onClick={() => { downloadVault(); toast("已导出备份文件"); }}><Download aria-hidden="true" /><span>导出个人备份</span></button>
          <button type="button" className="aq-mobile-tool" onClick={() => fileRef.current?.click()}><Upload aria-hidden="true" /><span>导入个人备份</span></button>
        </section>
      ) : null}
      <nav className="aq-mobile-nav" aria-label="主要导航">
        {PRIMARY_LINE.items.map((item) => { const Icon = item.icon; return <Link key={item.to} to={navHref(item.to)} aria-current={pathname === item.to ? "page" : undefined}><Icon aria-hidden="true" /><span>{item.label}</span></Link>; })}
      </nav>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <OperationRecorder />

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
