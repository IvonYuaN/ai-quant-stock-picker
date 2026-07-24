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
  return snapshot.summaries[0] || "";
}

export function snapshotScanSummary(snapshot: Pick<AqspSnapshot, "summaries">): string {
  const line = snapshot.summaries.find((summary) => summary.includes("真实扫描"));
  return line || "";
}

export function isCurrentEmptyObservation(snapshot: AqspSnapshot): boolean {
  return Boolean(
      snapshot.meta?.historical === false &&
      snapshot.candidates.length === 0 &&
      (snapshot.observation_candidates ?? []).length === 0 &&
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
    const key = text.replace(/\s+/g, " ");
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
  const [first] = dedupeResearchText([left, right]);
  return Boolean(first) && dedupeResearchText([left, right]).length === 1;
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
