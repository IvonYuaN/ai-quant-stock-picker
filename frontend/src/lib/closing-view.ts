// 收评日报 6 段卡片流的展示模型（纯函数层，组件只做渲染）。
//
// 数据契约来自后端 `GET /api/aqsp/closing-review`（市场级只读聚合，
// 复用 aqsp.briefing.closing_review 的 6 个 build_*_section）：
// 每段一个 { key, title, markdown, available }。builder 返回的 markdown
// 首行自带「## 段标题」，卡片头已展示中文标题，正文渲染前需剥掉首行避免重复。
//
// 纪律：本层不改任何后端语义；available=false 的段折叠置灰，
// 绝不用空段冒充有数据（与 daily-view 的「诚实空态」同口径）。

import type { ClosingReviewPayload, ClosingReviewSection } from "@/lib/api";

export type { ClosingReviewPayload, ClosingReviewSection };

export interface ClosingSectionView {
  key: string;
  title: string;
  /** 剥掉首行「## 标题」后的正文 markdown。 */
  body: string;
  available: boolean;
  /** 缺数据段默认折叠（置灰），有数据段默认展开。 */
  collapsedByDefault: boolean;
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

export function buildClosingSections(
  payload: ClosingReviewPayload | null,
): readonly ClosingSectionView[] {
  if (!payload) return [];
  return payload.sections.map((section) => ({
    key: section.key,
    title: section.title,
    body: sectionBody(section.markdown ?? ""),
    available: section.available && (section.markdown ?? "").trim().length > 0,
    collapsedByDefault: !(section.available && (section.markdown ?? "").trim().length > 0),
  }));
}
