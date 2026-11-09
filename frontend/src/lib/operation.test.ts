import { operationError, shanghaiDate, type OperationInput } from "./operation";
import { NAV_LINES, LEGACY_ROUTES } from "./ia";
import { normalizePortfolio } from "./portfolio-view";
import { normalizeAlerts } from "./alert-view";

const validFixture: OperationInput = { direction: "buy", code: "600000", price: "12.3456", shares: "100", date: "2026-10-23" };
const errorFor = (patch: Partial<OperationInput>) => operationError({ ...validFixture, ...patch }, "2026-10-23");
const portfolioFixture = normalizePortfolio({ holdings: [{ code: "600000", shares: 300, sellable_shares: 100, t1_locked_shares: 200 }] });
const alertsFixture = normalizeAlerts({ events: [], last_checked_at: "2026-10-23T14:30:00+08:00", last_check: { checked: ["600000:L1"], skipped: [{ symbol: "600000", level: "L2", reason: "日线缺失" }] } });
export const operationContract = {
  valid: errorFor({}) === "",
  sellAlsoValid: errorFor({ direction: "sell" }) === "",
  trimCode: errorFor({ code: " 600000 " }) === "",
  shortCodeRejected: errorFor({ code: "60000" }) !== "",
  nonNumericCodeRejected: errorFor({ code: "A60000" }) !== "",
  noPriceRejected: errorFor({ price: "" }) !== "",
  zeroPriceRejected: errorFor({ price: "0" }) !== "",
  nanPriceRejected: errorFor({ price: "NaN" }) !== "",
  infiniteSharesRejected: errorFor({ shares: "Infinity" }) !== "",
  negativeSharesRejected: errorFor({ shares: "-1" }) !== "",
  futureDateRejected: errorFor({ date: "2026-10-24" }) !== "",
  invalidDateRejected: errorFor({ date: "2026-02-31" }) !== "",
  shanghaiBoundary: shanghaiDate(new Date("2026-10-22T16:01:00Z")) === "2026-10-23",
  // 2026-11-09：导航从 4 项主线改为三组 14 个模块（参考 investment-news / TickFlow 的多模块视图切换器）。
  navigationModuleCount: NAV_LINES.flatMap((line) => line.items).length === 14,
  navigationOrder: NAV_LINES.flatMap((line) => line.items.map((item) => item.to)).join(",") ===
    "/today,/candidates,/screener,/factors,/backtest,/market,/data,/stream,/my/holdings,/monitor,/review,/evidence,/my/notes,/my/reports",
  navigationLabelsFollowModulePlan: NAV_LINES.flatMap((line) => line.items.map((item) => item.label)).join(",") ===
    "看板,候选,策略,因子,回测,市场环境,数据,消息,持仓,监控,复盘,研究依据,笔记,研报",
  navigationGroupsAreThree: NAV_LINES.map((line) => line.title).join(",") === "研究,决策,资料",
  navigationRoutesAreUnique: new Set(NAV_LINES.flatMap((line) => line.items.map((item) => item.to))).size === 14,
  watchlistCompatibility: LEGACY_ROUTES["/my/watchlist"] === "/my/holdings#watchlist",
  conceptsCompatibility: LEGACY_ROUTES["/concepts"] === "/stream",
  sellableSharesPreserved: portfolioFixture.holdings[0].sellable_shares === 100,
  t1SharesPreserved: portfolioFixture.holdings[0].t1_locked_shares === 200,
  checkedTimePreserved: alertsFixture.last_checked_at === "2026-10-23T14:30:00+08:00",
  incompleteCheckPreserved: alertsFixture.last_check?.skipped[0].reason === "日线缺失",
};
