import type { AqspCandidate, AqspMessage, AqspSnapshot } from "@/types/aqsp";

export function candidateName(candidate: Pick<AqspCandidate, "symbol" | "display_name">): string {
  const symbol = candidate.symbol.trim();
  const displayName = candidate.display_name.trim();
  if (!displayName) return "名称未记录";
  if (!symbol) return displayName;
  return displayName.replace(new RegExp(`^(?:${symbol}\\s*)+`), "").trim() || displayName;
}

export function messagesForCandidate(messages: readonly AqspMessage[], symbol: string): readonly AqspMessage[] {
  return messages.filter((message) =>
    message.affected_symbols?.includes(symbol) && Boolean(message.source_url?.trim() || message.url?.trim()),
  );
}

export function candidateResearchReady(snapshot: AqspSnapshot, symbol: string): boolean {
  const hasMessage = messagesForCandidate(snapshot.messages, symbol).length > 0;
  const hasDebate = snapshot.debates.some((debate) => debate.symbol === symbol);
  const chainConfirmsDebate = snapshot.research_chain?.debated_symbols.includes(symbol) ?? false;
  return hasMessage && hasDebate && chainConfirmsDebate;
}

export function allCandidatesResearchReady(snapshot: AqspSnapshot): boolean {
  return snapshot.candidates.length > 0 && snapshot.candidates.every((candidate) =>
    candidateResearchReady(snapshot, candidate.symbol),
  );
}

export function sourceCoverageLines(snapshot: AqspSnapshot): readonly string[] {
  return (snapshot.market_context?.summary_lines ?? []).filter((line) =>
    /^(来源覆盖|时效筛选|消息结果)[:：]/.test(line.trim()),
  );
}

export function latestVariantDate(snapshot: AqspSnapshot): string {
  return (snapshot.variants ?? []).reduce(
    (latest, variant) => (variant.end_date > latest ? variant.end_date : latest),
    "",
  );
}

export function historicalVariantCount(snapshot: AqspSnapshot, symbol: string): number {
  return (snapshot.variants ?? []).filter((variant) =>
    variant.holdings?.some((holding) => holding.symbol === symbol),
  ).length;
}
