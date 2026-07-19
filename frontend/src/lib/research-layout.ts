export const RESEARCH_SECTION_IDS = ["messages", "candidates", "discussion"] as const;

export type ResearchSectionId = (typeof RESEARCH_SECTION_IDS)[number];
