// 资讯雷达的归一化。
//
// 后端 `GET /api/radar` 在无缓存时返回「赛道骨架」（items 全空），有缓存时返回完整数据 ——
// 两种都是正常状态，但页面必须能区分：骨架要提示"点刷新去抓取"，而不是显示"没有资讯"。
import type { RadarData } from "./api";
import { asArray, asNumber, asRecord, asString } from "./safe";

export interface RadarItemView {
  key: string;
  title: string;
  url: string;
  time: string;
  source: string;
  summary: string;
}

export interface RadarIndustryView {
  key: string;
  name: string;
  accent: string;
  total: number;
  items: RadarItemView[];
}

export interface RadarView {
  generatedAt: string;
  recentDays: number;
  industries: RadarIndustryView[];
  stats: { industries: number; totalSources: number; failedSources: number };
  /** 是否真的抓到过内容（false = 骨架，需要点刷新）。 */
  hasContent: boolean;
}

function normalizeItem(raw: unknown, index: number): RadarItemView {
  const record = asRecord(raw);
  return {
    key: `${asString(record.title)}-${asString(record.time)}-${index}`,
    title: asString(record.title),
    url: asString(record.url),
    time: asString(record.time),
    source: asString(record.source),
    summary: asString(record.summary),
  };
}

function normalizeIndustry(raw: unknown): RadarIndustryView {
  const record = asRecord(raw);
  return {
    key: asString(record.key),
    name: asString(record.name),
    accent: asString(record.accent),
    total: asNumber(record.total),
    items: asArray<unknown>(record.items).map(normalizeItem),
  };
}

export function normalizeRadar(raw: unknown): RadarView {
  const record = asRecord(raw) as Partial<RadarData>;
  const stats = asRecord(record.stats);
  const industries = asArray<unknown>(record.industries).map(normalizeIndustry);
  return {
    generatedAt: asString(record.generated_at),
    recentDays: asNumber(record.recent_days),
    industries,
    stats: {
      industries: asNumber(stats.industries),
      totalSources: asNumber(stats.total_sources),
      failedSources: asNumber(stats.failed_sources),
    },
    hasContent: industries.some((industry) => industry.items.length > 0),
  };
}
