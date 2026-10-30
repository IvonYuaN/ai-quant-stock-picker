import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { MemoryRouter } from "react-router-dom";
import { PageOutline } from "../components/ui/PageOutline";

const render = (entry: string) => renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: [entry] },
  createElement(PageOutline, { items: [{ id: "stock-research", label: "为什么关注" }, { id: "stock-strategy", label: "条件与跟进" }] })));
const historical = render("/stock/600000?date=2026-10-29#stock-strategy");
const unrelated = render("/stock/600000#candidate-observation");

export const pageOutlineContracts = {
  readingHistoricalEvidenceKeepsDateAndStock: historical.includes('href="/stock/600000?date=2026-10-29#stock-research"')
    && historical.includes('href="/stock/600000?date=2026-10-29#stock-strategy"'),
  selectedSectionIsALocationNotACompletedStep: historical.includes('aria-current="location"') && !historical.includes('aria-current="step"'),
  foreignDeepLinkDoesNotPretendAnySectionIsSelected: !unrelated.includes("aria-current"),
};
