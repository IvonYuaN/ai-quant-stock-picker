import type { ActionWorksheet } from "./api";
import { beginnerPlan, planJourney, planSteps, worksheetExpired } from "./position-plan";
const basis: ActionWorksheet["basis"] = {
  checked_at: "2026-10-23T10:00:00+08:00", quote_as_of: "2026-10-23T10:00:00+08:00", quote_source: "合成行情", quote_issue: "", price: 9.2, cost: 12, shares: 1000, sellable_shares: 800, position_id: "test", joint_weakness: false,
  references: [{ label: "本交易日已见高点", price: 9.5, as_of: "2026-10-23T10:00:00+08:00", source: "合成行情" }, { label: "本交易日已见低点", price: 9, as_of: "2026-10-23T10:00:00+08:00", source: "合成行情" }],
};
const worksheet: ActionWorksheet = { symbol: "002129", basis, position_snapshot: {position_id:"test",cost:12,shares:1000,sellable_shares:800}, review_plan: {choice:"t_watch"}, scenario:null,scenario_issue:"" };
const at = Date.parse(basis.checked_at);
const journey = (patch: Partial<ActionWorksheet["basis"]> = {}) => planJourney("t_watch", {...worksheet,basis:{...basis,...patch}}, {sell_price:9.5,buy_price:9}, at);
export const positionPlanContract = {
  savedReductionUsesObservedLow: planJourney("reduce", worksheet, {sell_price:9.5,buy_price:0},at).label !== "先补观察价",
  waitingShowsDistance: journey().detail.includes("0.30 元"),
  aboveReferenceIsNotAReversal: journey({price:9.6}).label.includes("转弱尚未确认"),
  equalityIsNotTradeConfirmation: journey({price:9.5}).detail.includes("不能证明"),
  belowLowRequiresReview: journey({price:8.9}).attention && journey({price:8.9}).detail.includes("不继续沿用"),
  expiredJourneyHidesDistance: planJourney("t_watch", worksheet, {sell_price:9.5,buy_price:9}, at+61_000).label === "先更新有效行情",
  saleReceiptDoesNotProveTCompletion: planJourney("t_watch", {...worksheet,recorded_sale:{date:"2026-10-23",price:9.5,shares:200}}, {sell_price:9.5,buy_price:9}, at).detail.includes("未确认属于本次做 T"),
  saleReceiptMovesToFollowup: planJourney("t_watch", {...worksheet,recorded_sale:{date:"2026-10-23",price:9.5,shares:200}}, {sell_price:9.5,buy_price:9}, at).saleReview,
  replannedSnapshotCanResumeObservation: !planJourney("t_watch", {...worksheet,recorded_sale:{date:"2026-10-23",price:9.5,shares:200},review_plan:{choice:"t_watch",position_snapshot:worksheet.position_snapshot}}, {sell_price:9.5,buy_price:9}, at).saleReview,
  simultaneousWeaknessOverridesPriceArrival: journey({joint_weakness:true,price:9.6}).label === "先暂停做 T",

  simpleTContainsBothLegs: beginnerPlan("t_watch", basis).next.includes("9.50") && !!beginnerPlan("t_watch", basis).then?.includes("9.00"),
  noSellablePausesSale: beginnerPlan("reduce", {...basis, sellable_shares: 0}).paused,
  weakMarketPausesT: beginnerPlan("t_watch", {...basis, joint_weakness: true}).paused,
  missingLevelsPausePlan: beginnerPlan("hold", {...basis, references: []}).paused,
  adjustedPricesReachSimplePlan: beginnerPlan("t_watch", basis, {sell_price: 9.6, buy_price: 9.1}).next.includes("9.60"),
  staleQuotePausesPlan: beginnerPlan("hold", {...basis, quote_issue: "行情过期"}).paused,
  changedPricesAndSizeReachSteps: planSteps("t_watch", basis, {sell_price: 9.6, buy_price: 9.1, quantity: 200}).join().includes("9.60") && planSteps("t_watch", basis, {sell_price: 9.6, buy_price: 9.1, quantity: 200}).join().includes("200 股"),
  differentChoicesHaveDifferentSteps: planSteps("hold", basis).join() !== planSteps("t_watch", basis).join(),
  sellAndBuyUseSourcePrices: planSteps("t_watch", basis)[0].includes("9.50") && planSteps("t_watch", basis)[1].includes("9.00"),
  conditionsAreExplicit: planSteps("t_watch", basis)[0].includes("转弱") && planSteps("t_watch", basis)[1].includes("实际卖出成交后"),
  t1RestrictionShown: planSteps("t_watch", basis).join().includes("T+1"),
  reductionNeverPromisesRecovery: planSteps("reduce", basis).join().includes("不会自动安排回补"),
  missingPricesStayMissing: planSteps("t_watch", { ...basis, references: [] }).join().includes("价位待补"),
  expiredSnapshotBlocked: worksheetExpired(basis, Date.parse("2026-10-23T10:02:00+08:00")),
  futureSnapshotBlocked: worksheetExpired(basis, Date.parse("2026-10-23T09:59:00+08:00")),
  freshSnapshotAccepted: !worksheetExpired(basis, Date.parse("2026-10-23T10:00:01+08:00")),
};
