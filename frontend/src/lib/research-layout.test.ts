import { RESEARCH_SECTION_IDS } from "./research-layout";

export const researchLayoutContract = {
  sectionsAreSeparate: RESEARCH_SECTION_IDS.join("|") === "messages|candidates|discussion",
  sectionsAreUnique: new Set(RESEARCH_SECTION_IDS).size === RESEARCH_SECTION_IDS.length,
};
