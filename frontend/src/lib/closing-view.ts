// 收评日报 6 段卡片流的展示模型（纯函数层，组件只做渲染）。
//
// 数据契约来自后端 `GET /api/aqsp/closing-review`（市场级只读聚合，
// 复用 aqsp.briefing.closing_review 的 6 个 build_*_section）：
// 每段一个 { key, title, markdown, available, state, reason }。builder 返回的
// markdown 首行自带「## 段标题」，卡片头已展示中文标题，正文渲染前需剥掉首行避免重复。
//
// 🔴 本层的核心职责是**把「为什么没有数据」分档**。后端原来把四种情况一律
// `available: false`：文件不存在 / 读失败 / 表头残缺 / 读到了但没可用行。
// 前端当时只能猜一句「盘后预载未落盘或数据源缺缓存」——于是「读失败」被写成
// 「还没生成」，用户会去等下一次调度，而真正的问题是文件坏了 / 权限不对 / 磁盘满。
//
// 纪律：本层不改任何后端语义；非 ok 的段折叠置灰，
// 绝不用空段冒充有数据（与 daily-view 的「诚实空态」同口径）。

import type { ClosingReviewPayload, ClosingReviewSection } from "@/lib/api";

export type { ClosingReviewPayload, ClosingReviewSection };

/** 段的「有没有数据」五态，与后端 `SECTION_*` 常量一一对应。 */
export type ClosingSectionState = "ok" | "missing" | "unreadable" | "malformed" | "empty" | "unknown";

const KNOWN_STATES: readonly string[] = ["ok", "missing", "unreadable", "malformed", "empty"];

export interface ClosingSectionView {
  key: string;
  title: string;
  /** 剥掉首行「## 标题」后的正文 markdown。 */
  body: string;
  available: boolean;
  /** 缺数据段默认折叠（置灰），有数据段默认展开。 */
  collapsedByDefault: boolean;
  state: ClosingSectionState;
  /** 非 ok 时解释「为什么没有」；ok 时为空串。 */
  reason: string;
}

/**
 * 剥掉 markdown 首行（若为 `## 标题` 行），避免卡片头与正文标题重复。
 * 首行不是标题行时原样保留。
 */
export function sectionBody(markdown: string): string {
  const trimmed = markdown.trim();
  if (!trimmed) return "";
  const nl = trimmed.indexOf("\n");
  const first = nl === -1 ? trimmed : trimmed.slice(0, nl);
  if (first.startsWith("## ")) {
    const rest = nl === -1 ? "" : trimmed.slice(nl + 1);
    return rest.trim();
  }
  return trimmed;
}

/**
 * 段状态。`available` 是**权威**（它是后端 `bool(markdown.strip())` 的直译），
 * 状态码只用来解释「为什么不可用」。
 *
 * 🔴 拿不到状态码时落 `unknown`，**不许猜成 `missing`**：
 * 「不知道为什么不」比「编一个原因」诚实，后者会把读失败说成「还没生成」。
 */
export function sectionState(section: ClosingReviewSection, available: boolean): ClosingSectionState {
  if (available) return "ok";
  const raw = (section.state ?? "").trim();
  // 后端说 ok 却 available=false 属自相矛盾 ⇒ unknown（不替它圆场）。
  return raw !== "ok" && KNOWN_STATES.includes(raw) ? (raw as ClosingSectionState) : "unknown";
}

export function buildClosingSections(
  payload: ClosingReviewPayload | null,
): readonly ClosingSectionView[] {
  if (!payload) return [];
  return payload.sections.map((section) => {
    const body = sectionBody(section.markdown ?? "");
    const available = Boolean(section.available) && body.length > 0;
    return {
      key: section.key,
      title: section.title,
      body,
      available,
      collapsedByDefault: !available,
      state: sectionState(section, available),
      reason: available ? "" : (section.reason ?? "").trim(),
    };
  });
}

export interface SectionGapCopy {
  badge: string;
  tone: "ok" | "warn" | "neutral";
  /** 展开后的说明；`ok` 时为空串。 */
  hint: string;
}

/**
 * 段头徽标 + 缺数据说明。**每一档都必须说清「该做什么」**，
 * 因为用户的下一步动作完全不同：等预载 / 查权限 / 重新生成 / 无需动作。
 */
export function sectionGapCopy(state: ClosingSectionState, reason: string): SectionGapCopy {
  const why = reason || "后端未说明原因";
  switch (state) {
    case "ok":
      return { badge: "有数据", tone: "ok", hint: "" };
    case "missing":
      return {
        badge: "未生成",
        tone: "neutral",
        hint: `该段产物还没有落盘（${why}）。这是盘后预载还没跑，不是故障——预载跑完再看。`,
      };
    case "unreadable":
      return {
        badge: "读取失败",
        tone: "warn",
        hint: `该段产物在，但读不出来：${why}。这是故障（权限 / 磁盘 / 产物本身），不是「还没生成」。`,
      };
    case "malformed":
      return {
        badge: "产物残缺",
        tone: "warn",
        hint: `该段产物结构不对：${why}。需要重新生成产物，重试读取不会有变化。`,
      };
    case "empty":
      return {
        badge: "无内容",
        tone: "neutral",
        hint: `该段产物读到了，但里面没有可用内容（${why}）。`,
      };
    default:
      return {
        badge: "状态未知",
        tone: "neutral",
        hint: `后端没有说明这一段为什么没有数据（${why}）。不要据此认为「今天确实没有」。`,
      };
  }
}

/**
 * 6 段全不可用时的汇总句。**不许把「读失败」说成「没落盘」**——
 * 原来那句「盘后预载未落盘或数据源缺缓存」正是这个错误，它把故障引向了等待。
 */
export function allSectionsGapNotice(sections: readonly ClosingSectionView[]): string {
  const total = sections.length;
  const broken = sections.filter((s) => s.state === "unreadable" || s.state === "malformed").length;
  if (broken > 0) {
    return `今日收评 ${total} 段都拿不到数据，其中 ${broken} 段是读取失败或产物残缺——这是故障，不是「盘后预载还没跑」。`;
  }
  const missing = sections.filter((s) => s.state === "missing").length;
  if (missing === total) {
    return `今日收评 ${total} 段产物都还没有落盘（盘后预载未跑）。等预载跑完再来看，不是故障。`;
  }
  const parts: string[] = [];
  if (missing) parts.push(`${missing} 段产物未落盘`);
  const empty = sections.filter((s) => s.state === "empty").length;
  if (empty) parts.push(`${empty} 段产物无内容`);
  const unknown = sections.filter((s) => s.state === "unknown").length;
  if (unknown) parts.push(`${unknown} 段状态未知`);
  return `今日收评 ${total} 段暂无可用数据（${parts.join(" / ") || "原因未知"}）。`;
}
