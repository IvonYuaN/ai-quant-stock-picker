import { RESEARCH_SECTION_IDS } from "./research-layout";

export const researchLayoutContract = {
  sectionsAreSeparate: RESEARCH_SECTION_IDS.join("|") === "messages|candidates|discussion|market-context",
  sectionsAreUnique: new Set(RESEARCH_SECTION_IDS).size === RESEARCH_SECTION_IDS.length,
};