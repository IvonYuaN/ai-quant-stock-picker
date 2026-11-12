// 左侧导航 = 模块视图切换器（做法参考 investment-news / TradingAgents-astock）。
//
// 2026-11-09 订正：此前这里按"小白视角"把 12 个入口收敛成 4 条主线，
// 代价是所有量化内容被塞进「今日」页的证据折叠区 —— 结果今日页越滚越长、
// 信息东一块西一块、左栏又没有深度。参考项目（investment-news）恰恰相反：
// 左栏模块多、每个模块点进去是一屏密度足够的数据表。
//
// 现在改为：每个研究对象一个独立路由页面，左栏三组共 8 个模块；
// 页面内不再靠折叠藏信息（信息平铺 + 密排网格 + 分组表）。
import {
  BarChart3,
  BellRing,
  ClipboardCheck,
  Compass,
  Database,
  Globe2,
  ListFilter,
  Radio,
  Scale,
  Star,
  TableProperties,
  Target,
  TrendingUp,
  Wallet,
  Zap,
  type LucideIcon,
} from "lucide-react";

export type DataScope = "public" | "private";

export interface NavItem {
  to: string;
  label: string;
  desc: string;
  icon: LucideIcon;
  /** 左栏右侧的实时计数（由 Layout 按当前快照注入）。 */
  metric?: "candidates" | "messages" | "debates" | "alerts" | "holdings" | "factors" | "variants" | "watchlist";
}

export interface NavLine {
  id: string;
  title: string;
  /** 这条线的数据性质，直接显示在侧栏，避免用户对"我的数据会不会被别人看到"产生疑虑 */
  subtitle: string;
  scope: DataScope;
  items: NavItem[];
}

/** 研究线：当日快照与研究台账，全部来自系统抓取，公开可读。 */
export const RESEARCH_LINE: NavLine = {
  id: "research",
  title: "研究",
  subtitle: "当日快照 · 公开数据",
  scope: "public",
  items: [
    { to: "/today", label: "看板", desc: "今日结论 · 门禁 · 候选摘要", icon: Compass },
    { to: "/candidates", label: "候选", desc: "候选名单 · 逐票证据链", icon: Target, metric: "candidates" },
    { to: "/screener", label: "策略", desc: "策略假设 · 当前规则", icon: ListFilter },
    { to: "/factors", label: "因子", desc: "评分因子 · 来源与覆盖", icon: TableProperties, metric: "factors" },
    { to: "/backtest", label: "回测", desc: "变体台账 · 研究链", icon: BarChart3, metric: "variants" },
    { to: "/signals", label: "信号", desc: "信号台账 · 策略命中", icon: Radio },
    { to: "/market", label: "市场环境", desc: "行业 · 情绪 · 板块资金 · 市场风险", icon: Globe2 },
    { to: "/indices", label: "指数", desc: "宽基指数行情 · 隔夜外围数值", icon: TrendingUp },
    { to: "/data", label: "数据", desc: "来源健康 · 漏斗 · 时点", icon: Database },
    { to: "/stream", label: "消息", desc: "快讯 · 板块 · 概念异动", icon: Zap, metric: "messages" },
  ],
};

/** 决策线：持仓与跟进，按浏览器身份隔离。 */
export const DECISION_LINE: NavLine = {
  id: "decision",
  title: "决策",
  subtitle: "我的持仓 · 风险 · 跟进",
  scope: "private",
  items: [
    { to: "/my/holdings", label: "持仓", desc: "真实持仓 · 买卖条件", icon: Wallet, metric: "holdings" },
    { to: "/monitor", label: "监控", desc: "风险提醒 · 跟进 · 观察", icon: BellRing, metric: "alerts" },
    { to: "/review", label: "复盘", desc: "收盘回顾 · 明日待办", icon: ClipboardCheck },
    { to: "/evidence", label: "研究依据", desc: "消息复核 · 门禁 · 冻结参数", icon: Scale },
    { to: "/watchlist", label: "自选", desc: "我的关注 · 实时跟踪", icon: Star, metric: "watchlist" },
  ],
};

export const NAV_LINES: readonly NavLine[] = [RESEARCH_LINE, DECISION_LINE];

/** 移动端底栏只放最常用的三个入口。 */
export const PRIMARY_LINE: NavLine = {
  ...RESEARCH_LINE,
  items: [RESEARCH_LINE.items[0], RESEARCH_LINE.items[1], DECISION_LINE.items[0]],
};

export const DEFAULT_ROUTE = "/today";

/**
 * 旧路由 → 新路由。保留书签、外部链接与服务器上旧文档里的链接可用，
 * 不在页面上暴露任何"迁移"概念。
 *
 * 2026-11-09：模块拆分后补上新页面的旧入口映射。2026-10-09 的页面收敛
 * （issue #317）曾把资讯雷达 / 运行监控 / 策略实验室 / 历史结论 / 历史表现 /
 * 复盘笔记六个进阶页删掉，那批内容现在分别落在 /stream、/today、/candidates、
 * /evidence、/review —— 功能没丢，只是换成独立模块页而不是折叠区。
 */
export const LEGACY_ROUTES: Readonly<Record<string, string>> = {
  "/system/review": "/review",
  "/system/recommend": "/candidates",
  "/system/lab": "/evidence",
  "/system/archive": "/candidates",
  "/radar": "/stream",
  "/dashboard": "/today",
  "/lab": "/evidence",
  "/archive": "/candidates",
  "/performance": "/candidates",
  "/reviews": "/review",
  // 「我的测试」已升级为「我的持仓」；对照所需的额外列已并入「我的自选」。
  "/my/lab": "/my/holdings",
  "/my/watchlist": "/watchlist",
  "/concepts": "/stream",
  // 2026-11-09：/alerts 与 /observations 并入「监控」模块页，旧链接仍然可用。
  "/alerts": "/monitor",
  "/observations": "/monitor",
  // 笔记与研报仍在（用户可从旧链接进入），但不再占左栏一级模块位。
};
