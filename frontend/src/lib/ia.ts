// TickFlow 的完整研究导航与 AQSP 个人决策线并列；各入口复用原数据归属。
// 持仓按服务端访客隔离，自选/笔记保留原浏览器存储。
import {
  Activity,
  BarChart3,
  BellRing,
  BookOpen,
  Compass,
  Database,
  Gauge,
  ListFilter,
  Star,
  TableProperties,
  TrendingUp,
  Wallet,
  Zap,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  desc: string;
  icon: LucideIcon;
}

export interface NavLine {
  id: string;
  title: string;
  items: NavItem[];
}

// Restore TickFlow's working-terminal module map; route adapters reuse AQSP data
// and enforce AQSP's PIT, tenant, advisory-only, and no-order contracts.
export const PRIMARY_LINE: NavLine = {
  id: "research",
  title: "量化工作台",
  items: [
    { to: "/today", label: "看板", desc: "今日市场 · 决策与候选", icon: Compass },
    { to: "/watchlist", label: "自选", desc: "我的关注 · 实时跟踪", icon: Star },
    { to: "/screener", label: "策略", desc: "策略假设 · 当前候选命中", icon: ListFilter },
    { to: "/signals", label: "信号", desc: "条件定义 · 版本与启停", icon: Zap },
    { to: "/factors", label: "因子", desc: "候选因子 · 来源与贡献", icon: TableProperties },
    { to: "/backtest", label: "回测", desc: "正式台账 · 研究变体", icon: BarChart3 },
    { to: "/regime", label: "市场环境", desc: "宽度 · 情绪 · 板块主线", icon: Gauge },
    { to: "/abnormal", label: "异动", desc: "竞价 · 盘中 · 多日偏移", icon: Activity },
    { to: "/indices", label: "指数", desc: "宽基指数 · 隔夜外围", icon: TrendingUp },
    { to: "/data", label: "数据", desc: "来源能力 · 健康与时点", icon: Database },
  ],
};

export const MY_LINE: NavLine = {
  id: "personal",
  title: "个人操作系统",
  items: [
    { to: "/monitor", label: "监控", desc: "持仓风险 · 候选条件跟进", icon: BellRing },
    { to: "/review", label: "复盘", desc: "收盘回顾 · 本人判断沉淀", icon: BookOpen },
    { to: "/my/holdings", label: "持仓", desc: "真实持仓 · 操作与成交事实", icon: Wallet },
    { to: "/stream", label: "消息", desc: "事件线索 · 来源核验", icon: Zap },
  ],
};

export const NAV_LINES: readonly NavLine[] = [PRIMARY_LINE, MY_LINE];

export const DEFAULT_ROUTE = "/today";

/**
 * 旧路由 → 新路由。保留书签、外部链接与服务器上旧文档里的链接可用，
 * 不在页面上暴露任何"迁移"概念。
 *
 * 兼容旧入口，重定向到当前保留的 AQSP 工作区；删除旧页面前不破坏书签和外部链接。
 */
export const LEGACY_ROUTES: Readonly<Record<string, string>> = {
  "/system/review": "/review",
  "/system/recommend": "/screener",
  "/system/lab": "/backtest",
  "/system/archive": "/review",
  "/radar": "/stream",
  "/dashboard": "/today",
  "/lab": "/backtest",
  "/archive": "/review",
  "/performance": "/backtest",
  "/reviews": "/review",
  "/my/lab": "/my/holdings",
  "/my/watchlist": "/watchlist",
  "/concepts": "/stream",
};
