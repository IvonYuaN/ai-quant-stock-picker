// 信息架构（IA）：把前端拆成「系统线」和「我的线」两条主线。
//
// 分线的依据不是功能模块，而是**数据的归属与读写性质**：
//   - 系统线 = 服务端产出的公共只读数据。任何人打开看到的都一样，不需要隔离，也不该被个人改动。
//   - 我的线 = 只属于当前浏览器的私有数据。它不上传服务端，因此天然不会与他人冲突。
//
// 这次改造把原来的「每日复盘」与「今日推荐」合并为单页「今日研究」：
// 两者本来就是同一个交易日的同一份数据（推荐 = 门禁 + 候选，复盘 = 结论 + 证据 + 分歧），
// 拆成两页只会让"今天到底能不能买"这个判断被割到两个入口里，还容易口径不一致。
// 现在按**阅读顺序**在页内分段：结论 → 推荐 → 候选 → 证据 → 讨论。
import {
  Activity,
  Archive,
  BookOpen,
  Compass,
  FileText,
  FlaskConical,
  Gauge,
  Globe2,
  NotebookPen,
  Rss,
  Star,
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
}

export interface NavLine {
  id: string;
  title: string;
  /** 这条线的数据性质，直接显示在侧栏，避免用户对"我的数据会不会被别人看到"产生疑虑 */
  subtitle: string;
  scope: DataScope;
  items: NavItem[];
}

// 小白视角重排（2026-10-08）：原来 12 个 jargon 入口平铺，普通人一开门就懵。
// 现在按「我会用到的」分三层 —— 主区只留每天真正要看的两件事，
// 进阶工具收进「进阶分析」一组，本机私有数据单独成「我的」。
export const PRIMARY_LINE: NavLine = {
  id: "primary",
  title: "每日选股",
  subtitle: "AI 挑的票 · 能不能买",
  scope: "public",
  items: [
    { to: "/today", label: "今日选股", desc: "今天 AI 挑的几只 · 白话理由", icon: Compass },
    { to: "/stream", label: "小道信息", desc: "财联社快讯 · 概念异动", icon: Zap },
    { to: "/market", label: "行情环境", desc: "大盘 · 情绪 · 涨跌家数", icon: Globe2 },
  ],
};

export const MY_LINE: NavLine = {
  id: "my",
  title: "我的",
  subtitle: "只存本机 · 不上传服务端",
  scope: "private",
  items: [
    { to: "/my/watchlist", label: "自选", desc: "关注的票与行情", icon: Star },
    { to: "/my/holdings", label: "持仓", desc: "台账与盈亏", icon: Wallet },
    { to: "/my/notes", label: "笔记", desc: "投研沉淀", icon: NotebookPen },
    { to: "/my/reports", label: "资料", desc: "私有资料", icon: FileText },
  ],
};

export const ADVANCED_LINE: NavLine = {
  id: "advanced",
  title: "进阶分析",
  subtitle: "给深度使用者",
  scope: "public",
  items: [
    { to: "/reviews", label: "复盘笔记", desc: "信号复盘 · 评分标签", icon: BookOpen },
    { to: "/dashboard", label: "运行监控", desc: "指标总览 · 系统健康", icon: Activity },
    { to: "/radar", label: "资讯雷达", desc: "12 赛道 RSS 聚合", icon: Rss },
    { to: "/lab", label: "策略实验室", desc: "变体对比与生命周期", icon: FlaskConical },
    { to: "/archive", label: "历史结论", desc: "按日回看与多日对比", icon: Archive },
    { to: "/performance", label: "历史表现", desc: "命中率与策略衰减", icon: Gauge },
  ],
};

export const NAV_LINES: readonly NavLine[] = [PRIMARY_LINE, MY_LINE, ADVANCED_LINE];

export const DEFAULT_ROUTE = "/today";

/**
 * 旧路由 → 新路由。保留书签、外部链接与服务器上旧文档里的链接可用，
 * 不在页面上暴露任何"迁移"概念。
 */
export const LEGACY_ROUTES: Readonly<Record<string, string>> = {
  "/system/review": "/today",
  "/system/recommend": "/today#candidates",
  "/system/lab": "/lab",
  "/system/archive": "/archive",
  // 「我的测试」已升级为「我的持仓」；对照所需的额外列已并入「我的自选」。
  "/my/lab": "/my/holdings",
};
