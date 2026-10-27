import { actualFeeError, allocationError, netDifferenceText, unusedReceiptShares } from "./trade-journal";
import { operationError } from "./operation";
import type { TradeJournal, TradeReceipt, TradeAllocation } from "./api";
const sellFixture: TradeReceipt = { receipt_id: "sell-1", operation_id: "op-sell", tenant: "test", position_id: "old", code: "600000", direction: "sell", date: "2026-10-26", price: 10, shares: 300, actual_fees: null, recorded_at: "2026-10-27T10:00:00+08:00" };
const buyFixture: TradeReceipt = { ...sellFixture, receipt_id: "buy-1", operation_id: "op-buy", direction: "buy", date: "2026-10-27", price: 9, shares: 200, actual_fees: 0 };
const allocationFixture: TradeAllocation = { sell_receipt_id: "sell-1", buy_receipt_id: "buy-1", shares: 100, confirm_same_day_order: false, confirm_cross_position: false };
const journalFixture: TradeJournal = { revision: "r1", code: "600000", position: { position_id: "old", revision: "p1", shares: 900, cost: 11, sellable_shares: 700 }, receipts: [sellFixture, buyFixture], links: [{ link_id: "l1", allocations: [allocationFixture] }], summary: { sold_shares: 300, bought_back_shares: 100, unmatched_sell_shares: 200, gross_difference: 100, allocated_fees: null, net_difference: null, unmatched_sell_proceeds: 2000, allocations: [] } };
const formFixture = { direction: "buy" as const, code: "600000", price: "9", shares: "100", date: "2026-10-27" };
export const tradeJournalContracts = {
  unknownFeesStayUnknown: netDifferenceText(null, 100).includes("尚未确定"),
  confirmedZeroIsDefinite: netDifferenceText(0, 100).includes("为 0 元") && !netDifferenceText(0, 100).includes("待补"),
  unpairedSellIsNotProfit: netDifferenceText(2000, 0).includes("不计算"),
  negativeCashIsLoss: netDifferenceText(-28.25, 100).includes("少留下 28.25"),
  positiveCashIsOnlyMatched: netDifferenceText(28.25, 100).includes("已匹配部分多留下"),
  soldSharesAllocatedOnce: unusedReceiptShares(sellFixture, journalFixture) === 200,
  boughtSharesAllocatedOnce: unusedReceiptShares(buyFixture, journalFixture) === 100,
  cancelledLinksReleaseShares: unusedReceiptShares(buyFixture, { ...journalFixture, links: [{ link_id: "l1", allocations: [] }] }) === 200,
  overAllocationRejected: allocationError(sellFixture, buyFixture, { ...allocationFixture, shares: 101 }, journalFixture).includes("超过"),
  partialAllowed: allocationError(sellFixture, buyFixture, allocationFixture, journalFixture) === "",
  sameDayNeedsConfirmation: allocationError(sellFixture, { ...buyFixture, date: sellFixture.date }, allocationFixture, journalFixture).includes("先卖"),
  sameDayConfirmedAllowed: allocationError(sellFixture, { ...buyFixture, date: sellFixture.date }, { ...allocationFixture, confirm_same_day_order: true }, journalFixture) === "",
  earlierBuyRejected: allocationError(sellFixture, { ...buyFixture, date: "2026-10-25" }, allocationFixture, journalFixture).includes("不能早于"),
  crossLifecycleNeedsConfirmation: allocationError(sellFixture, { ...buyFixture, position_id: "new" }, allocationFixture, journalFixture).includes("不同仓位"),
  crossLifecycleConfirmed: allocationError(sellFixture, { ...buyFixture, position_id: "new" }, { ...allocationFixture, confirm_cross_position: true }, journalFixture) === "",
  missingReceiptRejected: allocationError(undefined, buyFixture, allocationFixture, journalFixture).includes("明确选择"),
  nonFiniteQuantityRejected: allocationError(sellFixture, buyFixture, { ...allocationFixture, shares: NaN }, journalFixture).includes("大于 0"),
  unknownFeeAllowed: actualFeeError("") === "" && operationError({ ...formFixture, actualFees: "" }, "2026-10-27") === "",
  verifiedFreeAllowed: actualFeeError("0") === "" && operationError({ ...formFixture, actualFees: "0" }, "2026-10-27") === "",
  negativeFeeRejected: actualFeeError("-1") !== "" && operationError({ ...formFixture, actualFees: "-1" }, "2026-10-27").includes("实际费用"),
  infiniteFeeRejected: actualFeeError("Infinity") !== "",
};
