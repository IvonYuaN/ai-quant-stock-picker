import { RESEARCH_NAV_ITEMS, RESEARCH_SECTION_IDS, TEST_VARIANTS_SECTION_ID } from "./research-layout";

export const researchLayoutContract = {
  sectionsAreSeparate: RESEARCH_SECTION_IDS.join("|") === "messages|candidates|discussion|market-context",
  sectionsAreUnique: new Set(RESEARCH_SECTION_IDS).size === RESEARCH_SECTION_IDS.length,
  navCoversEverySection: RESEARCH_NAV_ITEMS.map((item) => item.id).join("|") === RESEARCH_SECTION_IDS.join("|"),
  testVariantsHasStableAnchor: TEST_VARIANTS_SECTION_ID === "test-variants",
};
