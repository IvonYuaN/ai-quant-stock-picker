// 复盘导出：把当天结论整理成一份可存档 / 可分享的 Markdown。
//
// 为什么需要：复盘的价值在于**沉淀与回看**。结论散在页面上，过几天就只剩印象；
// 落到一份结构化文本里，才能存进笔记、贴进周报、或者跟前几天的对比。
//
// 这里是纯函数（不碰 DOM），导出动作在 components/aqsp/ReviewExportButton.tsx。
import type { DailyView } from "./daily-view";

/** 表格单元格里的竖线会破坏 Markdown 表格，必须转义。 */
function cell(value: string): string {
  return value.replace(/\|/g, "\\|").replace(/\n/g, " ").trim() || "—";
}

function block(title: string, body: string[]): string {
  const lines = body.filter((line) => line.trim().length > 0);
  if (lines.length === 0) return "";
  return `## ${title}\n\n${lines.join("\n\n")}\n`;
}

export function buildReviewMarkdown(view: DailyView): string {
  const parts: string[] = [];

  parts.push(`# AQSP 每日复盘 · ${view.date || "日期未记录"}\n`);
  parts.push(
    `> 更新 ${view.generatedAt || "—"} · ${view.isHistorical ? "历史日期回看" : "当前数据"}\n`,
  );

  parts.push(block("当天结论", [view.conclusion]));

  parts.push(block("门禁", [`**${view.gate.label}** —— ${view.gate.detail}`]));

  if (view.crossMarket) {
    const cm = view.crossMarket;
    parts.push(
      block("跨市主线", [
        `${cm.theme}（强度 ${cm.strength}）`,
        `先看：${cm.watch}`,
        `确认：${cm.confirm}`,
        `失效：${cm.invalid}`,
      ]),
    );
  }

  if (view.candidates.length > 0) {
    const header = "| 代码 | 名称 | 评分 | 状态 | 证据 | 可复核 | 关键指标 |";
    const split = "| --- | --- | --- | --- | --- | --- | --- |";
    const rows = view.candidates.map(
      (row) =>
        `| ${cell(row.symbol)} | ${cell(row.name)} | ${cell(row.scoreText)} | ${cell(row.status)} | ${cell(
          row.evidence,
        )} | ${row.ready ? "✅" : "—"} | ${cell(row.keyMetric)} |`,
    );
    parts.push(block(`候选（${view.candidates.length}）`, [[header, split, ...rows].join("\n")]));

    const notes = view.candidates
      .filter((row) => row.nextStep)
      .map((row) => `- ${row.name}（${row.symbol}）：${row.nextStep}`);
    if (notes.length > 0) parts.push(block("下一步观察", notes));
  } else {
    parts.push(block("候选", ["当天没有通过数据质量与短线筛选的对象。"]));
  }

  parts.push(block("证据链", [`**${view.chain.label}** —— ${view.chainDetail}`]));

  if (view.phaseLanes.length > 0) {
    parts.push(
      block(
        "阶段产出",
        view.phaseLanes.map((phase) => {
          const base = `- ${phase.label}：${phase.status}（候选 ${phase.candidateCount}）`;
          return phase.note ? `${base} · ${phase.note}` : base;
        }),
      ),
    );
  }

  parts.push("---\n");
  parts.push("*由 AQSP 生成 · 仅供研究，不构成投资建议*\n");

  return parts.filter((part) => part.trim().length > 0).join("\n");
}

/** 导出文件名：复盘-2026-09-11.md */
export function reviewFileName(view: DailyView): string {
  return `复盘-${view.date || "未记录日期"}.md`;
}
