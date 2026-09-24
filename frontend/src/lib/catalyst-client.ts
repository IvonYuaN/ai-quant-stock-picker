// 催化事件客户端（PR-X3 自包含实现）。
//
// 为什么不直接加到 api.ts：PR-X2 也在改 api.ts（加 api.catalyst），
// 为避免合并冲突，这里独立封装，直接走 /api（vite 代理到 FastAPI:8900）。
// 类型（CatalystEvent / CatalystData）镜像后端的 CatalystReport，但隔离在本文件，
// 不依赖 PR-X2 的改动。只读聚合，不做评分 / 预测。
import { authHeaders } from "@/lib/api";

export type CatalystImpact = "positive" | "negative" | "neutral";

export interface CatalystEvent {
  title: string;
  source: string;
  published_at: string;
  symbol?: string;
  name?: string;
  impact: CatalystImpact;
  category?: string;
  url?: string;
  summary?: string;
  verification?: string;
  confidence?: number;
  affected_sectors: string[];
  affected_symbols: string[];
  transmission_hypothesis?: string;
  transmission_path: string[];
}

export interface CatalystData {
  date?: string;
  generated_at?: string;
  events: CatalystEvent[];
  source_status?: string;
  warnings?: string[];
}

export class CatalystError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export async function fetchCatalyst(signal?: AbortSignal): Promise<CatalystData> {
  let resp: Response;
  try {
    resp = await fetch("/api/catalyst", { headers: authHeaders(), signal });
  } catch {
    throw new CatalystError("连接不到后端，请先启动 backend（uvicorn app:app --port 8900）", 0);
  }
  if (!resp.ok) throw new CatalystError(`HTTP ${resp.status}`, resp.status);
  const payload = (await resp.json().catch(() => null)) as unknown;
  return normalizeCatalystPayload(payload);
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function normalizeImpact(value: unknown): CatalystImpact {
  return value === "positive" || value === "negative" ? value : "neutral";
}

function normalizeEvent(item: Record<string, unknown>): CatalystEvent {
  return {
    title: String(item.title ?? ""),
    source: String(item.source ?? ""),
    published_at: String(item.published_at ?? ""),
    symbol: typeof item.symbol === "string" ? item.symbol : undefined,
    name: typeof item.name === "string" ? item.name : undefined,
    impact: normalizeImpact(item.impact),
    category: typeof item.category === "string" ? item.category : undefined,
    url: typeof item.url === "string" ? item.url : undefined,
    summary: typeof item.summary === "string" ? item.summary : undefined,
    verification: typeof item.verification === "string" ? item.verification : undefined,
    confidence: typeof item.confidence === "number" ? item.confidence : undefined,
    affected_sectors: asStringArray(item.affected_sectors),
    affected_symbols: asStringArray(item.affected_symbols),
    transmission_hypothesis:
      typeof item.transmission_hypothesis === "string" ? item.transmission_hypothesis : undefined,
    transmission_path: asStringArray(item.transmission_path),
  };
}

/** 容错归一化：空 / 畸形载荷都降级为空事件列表，不抛错。 */
export function normalizeCatalystPayload(payload: unknown): CatalystData {
  if (!payload || typeof payload !== "object") return { events: [] };
  const obj = payload as Record<string, unknown>;
  const inner = obj.data && typeof obj.data === "object" ? (obj.data as Record<string, unknown>) : obj;
  const rawEvents = Array.isArray(inner.events) ? inner.events : [];
  const events = rawEvents
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map(normalizeEvent);
  return {
    date: typeof inner.date === "string" ? inner.date : undefined,
    generated_at: typeof inner.generated_at === "string" ? inner.generated_at : undefined,
    source_status: typeof inner.source_status === "string" ? inner.source_status : undefined,
    warnings: Array.isArray(inner.warnings) ? inner.warnings.map(String) : [],
    events,
  };
}

/** 按 affected_symbols（或 event.symbol）过滤，代码大小写归一化。 */
export function filterEventsForSymbol(events: readonly CatalystEvent[], code: string): CatalystEvent[] {
  const target = code.trim().toUpperCase();
  if (!target) return [];
  return events.filter(
    (event) =>
      (event.affected_symbols ?? []).some((symbol) => symbol.trim().toUpperCase() === target) ||
      (event.symbol ?? "").trim().toUpperCase() === target,
  );
}

/** 按 affected_sectors 聚合；无板块的事件归入「未分类」。 */
export function groupEventsBySector(
  events: readonly CatalystEvent[],
): Array<{ sector: string; events: CatalystEvent[] }> {
  const map = new Map<string, CatalystEvent[]>();
  for (const event of events) {
    const sectors = event.affected_sectors.length > 0 ? event.affected_sectors : ["未分类"];
    for (const sector of sectors) {
      const list = map.get(sector) ?? [];
      list.push(event);
      map.set(sector, list);
    }
  }
  return [...map.entries()].map(([sector, evts]) => ({ sector, events: evts }));
}

export function impactLabel(impact: CatalystImpact): string {
  if (impact === "positive") return "利好";
  if (impact === "negative") return "利空";
  return "中性";
}

// A 股口径：涨 / 利好 = 红（up），跌 / 利空 = 绿（down），0 / 中性无边色。
export function impactToneClass(impact: CatalystImpact): string {
  if (impact === "positive") return "aq-tone-up";
  if (impact === "negative") return "aq-tone-down";
  return "";
}
