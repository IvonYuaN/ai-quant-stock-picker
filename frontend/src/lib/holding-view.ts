import type { AlertEvent, AqspCandidate, Holding, Quote } from "./api";
import { ownedKind } from "./ownership";

export const PORTFOLIO_CHANGED_EVENT = "aqsp:portfolio-changed";
export const ALERTS_CHANGED_EVENT = "aqsp:alerts-changed";

export type HoldingGroupKind = "triggered" | "holding" | "candidate-watch" | "watch";

export interface HoldingWatchRow {
  code: string;
  name: string;
  holding?: Holding;
  quote?: Quote;
  candidate?: AqspCandidate;
  watched: boolean;
  alerts: readonly AlertEvent[];
  sectors: readonly string[];
  group: HoldingGroupKind;
}

export interface HoldingWatchGroup {
  kind: HoldingGroupKind;
  label: string;
  rows: HoldingWatchRow[];
}

/** 同一代码只出现一次；风险优先，之后保留持仓与自选的原始顺序。 */
export function buildHoldingGroups(input: {
  holdings: readonly Holding[];
  watchlist: readonly string[];
  alerts: readonly AlertEvent[];
  candidates: readonly AqspCandidate[];
  quotes?: Readonly<Record<string, Quote>>;
  sectors?: Readonly<Record<string, readonly string[]>>;
  sector?: string;
}): HoldingWatchGroup[] {
  const groups: HoldingWatchGroup[] = [
    { kind: "triggered", label: "风险已触发", rows: [] },
    { kind: "holding", label: "我的持仓", rows: [] },
    { kind: "candidate-watch", label: "命中今日候选的自选", rows: [] },
    { kind: "watch", label: "其他自选", rows: [] },
  ];
  const held = new Map(input.holdings.map((row) => [row.code, row]));
  const watched = new Set(input.watchlist);
  const ownership = { holdings: new Set(held.keys()), watchlist: watched };
  const candidates = new Map(input.candidates.map((row) => [row.symbol, row]));
  const codes = new Set([...held.keys(), ...watched]);
  for (const code of codes) {
    const holding = held.get(code);
    const quote = input.quotes?.[code];
    const candidate = candidates.get(code);
    const alerts = input.alerts.filter((alert) => alert.symbol === code && alert.status === "OPEN");
    const sectors = input.sectors?.[code] ?? [];
    if (input.sector && !sectors.includes(input.sector)) continue;
    const group: HoldingGroupKind = alerts.length > 0 ? "triggered"
      : ownedKind(code, ownership) === "holding" ? "holding"
      : candidate ? "candidate-watch" : "watch";
    groups.find((item) => item.kind === group)!.rows.push({
      code,
      name: holding?.name || quote?.name || candidate?.display_name || code,
      holding, quote, candidate, watched: watched.has(code), alerts, sectors, group,
    });
  }
  return groups;
}

/** 板块接口无资料时保留空值，不从股票名称猜所属行业。 */
export function normalizeHoldingSectors(raw: unknown): string[] {
  if (!raw || typeof raw !== "object") return [];
  const payload = raw as { boards?: unknown; concept_tags?: unknown };
  const tags = Array.isArray(payload.concept_tags) ? payload.concept_tags : [];
  const boards = Array.isArray(payload.boards) ? payload.boards : [];
  return [...new Set([...tags, ...boards.map((board) => {
    return board && typeof board === "object" ? (board as { name?: unknown }).name : undefined;
  })].filter((name): name is string => typeof name === "string" && name.trim().length > 0))];
}
