// 催化事件中枢归一化的契约断言（由 npm test 真正执行）。
//
// 数据来自脱敏 fixture：混合 impact / sector / 缺失字段，验证 facets 派生与失败降级。
import { normalizeCatalyst } from "./catalyst-view";

const desensitizedFixture = {
  generated_at: "2026-09-20T15:00:00+08:00",
  source_status: "ok",
  warnings: [],
  events: [
    {
      title: "某公司中标大单",
      summary: "摘要一",
      source: "财联社",
      published_at: "2026-09-20T10:00:00+08:00",
      symbol: "600000",
      name: "某银行",
      impact: "positive",
      category: "公告",
      weight: 0.8,
      confidence: 0.6,
      verification: "已核实",
      url: "https://example.com/1",
      affected_sectors: ["银行", "金融"],
      affected_symbols: ["600000", "601398"],
      transmission_path: ["政策", "情绪"],
      transmission_hypothesis: "假设一",
      time_horizon: "短期",
    },
    {
      title: "行业监管收紧",
      summary: "摘要二",
      source: "同花顺",
      published_at: "2026-09-20T11:00:00+08:00",
      symbol: "000001",
      name: "某保险",
      impact: "negative",
      category: "政策",
      weight: 0.5,
      confidence: 0.4,
      verification: "待核实",
      url: "",
      affected_sectors: ["保险"],
      affected_symbols: ["000001"],
      transmission_path: [],
      transmission_hypothesis: "假设二",
      time_horizon: "中期",
    },
    {
      title: "中性跟踪",
      summary: "",
      source: "东财",
      published_at: "2026-09-20T12:00:00+08:00",
      symbol: "",
      name: "",
      impact: "neutral",
      category: "公告",
      weight: 0.1,
      confidence: 0.2,
      verification: "",
      url: "https://example.com/3",
      affected_sectors: [],
      affected_symbols: [],
      transmission_path: [],
      transmission_hypothesis: "",
      time_horizon: "长期",
    },
  ],
};

const emptyFixture = { events: [], source_status: "no_data", warnings: [] };

const catalystViewFixture = normalizeCatalyst(desensitizedFixture);

export const catalystViewContract = {
  preservesEventCount: catalystViewFixture.events.length === 3,

  impactLabelsMapped:
    catalystViewFixture.events[0].impactLabel === "利好" &&
    catalystViewFixture.events[1].impactLabel === "利空" &&
    catalystViewFixture.events[2].impactLabel === "中性",

  sourceStatusForwarded: catalystViewFixture.sourceStatus === "ok",
  generatedAtForwarded: catalystViewFixture.generatedAt === "2026-09-20T15:00:00+08:00",

  facetsImpactsDistinct: catalystViewFixture.facets.impacts.length === 3,
  facetsCategoriesDistinct: catalystViewFixture.facets.categories.length === 2,
  facetsSectorsDistinct: catalystViewFixture.facets.sectors.length === 3,

  facetsSectorNames:
    catalystViewFixture.facets.sectors.includes("银行") &&
    catalystViewFixture.facets.sectors.includes("金融") &&
    catalystViewFixture.facets.sectors.includes("保险"),

  affectedSymbolsCarried: catalystViewFixture.events[0].affectedSymbols.length === 2,

  emptyListSafe: normalizeCatalyst(emptyFixture).events.length === 0,
  emptyFacetsSafe: normalizeCatalyst(emptyFixture).facets.impacts.length === 0,
  emptySourceStatus: normalizeCatalyst(emptyFixture).sourceStatus === "no_data",

  missingFieldsSafe: (() => {
    const view = normalizeCatalyst({ events: [{}] });
    return (
      view.events.length === 1 &&
      view.events[0].impactLabel === "中性" &&
      view.events[0].affectedSymbols.length === 0 &&
      view.events[0].affectedSectors.length === 0
    );
  })(),
};
