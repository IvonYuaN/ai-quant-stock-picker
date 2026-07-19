export const FORMAL_RESEARCH_SECTIONS = [
  { id: "overview", number: "01", label: "当天结论", description: "今日主线", countKey: "conclusion" },
  { id: "messages", number: "02", label: "消息证据", description: "来源与影响", countKey: "messages" },
  { id: "candidates", number: "03", label: "候选研究", description: "评分与依据", countKey: "candidates" },
  { id: "discussion", number: "04", label: "讨论复核", description: "分歧与风险", countKey: "debates" },
] as const;

export const RESEARCH_SECTION_IDS = FORMAL_RESEARCH_SECTIONS.map((section) => section.id) as [
  "overview",
  "messages",
  "candidates",
  "discussion",
];
export const TEST_VARIANTS_SECTION_ID = "test-variants" as const;
export const MARKET_CONTEXT_SECTION_ID = "market-context" as const;

export const RESEARCH_NAV_ITEMS = FORMAL_RESEARCH_SECTIONS;

export type ResearchSectionId = (typeof RESEARCH_SECTION_IDS)[number];
export type ResearchViewId = ResearchSectionId | typeof TEST_VARIANTS_SECTION_ID;

export function resolveResearchView(hash: string): ResearchViewId {
  const value = hash.replace(/^#/, "");
  return value === TEST_VARIANTS_SECTION_ID || RESEARCH_SECTION_IDS.includes(value as ResearchSectionId)
    ? (value as ResearchViewId)
    : RESEARCH_SECTION_IDS[0];
}
