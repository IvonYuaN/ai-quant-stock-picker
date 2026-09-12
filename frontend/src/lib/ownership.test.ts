// 所有权联动的契约断言（由 npm test 真正执行）。
import {
  EMPTY_OWNERSHIP,
  alreadyHeldCount,
  ownedKind,
  summarizeOwned,
  type OwnershipSets,
} from "./ownership";

const own: OwnershipSets = {
  holdings: new Set(["600036"]),
  watchlist: new Set(["300750"]),
};
const codes = ["600036", "300750", "601318", "000858"];

const summary = summarizeOwned(codes, own);

export const ownershipContract = {
  /* ---- 判断归属 ---- */
  holdingIsDetected: ownedKind("600036", own) === "holding",
  watchlistIsDetected: ownedKind("300750", own) === "watchlist",
  unknownIsNull: ownedKind("601318", own) === null,
  // 持仓优先：同时出现在两边时应报 holding（真金白银 > 关注）
  holdingWinsOverWatchlist:
    ownedKind("600036", { holdings: new Set(["600036"]), watchlist: new Set(["600036"]) }) === "holding",
  emptyCodeIsNull: ownedKind("", own) === null,

  /* ---- 汇总 ---- */
  summaryCountsHolding: summary.holding === 1,
  summaryCountsWatchlist: summary.watchlist === 1,
  summaryCountsFresh: summary.fresh === 2,
  summaryTotalMatchesInput: summary.holding + summary.watchlist + summary.fresh === codes.length,
  emptyOwnershipAllFresh: summarizeOwned(codes, EMPTY_OWNERSHIP).fresh === codes.length,
  emptyCodesAllZero: summarizeOwned([], own).fresh === 0,
  duplicateCodesAreCountedOnce: summarizeOwned(["600036", "600036"], own).holding === 2,

  /* ---- 已持有提醒 ---- */
  alreadyHeldDetects: alreadyHeldCount(codes, own) === 1,
  alreadyHeldZeroWhenNone: alreadyHeldCount(["601318"], own) === 0,
  alreadyHeldIgnoresWatchlist: alreadyHeldCount(["300750"], own) === 0,
};
