// 复盘导出的契约断言（由 npm test 真正执行）。
//
// 重点：导出的东西要**能直接用** —— 表格不能因为竖线/换行而错列，
// 空候选不能导出一张空表，历史日期必须标明（避免日后回看时误当当天结论）。
import { buildDailyView } from "./daily-view";
import { dailyViewFixture } from "./daily-view.test";
import { buildReviewMarkdown, reviewFileName } from "./review-export";

const view = buildDailyView(dailyViewFixture);
const md = buildReviewMarkdown(view);

const emptyView = buildDailyView({ ...dailyViewFixture, candidates: [], debates: [], messages: [] });
const emptyMd = buildReviewMarkdown(emptyView);

const historicalMd = buildReviewMarkdown(
  buildDailyView({ ...dailyViewFixture, meta: { historical: true, stale: true } }),
);

const pipedMd = buildReviewMarkdown(
  buildDailyView({
    ...dailyViewFixture,
    candidates: dailyViewFixture.candidates.map((row, index) =>
      index === 0 ? { ...row, display_name: "A|B 名称" } : row,
    ),
  }),
);

const tableRows = md.split("\n").filter((line) => line.startsWith("| ") && !line.startsWith("| ---"));

export const reviewExportContract = {
  /* ---- 基本结构 ---- */
  hasDateInTitle: md.includes("# AQSP 每日复盘 · 2026-09-11"),
  hasConclusion: md.includes("## 当天结论") && md.includes(view.conclusion.slice(0, 12)),
  hasGate: md.includes("## 门禁") && md.includes(view.gate.label),
  hasChain: md.includes("## 证据链") && md.includes(view.chain.label),
  hasCrossMarket: md.includes("## 跨市主线"),
  hasDisclaimer: md.includes("不构成投资建议"),

  /* ---- 候选表 ---- */
  hasCandidatesSection: md.includes("## 候选（2）"),
  hasTableHeader: md.includes("| 代码 | 名称 | 评分 | 状态 | 证据 | 可复核 | 关键指标 |"),
  // 表头 + 2 行数据 = 3（分隔行以 "| ---" 开头，已排除）
  tableRowCountIsRight: tableRows.length === 3,
  readyCandidateMarked: md.includes("✅"),
  notReadyCandidateNotMarked: (md.match(/✅/g) ?? []).length === 1,

  /* ---- 表格安全：竖线必须转义，否则整行会错列 ---- */
  pipesAreEscaped: pipedMd.includes("A\\|B 名称"),
  pipedMdHasNoRawPipeInName: !pipedMd.includes("| A|B 名称 |"),

  /* ---- 空候选：不能导出一张空表 ---- */
  emptySaysNoCandidates: emptyMd.includes("当天没有通过数据质量与短线筛选的对象"),
  emptyHasNoTable: !emptyMd.includes("| 代码 | 名称 |"),
  emptyStillHasConclusion: emptyMd.includes("## 当天结论"),

  /* ---- 历史日期必须标明，否则日后回看会误当当天结论 ---- */
  historicalIsLabeled: historicalMd.includes("历史日期回看"),

  /* ---- 文件名 ---- */
  fileNameUsesDate: reviewFileName(view) === "复盘-2026-09-11.md",
  fileNameFallsBackWhenNoDate:
    reviewFileName(buildDailyView({ ...dailyViewFixture, selected_date: "" })) === "复盘-未记录日期.md",
};
