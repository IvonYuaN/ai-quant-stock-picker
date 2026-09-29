// 收评 6 段卡片流展示模型（lib/closing-view）的契约断言。
//
// 钉住三条铁律：
//   1. sectionBody：首行 `## 标题` 必被剥掉（卡片头已展示中文标题，正文不重复）；
//   2. sectionBody：非标题首行 / 空串 原样保留（不吞正文）；
//   3. buildClosingSections：段序稳定、缺数据段默认折叠、空 markdown 判无数据。
import { buildClosingSections, sectionBody } from "./closing-view";
import type { ClosingReviewSection, ClosingReviewPayload } from "@/lib/api";

const SECTION_KEYS = [
  "factor_ic",
  "board_fund",
  "longhubang",
  "news",
  "announcements",
  "holder_concentration",
];

function mkSection(key: string, markdown: string, available: boolean): ClosingReviewSection {
  return { key, title: "标题-" + key, markdown, available };
}

function mkPayload(): ClosingReviewPayload {
  return {
    as_of: "2026-09-29T15:00:00+08:00",
    sections: [
      mkSection("factor_ic", "## 因子 IC 健康（as-of 20260924）\n- momentum t=-2.52", true),
      mkSection("board_fund", "## 板块资金面（东财概念板块 · 主力资金）\n共 100 板块", true),
      mkSection("longhubang", "", false),
      mkSection("news", "## 财经快讯（财联社 · 近窗口）\n1. 某快讯", true),
      mkSection("announcements", "", false),
      mkSection("holder_concentration", "   ", false),
    ],
  };
}

export const closingViewContract = {
  /* ---- sectionBody：标题行剥离 ---- */
  bodyStripsLeadingHeading:
    sectionBody("## 板块资金面（东财概念板块 · 主力资金）\n共 100 板块｜上涨 60") ===
    "共 100 板块｜上涨 60",
  bodyKeepsNonHeadingFirstLine:
    sectionBody("（本批次无主力净流入/流出板块样本）") ===
    "（本批次无主力净流入/流出板块样本）",
  bodyEmptyStaysEmpty: sectionBody("") === "" && sectionBody("   \n  ") === "",
  bodySingleHeadingLine: sectionBody("## 只有标题行") === "",
  bodyMiddleHeadingUnaffected: sectionBody("行一\n## 行二") === "行一\n## 行二",

  /* ---- buildClosingSections：段序 + 折叠 + 空态 ---- */
  sectionOrderStable:
    buildClosingSections(mkPayload()).map((s) => s.key).join(",") === SECTION_KEYS.join(","),
  emptyPayloadGivesEmpty: buildClosingSections(null).length === 0,
  viewCountMatchesPayloadSections:
    buildClosingSections({ as_of: "x", sections: [mkSection("news", "## 快讯\n1", true)] }).length ===
    1,
  availableSectionExpandedByDefault:
    buildClosingSections(mkPayload()).find((s) => s.key === "factor_ic")?.collapsedByDefault ===
    false,
  emptyMarkdownForcedUnavailable:
    buildClosingSections(mkPayload()).find((s) => s.key === "longhubang")?.available === false,
  whitespaceMarkdownForcedUnavailable:
    buildClosingSections(mkPayload()).find((s) => s.key === "holder_concentration")?.available ===
    false,
  unavailableSectionCollapsedByDefault:
    buildClosingSections(mkPayload()).find((s) => s.key === "announcements")?.collapsedByDefault ===
    true,
};
