import { RESEARCH_NAV_ITEMS, RESEARCH_SECTION_IDS, resolveResearchView, TEST_VARIANTS_SECTION_ID } from "./research-layout";

export const researchLayoutContract = {
  sectionsAreSeparate: RESEARCH_SECTION_IDS.join("|") === "overview|messages|candidates|discussion",
  sectionsAreUnique: new Set(RESEARCH_SECTION_IDS).size === RESEARCH_SECTION_IDS.length,
  navCoversEverySection: RESEARCH_NAV_ITEMS.map((item) => item.id).join("|") === RESEARCH_SECTION_IDS.join("|"),
  testVariantsHasStableAnchor: TEST_VARIANTS_SECTION_ID === "test-variants",
  unknownHashOpensConclusion: resolveResearchView("#unknown") === "overview",
  formalHashKeepsOneToOneMapping: RESEARCH_SECTION_IDS.every((section) => resolveResearchView(`#${section}`) === section),
  variantsStayOutsideFormalSections: !RESEARCH_SECTION_IDS.includes(resolveResearchView("#test-variants") as (typeof RESEARCH_SECTION_IDS)[number]),
};
