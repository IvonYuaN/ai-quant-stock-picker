export const RESEARCH_SECTION_IDS = ["messages", "candidates", "discussion", "market-context"] as const;

export type ResearchSectionId = (typeof RESEARCH_SECTION_IDS)[number];