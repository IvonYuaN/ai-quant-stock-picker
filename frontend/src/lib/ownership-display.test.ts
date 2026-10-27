import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { MemoryRouter } from "react-router-dom";
import { CandidateSection } from "../components/aqsp/sections/CandidateSection";
import { buildDailyView } from "./daily-view";
import { dailyViewFixture } from "./daily-view.test";

const view = buildDailyView(dailyViewFixture);
const section = view.sections.find((row) => row.id === "candidates")!;
const render = (ownedHoldings: [] | null) => renderToStaticMarkup(createElement(MemoryRouter, null,
  createElement(CandidateSection, { view, section, ownedHoldings })));
const unknown = render(null);
const knownEmpty = render([]);
export const ownershipDisplayContracts = {
  unknownDoesNotRenderZeroHoldingCount: unknown.includes("持仓身份未读全") && !unknown.includes("命中我的持仓 0"),
  unknownExplainsSuspendedHoldingFilters: unknown.includes("已暂停持仓筛选与计数"),
  unknownDisablesBothOwnershipFilters: /<button[^>]*disabled=""[^>]*>只看我的命中<\/button>/.test(unknown)
    && /<button[^>]*disabled=""[^>]*>隐藏已持有<\/button>/.test(unknown),
  knownEmptyStillRendersZeroAndEnablesFilters: knownEmpty.includes("命中我的持仓 0")
    && !/<button[^>]*disabled=""[^>]*>只看我的命中<\/button>/.test(knownEmpty),
  unknownKeepsResearchCandidatesVisible: unknown.includes("宁德时代") && unknown.includes("招商银行"),
};
