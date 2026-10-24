import type { CatalystData } from "./catalyst-client";
import { shanghaiDate } from "./portfolio-view";

const sourceLabels = new Map([
  ["ok", "来源正常"], ["partial", "部分来源缺失"], ["failed", "来源失败"],
  ["timeout", "来源超时"], ["no_data", "报告尚未生成"],
]);

export function catalystReportMetadata(data: CatalystData, currentDay = shanghaiDate()) {
  const rawDate = data.date || "";
  const timestamp = /^\d{4}-\d{2}-\d{2}$/.test(rawDate) ? Date.parse(`${rawDate}T00:00:00Z`) : NaN;
  const validDate = Number.isFinite(timestamp) && new Date(timestamp).toISOString().slice(0, 10) === rawDate;
  const dateStatus = !validDate ? "报告日期未验证"
    : rawDate < currentDay ? "历史报告 · 非今日信号"
      : rawDate > currentDay ? "报告日期异常 · 待核验" : "当日报告";
  return {
    reportDate: validDate ? rawDate : "未记录有效日期",
    dateStatus,
    current: validDate && rawDate === currentDay,
    sourceLabel: sourceLabels.get(data.source_status || "") || "来源状态未记录",
    sourceOk: data.source_status === "ok",
    warningCount: data.warnings?.length || 0,
  };
}

/** Only report-provided HTTP(S) links can leave the page. */
export function catalystSourceUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : undefined;
  } catch {
    return undefined;
  }
}
