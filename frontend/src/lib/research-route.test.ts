import { stockResearchHref, todayResearchHref } from "./research-route";

function rejects(callback: () => string): boolean {
  try { callback(); return false; } catch (error) { return error instanceof RangeError; }
}

export const researchRouteContract = {
  stockDatePreserved: stockResearchHref("000001", "2026-10-23") === "/stock/000001?date=2026-10-23",
  stockLiveLink: stockResearchHref("600519") === "/stock/600519",
  todayDatePreserved: todayResearchHref("2026-10-23") === "/today?date=2026-10-23",
  todayLiveLink: todayResearchHref() === "/today",
  emptyDateMeansLive: todayResearchHref("") === "/today",
  leapDateAccepted: todayResearchHref("2024-02-29").endsWith("2024-02-29"),
  shortCodeRejected: rejects(() => stockResearchHref("12345")),
  longCodeRejected: rejects(() => stockResearchHref("1234567")),
  invalidCodeRejected: rejects(() => stockResearchHref("abc123")),
  pathCodeRejected: rejects(() => stockResearchHref("../001")),
  impossibleDayRejected: rejects(() => todayResearchHref("2026-02-30")),
  nonLeapDateRejected: rejects(() => stockResearchHref("600519", "2026-02-29")),
  malformedDateRejected: rejects(() => todayResearchHref("2026-2-03")),
  invalidMonthRejected: rejects(() => todayResearchHref("2026-13-01")),
  yearZeroRejected: rejects(() => todayResearchHref("0000-01-01")),
  queryInjectionRejected: rejects(() => todayResearchHref("2026-10-23&x=1")),
};
