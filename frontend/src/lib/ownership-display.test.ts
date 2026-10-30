import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server.edge";
import { MemoryRouter } from "react-router-dom";
import { CandidateSection } from "../components/aqsp/sections/CandidateSection";
import { buildDailyView } from "./daily-view";
import { dailyViewFixture } from "./daily-view.test";

const view = buildDailyView(dailyViewFixture);
const section = view.sections.find((row) => row.id === "candidates")!;
const render = (ownedHoldings: [] | null, url = "/today") => renderToStaticMarkup(createElement(MemoryRouter, { initialEntries: [url] },
  createElement(CandidateSection, { view, section, ownedHoldings })));
const unknown = render(null);
const knownEmpty = render([]);
const shared = render([], `/today?cmp=${view.candidates.slice(0, 2).map(row => row.symbol).join(",")}`);
const invalidCompare = render([], "/today?cmp=unknown");
export const ownershipDisplayContracts = {
  unknownDoesNotRenderZeroHoldingCount: unknown.includes("持仓身份未读全") && !unknown.includes("命中我的持仓 0"),
  unknownExplainsSuspendedHoldingFilters: unknown.includes("已暂停持仓筛选与计数"),
  unknownDisablesBothOwnershipFilters: /<button[^>]*disabled=""[^>]*>只看我的命中<\/button>/.test(unknown)
    && /<button[^>]*disabled=""[^>]*>隐藏已持有<\/button>/.test(unknown),
  knownEmptyStillRendersZeroAndEnablesFilters: knownEmpty.includes("命中我的持仓 0")
    && !/<button[^>]*disabled=""[^>]*>只看我的命中<\/button>/.test(knownEmpty),
  unknownKeepsResearchCandidatesVisible: unknown.includes("宁德时代") && unknown.includes("招商银行"),
  defaultDoesNotPreselectOrMountComparison: !knownEmpty.includes("同日候选取舍") && knownEmpty.includes("比较已选 0 只"),
  defaultShowsOneResearchDetail: (knownEmpty.match(/aq-selected-candidate/g) || []).length === 1,
  sharedComparisonReplacesSingleDetail: shared.includes("同日候选取舍") && !shared.includes("aq-selected-candidate"),
  sharedComparisonKeepsRosterAndSelection: shared.includes("候选名单 / 研究状态") && shared.includes("已选比较股票"),
  unknownComparisonFallsBackToResearch: !invalidCompare.includes("同日候选取舍") && invalidCompare.includes("aq-selected-candidate"),
  calculationIdentityFollowsMainTask: knownEmpty.indexOf("当日研究口径与冻结参数") > knownEmpty.indexOf("aq-selected-candidate"),
};
