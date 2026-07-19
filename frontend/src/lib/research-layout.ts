export const RESEARCH_SECTION_IDS = ["overview", "messages", "candidates", "discussion"] as const;
export const TEST_VARIANTS_SECTION_ID = "test-variants" as const;
export const MARKET_CONTEXT_SECTION_ID = "market-context" as const;

export const RESEARCH_NAV_ITEMS = [
  { id: RESEARCH_SECTION_IDS[0], label: "当天结论", description: "今日主线", countKey: "conclusion" },
  { id: RESEARCH_SECTION_IDS[1], label: "消息证据", description: "来源与影响", countKey: "messages" },
  { id: RESEARCH_SECTION_IDS[2], label: "候选研究", description: "评分与依据", countKey: "candidates" },
  { id: RESEARCH_SECTION_IDS[3], label: "讨论复核", description: "分歧与风险", countKey: "debates" },
] as const;

export type ResearchSectionId = (typeof RESEARCH_SECTION_IDS)[number];
export type ResearchViewId = ResearchSectionId | typeof TEST_VARIANTS_SECTION_ID;

export function resolveResearchView(hash: string): ResearchViewId {
  const value = hash.replace(/^#/, "");
  return value === TEST_VARIANTS_SECTION_ID || RESEARCH_SECTION_IDS.includes(value as ResearchSectionId)
    ? (value as ResearchViewId)
    : RESEARCH_SECTION_IDS[0];
}
