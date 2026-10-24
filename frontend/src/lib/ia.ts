// T+1 工作台只有四个主入口。导航收敛不删除原数据与端点。
// 持仓按服务端访客隔离，自选/笔记保留原浏览器存储。
import {
  Compass,
  Globe2,
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
// 现在按「我会用到的」收敛为一条四项主线：今日、我的持仓、大盘、消息。
// 量化过程与历史资料留在页面内的证据折叠区，不再占用主导航。
export const PRIMARY_LINE: NavLine = {
  id: "primary",
  title: "T+1 决策",
  subtitle: "持仓 · 风险 · 跟进",
  scope: "public",
  items: [
    { to: "/today", label: "今日", desc: "持仓提醒 · 今日候选", icon: Compass },
    { to: "/my/holdings", label: "我的持仓", desc: "持仓与自选 · 操作跟进", icon: Wallet },
    { to: "/market", label: "大盘", desc: "大盘 · 情绪 · 涨跌家数", icon: Globe2 },
    { to: "/stream", label: "消息", desc: "消息线索 · 事实核验", icon: Zap },
  ],
};

export const MY_LINE: NavLine = {
  id: "my",
  title: "我的",
  subtitle: "当前身份隔离保存",
  scope: "private",
  items: [
    { to: "/my/holdings", label: "我的持仓", desc: "持仓与自选 · 操作跟进", icon: Wallet },
  ],
};

export const NAV_LINES: readonly NavLine[] = [PRIMARY_LINE];

export const DEFAULT_ROUTE = "/today";

/**
 * 旧路由 → 新路由。保留书签、外部链接与服务器上旧文档里的链接可用，
 * 不在页面上暴露任何"迁移"概念。
 *
 * W4 页面收敛（issue #317，2026-10-09）到此**全部完成**：仓主反馈「前端太复杂、
 * 很多多余内容」⇒ 侧栏只留「每日选股 + 小道信息 + 行情环境 + 我的」。
 * 二阶段逐页删除了 6 个进阶页文件（含本次的复盘笔记）：
 *   - 资讯雷达 /radar → /stream（功能由「小道信息流」承接）
 *   - 运行监控 /dashboard → /today（纯运维视角，不属于每日选股主链）
 *   - 策略实验室 /lab → /today（变体数据仍在「今日选股」的历史变体列可见）
 *   - 历史结论 /archive → /today
 *   - 历史表现 /performance → /today
 *   - 复盘笔记 /reviews → /today（被删的是**系统侧复盘/笔记/资料**的输出页 —— 这类内容
 *     由系统（agent）产出、不该让用户手写；系统侧写入通道见
 *     outputs/系统侧复盘写入通道_提案_2026-10-09.md）
 *
 *     ⚠️ 与**用户自己的**笔记页 `/my/notes`（见上方侧栏项）不是同一回事：后者**有意保留**。
 *     故「页面侧收敛完成」≠「用户不再有笔记功能」。此前本条注释写作「笔记是系统的任务、
 *     不是让用户写」，与侧栏仍在的 `/my/notes` 自相矛盾（issue #347，2026-10-12 订正）。
 */
export const LEGACY_ROUTES: Readonly<Record<string, string>> = {
  "/system/review": "/today",
  "/system/recommend": "/today#candidates",
  "/system/lab": "/lab",
  "/system/archive": "/archive",
  "/radar": "/stream",
  "/dashboard": "/today",
  "/lab": "/today",
  "/archive": "/today",
  "/performance": "/today",
  "/reviews": "/today",
  // 「我的测试」已升级为「我的持仓」；对照所需的额外列已并入「我的自选」。
  "/my/lab": "/my/holdings",
  "/my/watchlist": "/my/holdings#watchlist",
  "/concepts": "/stream",
};
