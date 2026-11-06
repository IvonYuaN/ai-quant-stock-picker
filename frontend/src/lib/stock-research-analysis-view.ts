export interface StockResearchEvidence {
  title?: string;
  source?: string;
  as_of?: string;
  price_basis?: string;
  value?: unknown;
}

export interface ResolvedStockResearchEvidence {
  id: string;
  item: StockResearchEvidence | null;
}

export function resolveStockResearchEvidence(
  ids: readonly string[],
  evidence: Readonly<Record<string, StockResearchEvidence>>,
): ResolvedStockResearchEvidence[] {
  const seen = new Set<string>();
  return ids.flatMap((rawId) => {
    const id = rawId.trim();
    if (!id || seen.has(id)) return [];
    seen.add(id);
    return [{ id, item: evidence[id] ?? null }];
  });
}

export function formatStockResearchEvidenceValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "没有可展示的事实值";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "事实值无法展示";
  }
}
