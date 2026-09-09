// 信息架构（IA）：把前端拆成「系统线」和「我的线」两条主线。
//
// 分线的依据不是功能模块，而是**数据的归属与读写性质**：
//   - 系统线 = 服务端产出的公共只读数据。任何人打开看到的都一样，不需要隔离，也不该被个人改动。
//   - 我的线 = 只属于当前浏览器的私有数据。它不上传服务端，因此天然不会与他人冲突。
//
// 这条分界线同时回答了「多人访问会不会串号」：公共数据本就该一致；私有数据不进服务端，故无从串起。
import {
  Archive,
  FlaskConical,
  FileText,
  LineChart,
  NotebookPen,
  ScrollText,
  Sparkles,
  Star,
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

export const SYSTEM_LINE: NavLine = {
  id: "system",
  title: "系统线",
  subtitle: "AI 自动产出 · 公共只读",
  scope: "public",
  items: [
    { to: "/system/review", label: "每日复盘", desc: "当天结论、证据与分歧", icon: ScrollText },
    { to: "/system/recommend", label: "今日推荐", desc: "通过门禁的候选", icon: Sparkles },
    { to: "/system/lab", label: "策略实验室", desc: "变体对比与门禁状态", icon: FlaskConical },
    { to: "/system/archive", label: "结论归档", desc: "按交易日回看", icon: Archive },
  ],
};

export const MY_LINE: NavLine = {
  id: "my",
  title: "我的线",
  subtitle: "只存此浏览器 · 不上传服务端",
  scope: "private",
  items: [
    { to: "/my/watchlist", label: "我的自选", desc: "关注股与分组", icon: Star },
    { to: "/my/lab", label: "我的测试", desc: "手动选股对照", icon: LineChart },
    { to: "/my/notes", label: "我的笔记", desc: "投研沉淀", icon: NotebookPen },
    { to: "/my/reports", label: "我的研报", desc: "私有资料", icon: FileText },
  ],
};

export const NAV_LINES: readonly NavLine[] = [SYSTEM_LINE, MY_LINE];

export const DEFAULT_ROUTE = SYSTEM_LINE.items[0].to;

export function isPrivateRoute(pathname: string): boolean {
  return pathname.startsWith("/my");
}
