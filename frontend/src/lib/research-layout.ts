export const RESEARCH_SECTION_IDS = ["messages", "candidates", "discussion", "market-context"] as const;
export const TEST_VARIANTS_SECTION_ID = "test-variants" as const;

export const RESEARCH_NAV_ITEMS = [
  { id: RESEARCH_SECTION_IDS[0], label: "消息证据", description: "来源与影响" },
  { id: RESEARCH_SECTION_IDS[1], label: "候选研究", description: "评分与依据" },
  { id: RESEARCH_SECTION_IDS[2], label: "讨论复核", description: "分歧与风险" },
  { id: RESEARCH_SECTION_IDS[3], label: "市场与产业链", description: "跨市与传导" },
] as const;

export type ResearchSectionId = (typeof RESEARCH_SECTION_IDS)[number];