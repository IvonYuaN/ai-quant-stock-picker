// 催化事件中枢的归一化。
//
// 后端 `GET /api/catalyst` 的数据契约（见 PR-X2）：
//   data.events[].impact ∈ positive | negative | neutral
//   data.{generated_at, source_status, warnings, events, ...}
// 后端未就绪时返回 `{ events: [], source_status: "no_data", warnings: [...] }`，
// 页面据此走空态降级，而不是白屏。
import type { CatalystData } from "./api";
import { asArray, asRecord, asString } from "./safe";

const IMPACT_LABEL: Record<string, string> = {
  positive: "利好",
  negative: "利空",
  neutral: "中性",
};

export interface CatalystEventView {
  key: string;
  title: string;
  summary: string;
  source: string;
  publishedAt: string;
  url: string;
  impact: string;
  impactLabel: string;
  category: string;
  verification: string;
  affectedSectors: string[];
  affectedSymbols: string[];
  transmissionPath: string[];
  timeHorizon: string;
}

export interface CatalystFacet {
  value: string;
  label: string;
}

export interface CatalystFacets {
  impacts: CatalystFacet[];
  categories: string[];
  sectors: string[];
}

export interface CatalystView {
  generatedAt: string;
  sourceStatus: string;
  warnings: string[];
  events: CatalystEventView[];
  facets: CatalystFacets;
}

function impactLabel(impact: string): string {
  return IMPACT_LABEL[impact] ?? (impact || "中性");
}

function normalizeEvent(raw: unknown, index: number): CatalystEventView {
  const record = asRecord(raw);
  return {
    key: `${asString(record.title)}-${asString(record.published_at)}-${index}`,
    title: asString(record.title),
    summary: asString(record.summary),
    source: asString(record.source),
    publishedAt: asString(record.published_at),
    url: asString(record.url),
    impact: asString(record.impact),
    impactLabel: impactLabel(asString(record.impact)),
    category: asString(record.category),
    verification: asString(record.verification),
    affectedSectors: asArray<unknown>(record.affected_sectors).map((value) => asString(value)),
    affectedSymbols: asArray<unknown>(record.affected_symbols).map((value) => asString(value)),
    transmissionPath: asArray<unknown>(record.transmission_path).map((value) => asString(value)),
    timeHorizon: asString(record.time_horizon),
  };
}

function uniqueNonEmpty(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    if (value && !seen.has(value)) {
      seen.add(value);
      out.push(value);
    }
  }
  return out;
}

export function normalizeCatalyst(raw: unknown): CatalystView {
  const record = asRecord(raw) as Partial<CatalystData>;
  const events = asArray<unknown>(record.events).map(normalizeEvent);
  const impacts = uniqueNonEmpty(events.map((event) => event.impact));
  const categories = uniqueNonEmpty(events.map((event) => event.category));
  const sectors = uniqueNonEmpty(events.flatMap((event) => event.affectedSectors));
  return {
    generatedAt: asString(record.generated_at),
    sourceStatus: asString(record.source_status),
    warnings: asArray<unknown>(record.warnings).map((value) => asString(value)),
    events,
    facets: {
      impacts: impacts.map((value) => ({ value, label: impactLabel(value) })),
      categories,
      sectors,
    },
  };
}
