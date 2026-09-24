// 契约测试：catalyst-client 的抓取与聚合逻辑（mock fetch，不发起真实请求）。
import {
  fetchCatalyst,
  filterEventsForSymbol,
  groupEventsBySector,
  impactLabel,
  impactToneClass,
  type CatalystEvent,
} from "./catalyst-client";

const sampleEvents: CatalystEvent[] = [
  {
    title: "半导体扶持政策出台",
    source: "财联社",
    published_at: "2026-09-20T09:00:00+08:00",
    impact: "positive",
    category: "政策",
    url: "https://example.com/1",
    affected_sectors: ["半导体", "设备"],
    affected_symbols: ["688981", "600000"],
    transmission_hypothesis: "政策 → 半导体 → 设备国产替代",
    transmission_path: ["政策", "半导体", "设备"],
  },
  {
    title: "某消费龙头业绩不及预期",
    source: "巨潮",
    published_at: "2026-09-20T10:00:00+08:00",
    impact: "negative",
    category: "业绩",
    url: "https://example.com/2",
    affected_sectors: ["食品饮料"],
    affected_symbols: ["600519"],
    transmission_hypothesis: "业绩下修 → 估值承压",
    transmission_path: ["业绩", "食品饮料"],
  },
];

const samplePayload = {
  date: "2026-09-20",
  generated_at: "2026-09-20T11:00:00+08:00",
  events: sampleEvents,
  source_status: "ok",
  warnings: [],
};

const savedFetch = globalThis.fetch;
globalThis.fetch = (async () =>
  ({
    ok: true,
    status: 200,
    json: async () => samplePayload,
  }) as unknown as Response) as typeof fetch;

const loaded = await fetchCatalyst();
globalThis.fetch = savedFetch;

export const catalystClientFixtureLoaded = loaded; // fixture，非断言
export const catalystClientParsesEvents = loaded.events.length === 2;

const groups = groupEventsBySector(sampleEvents);
export const catalystGroupHasSectors = groups.length === 3; // 半导体 / 设备 / 食品饮料
export const catalystGroupHasEvents = groups.every((group) => group.events.length >= 1);

export const catalystFilterExact = filterEventsForSymbol(sampleEvents, "600519").length === 1;
export const catalystFilterImpact = filterEventsForSymbol(sampleEvents, "600519")[0]?.impact === "negative";
export const catalystFilterCaseInsensitive = filterEventsForSymbol(sampleEvents, "688981").length === 1;
export const catalystFilterEmpty = filterEventsForSymbol(sampleEvents, "").length === 0;

export const catalystImpactLabelPositive = impactLabel("positive") === "利好";
export const catalystImpactLabelNegative = impactLabel("negative") === "利空";
export const catalystImpactLabelNeutral = impactLabel("neutral") === "中性";
export const catalystImpactTonePositive = impactToneClass("positive") === "aq-tone-up";
export const catalystImpactToneNegative = impactToneClass("negative") === "aq-tone-down";
export const catalystImpactToneNeutral = impactToneClass("neutral") === "";

// HTTP 错误应抛出
globalThis.fetch = (async () =>
  ({ ok: false, status: 500, json: async () => ({}) }) as unknown as Response) as typeof fetch;
let threwOnError = false;
try {
  await fetchCatalyst();
} catch {
  threwOnError = true;
}
globalThis.fetch = savedFetch;
export const catalystClientThrowsOnHttpError = threwOnError;

// 畸形 events 容错为空的事件列表
globalThis.fetch = (async () =>
  ({ ok: true, status: 200, json: async () => ({ events: "nope" }) }) as unknown as Response) as typeof fetch;
const broken = await fetchCatalyst();
globalThis.fetch = savedFetch;
export const catalystClientToleratesBadEvents = broken.events.length === 0;
