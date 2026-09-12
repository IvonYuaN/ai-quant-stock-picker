// 资讯雷达归一化的契约断言（由 npm test 真正执行）。
//
// 关键是 `hasContent`：后端无缓存时返回「赛道骨架」（items 全空），这与"今天没有资讯"
// 完全不同 —— 前者要点刷新去抓，后者才是真的空。
import { normalizeRadar } from "./radar-view";

const fullFixture = {
  generated_at: "2026-09-11 15:30",
  recent_days: 7,
  industries: [
    {
      key: "ai",
      name: "AI 算力",
      accent: "#f35d2b",
      total: 4,
      items: [
        { title: "算力订单超预期", url: "https://example.test/1", time: "09-11 10:20", ts: 1, summary: "摘要", source: "源A" },
        { title: "芯片供给缓解", url: "https://example.test/2", time: "09-11 09:00", ts: 2, summary: "", source: "源B" },
      ],
    },
    { key: "battery", name: "电池储能", accent: "#30a46c", total: 3, items: [] },
  ],
  stats: { industries: 12, total_sources: 96, failed_sources: 2 },
};

const skeletonFixture = {
  generated_at: "",
  recent_days: 7,
  industries: [
    { key: "ai", name: "AI 算力", accent: "#f35d2b", total: 4, items: [] },
    { key: "battery", name: "电池储能", accent: "#30a46c", total: 3, items: [] },
  ],
  stats: { industries: 12, total_sources: 96 },
};

const full = normalizeRadar(fullFixture);
const skeleton = normalizeRadar(skeletonFixture);

export const radarViewContract = {
  /* ---- 完整数据 ---- */
  generatedAtIsKept: full.generatedAt === "2026-09-11 15:30",
  recentDaysIsKept: full.recentDays === 7,
  industriesAreKept: full.industries.length === 2 && full.industries[0].name === "AI 算力",
  itemsAreKept: full.industries[0].items.length === 2,
  itemFieldsAreKept:
    full.industries[0].items[0].title === "算力订单超预期" &&
    full.industries[0].items[0].source === "源A" &&
    full.industries[0].items[0].url === "https://example.test/1",
  // 同赛道可能抓到同标题同时间的重复条目，key 必须带上序号否则 React 会报重复 key
  itemKeysAreUnique:
    new Set(full.industries[0].items.map((item) => item.key)).size === full.industries[0].items.length,
  statsAreKept: full.stats.industries === 12 && full.stats.totalSources === 96,
  failedSourcesIsKept: full.stats.failedSources === 2,
  fullHasContent: full.hasContent === true,

  /* ---- 骨架：有赛道但无条目 ---- */
  skeletonHasNoContent: skeleton.hasContent === false,
  skeletonKeepsIndustries: skeleton.industries.length === 2,
  skeletonFailedSourcesDefaultsToZero: skeleton.stats.failedSources === 0,
  skeletonGeneratedAtIsEmpty: skeleton.generatedAt === "",

  /* ---- 异常负载 ---- */
  nullYieldsEmpty: normalizeRadar(null).industries.length === 0,
  emptyObjectYieldsNoContent: normalizeRadar({}).hasContent === false,
  stringYieldsEmpty: normalizeRadar("boom").industries.length === 0,
  industriesWrongTypeYieldsEmpty: normalizeRadar({ industries: "oops" }).industries.length === 0,
  itemsWrongTypeYieldsEmpty:
    normalizeRadar({ industries: [{ key: "ai", items: "oops" }] }).industries[0].items.length === 0,
  missingAccentIsEmptyString: normalizeRadar({ industries: [{ key: "ai" }] }).industries[0].accent === "",
};
