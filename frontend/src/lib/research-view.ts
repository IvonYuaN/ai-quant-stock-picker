import type { AqspAgentResult, AqspSnapshot } from "./api";

export function snapshotConclusion(snapshot: AqspSnapshot): string {
  return snapshot.summaries[0] || snapshot.market_context?.overview || "";
}

export function debateProcessText(result: AqspAgentResult): string {
  if (result.process_summary) return result.process_summary;
  const details: string[] = [];
  if (result.round_count > 0) details.push(`${result.round_count} 轮讨论`);
  if (result.active_roles.length > 0) details.push(`角色 ${result.active_roles.slice(0, 3).join("、")}`);
  return details.join(" · ");
}

export function formatResearchDate(date: string): { day: string; weekday: string } {
  const value = new Date(`${date}T00:00:00+08:00`);
  if (Number.isNaN(value.getTime())) return { day: date, weekday: "" };
  return {
    day: new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(value),
    weekday: new Intl.DateTimeFormat("zh-CN", { weekday: "short" }).format(value),
  };
}
