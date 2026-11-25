// 收评 6 段卡片流展示模型（lib/closing-view）的契约断言。
//
// 钉住三条铁律：
//   1. sectionBody：首行 `## 标题` 必被剥掉（卡片头已展示中文标题，正文不重复）；
//   2. sectionBody：非标题首行 / 空串 原样保留（不吞正文）；
//   3. buildClosingSections：段序稳定、缺数据段默认折叠、空 markdown 判无数据。
import {
  allSectionsGapNotice,
  buildClosingSections,
  sectionBody,
  sectionGapCopy,
  sectionState,
} from "./closing-view";
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

/* ==========================================================================
 * 「为什么没有数据」的分档（五态 + unknown）
 *
 * 后端原来把「文件不存在 / 读失败 / 表头残缺 / 读到了但没可用行」一律折成
 * available:false，前端只能猜一句「盘后预载未落盘或数据源缺缓存」——
 * 于是「读失败」被写成「还没生成」，用户会去等下一次调度，而真正的问题是
 * 文件坏了 / 权限不对 / 磁盘满。误诊比不诊断更糟。
 * ========================================================================== */

function mkStateSection(
  key: string,
  state: string | undefined,
  available: boolean,
  markdown = "",
  reason = "",
): ClosingReviewSection {
  return { key, title: "标题-" + key, markdown, available, state, reason };
}

function viewsOf(sections: ClosingReviewSection[]) {
  return buildClosingSections({ as_of: "2026-11-25", sections });
}

export const closingSectionGapContract = {
  /* ---- 状态码透传 ---- */
  okWhenAvailable: sectionState(mkStateSection("k", "ok", true), true) === "ok",
  missingPassedThrough: sectionState(mkStateSection("k", "missing", false), false) === "missing",
  unreadablePassedThrough:
    sectionState(mkStateSection("k", "unreadable", false), false) === "unreadable",
  malformedPassedThrough:
    sectionState(mkStateSection("k", "malformed", false), false) === "malformed",
  emptyPassedThrough: sectionState(mkStateSection("k", "empty", false), false) === "empty",

  /* ---- 关键对照：不许猜 ---- */
  // 老后端没有 state 字段 ⇒ unknown。猜成 missing 就把「不知道为什么」说成了「还没生成」。
  legacyPayloadWithoutStateIsUnknown: sectionState(mkStateSection("k", undefined, false), false) === "unknown",
  unknownStringIsUnknown: sectionState(mkStateSection("k", "whatever", false), false) === "unknown",
  // 后端说 ok 却没有正文属自相矛盾 ⇒ unknown（不替它圆场说「有数据」）。
  okStateWithoutBodyIsUnknown: sectionState(mkStateSection("k", "ok", false), false) === "unknown",
  // available=true 永远压过状态码：正文在就是有数据。
  availableBeatsStateCode: sectionState(mkStateSection("k", "missing", true), true) === "ok",

  /* ---- 视图层：reason 只在缺数据时保留 ---- */
  reasonDroppedWhenAvailable:
    viewsOf([mkStateSection("k", "ok", true, "## 标题\n正文", "残留原因")])[0].reason === "",
  reasonKeptWhenUnavailable:
    viewsOf([mkStateSection("k", "unreadable", false, "", "产物读取失败：OSError")])[0].reason ===
    "产物读取失败：OSError",
  stateOnViewMatches: viewsOf([mkStateSection("k", "malformed", false)])[0].state === "malformed",

  /* ---- 徽标：五档必须两两不同（否则用户看不出区别） ---- */
  badgeTextsDistinct:
    new Set(
      (["ok", "missing", "unreadable", "malformed", "empty", "unknown"] as const).map(
        (state) => sectionGapCopy(state, "原因").badge,
      ),
    ).size === 6,
  okBadgeIsNotWarn: sectionGapCopy("ok", "").tone === "ok",
  okHasNoHint: sectionGapCopy("ok", "").hint === "",
  failureStatesAreWarn:
    sectionGapCopy("unreadable", "x").tone === "warn" && sectionGapCopy("malformed", "x").tone === "warn",
  missingIsNotWarn: sectionGapCopy("missing", "x").tone === "neutral",

  /* ---- 文案：失败不得被写成「还没生成」 ---- */
  unreadableSaysFailureNotMissing:
    sectionGapCopy("unreadable", "磁盘满").hint.includes("故障") &&
    sectionGapCopy("unreadable", "磁盘满").hint.includes("不是「还没生成」"),
  unreadableCarriesReason: sectionGapCopy("unreadable", "磁盘满").hint.includes("磁盘满"),
  missingSaysNotFailure: sectionGapCopy("missing", "产物未落盘").hint.includes("不是故障"),
  malformedSaysRegenerate: sectionGapCopy("malformed", "缺 title").hint.includes("重新生成"),
  unknownDoesNotClaimNoData:
    sectionGapCopy("unknown", "").hint.includes("后端未说明原因") &&
    sectionGapCopy("unknown", "").hint.includes("不要据此认为"),
  emptyIsNotMissing: sectionGapCopy("empty", "x").badge !== sectionGapCopy("missing", "x").badge,

  /* ---- 汇总句：6 段全空时也不能把故障说成等待 ---- */
  allGapNoticeNamesFailure: (() => {
    const text = allSectionsGapNotice(
      viewsOf([
        mkStateSection("a", "unreadable", false),
        mkStateSection("b", "missing", false),
        mkStateSection("c", "missing", false),
      ]),
    );
    return text.includes("故障") && text.includes("不是「盘后预载还没跑」");
  })(),
  allMissingNoticeSaysNotFailure: (() => {
    const text = allSectionsGapNotice(
      viewsOf([mkStateSection("a", "missing", false), mkStateSection("b", "missing", false)]),
    );
    return text.includes("不是故障") && !text.includes("读取失败");
  })(),
  allUnknownNoticeDoesNotClaimMissing: (() => {
    const text = allSectionsGapNotice(
      viewsOf([mkStateSection("a", undefined, false), mkStateSection("b", undefined, false)]),
    );
    return text.includes("状态未知") && !text.includes("未落盘");
  })(),
  mixedNoticeCountsEachBucket: (() => {
    const text = allSectionsGapNotice(
      viewsOf([
        mkStateSection("a", "missing", false),
        mkStateSection("b", "empty", false),
        mkStateSection("c", "unknown", false),
      ]),
    );
    return text.includes("1 段产物未落盘") && text.includes("1 段产物无内容") && text.includes("1 段状态未知");
  })(),
};
