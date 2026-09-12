import type { AqspAgentResult, AqspSnapshot } from "./api";
import type { AqspRecommendationGate } from "./api";

export type GatePresentation = "ready" | "research_only" | "unavailable";

export function gatePresentation(gate: AqspRecommendationGate | undefined): GatePresentation {
  if (!gate) return "unavailable";
  if (gate.status === "research_display" || !gate.recommendation_allowed) return "research_only";
  return "ready";
}

export function snapshotMatchesSelectedDate(
  snapshot: Pick<AqspSnapshot, "selected_date">,
  selectedDate: string,
): boolean {
  return !selectedDate || snapshot.selected_date === selectedDate;
}

export function snapshotConclusion(snapshot: AqspSnapshot): string {
  // Market context is evidence attached to the message lane, never a
  // substitute for the day's own conclusion.
  return snapshot.summaries.find((line) => line.includes("判断：")) || snapshot.summaries.find((line) =>
    !line.includes("未产出") && !line.includes("未形成独立") && !line.includes("复用"),
  ) || snapshot.summaries[0] || "";
}

export function isCurrentEmptyObservation(snapshot: AqspSnapshot): boolean {
  return Boolean(
    snapshot.meta?.historical === false &&
      snapshot.candidates.length === 0 &&
      snapshot.messages.length === 0 &&
      snapshot.recommendation_gate &&
      gatePresentation(snapshot.recommendation_gate) !== "ready",
  );
}

export function latestReviewDate(snapshot: AqspSnapshot): string {
  return snapshot.available_dates.find((date) => date !== snapshot.selected_date) || "";
}

export function dedupeResearchText(values: readonly string[]): string[] {
  const seen = new Set<string>();
  return values.reduce<string[]>((result, value) => {
    const text = value.trim();
    // 比较键去掉**全部**空白：中文研究文本里「过程摘要」与「过程   摘要」是同一条，
    // 只把连续空白压成一个空格是不够的（前者压根没有空格，两者仍不相等），
    // 结果是同一句话在 UI 里被显示两遍。
    const key = text.replace(/\s+/g, "");
    if (!key || seen.has(key)) return result;
    seen.add(key);
    result.push(text);
    return result;
  }, []);
}

export function mergeAvailableResearchDates(
  snapshotDates: readonly string[],
  indexedDates: readonly string[],
): string[] {
  return dedupeResearchText([...indexedDates, ...snapshotDates]);
}

export function messageSourceUrl(message: { source_url?: string; url?: string }): string {
  return message.source_url?.trim() || message.url?.trim() || "";
}

export function sameResearchText(left: string, right: string): boolean {
  const deduped = dedupeResearchText([left, right]);
  return deduped.length === 1 && Boolean(deduped[0]);
}

// 历史辩论文本里可能残留「看多 2 / 看空 2 / 中性 5」这类投票串。
// 清掉它之后还必须收拾留下的空分隔符，否则会渲染成「第 1 轮：；跨市传导复核」。
const LEGACY_VOTE_PATTERN = /看多\s*\d+\s*[/／]\s*看空\s*\d+\s*[/／]\s*中性\s*\d+/g;

/** 清洗单条辩论文本（轮次摘要 / 过程摘要共用同一套规则，避免两处口径不一）。 */
export function cleanDebateRoundText(value: string): string {
  return value
    .replace(LEGACY_VOTE_PATTERN, "")
    .replace(/\s*；\s*；/g, "；")
    .replace(/^\s*[；·]\s*|\s*[；·]\s*$/g, "")
    .trim();
}

export function debateProcessText(result: AqspAgentResult): string {
  if (result.process_summary) return cleanDebateRoundText(result.process_summary);
  const details: string[] = [];
  if (result.round_count > 0) details.push(`${result.round_count} 轮讨论`);
  if (result.active_roles.length > 0) details.push(`角色 ${result.active_roles.slice(0, 3).join("、")}`);
  return cleanDebateRoundText(details.join(" · "));
}

export function formatResearchDate(date: string): { day: string; weekday: string } {
  const value = new Date(`${date}T00:00:00+08:00`);
  if (Number.isNaN(value.getTime())) return { day: date, weekday: "" };
  return {
    day: new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(value),
    weekday: new Intl.DateTimeFormat("zh-CN", { weekday: "short" }).format(value),
  };
}
