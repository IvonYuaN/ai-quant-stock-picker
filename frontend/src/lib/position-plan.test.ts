import type { ActionWorksheet } from "./api";
import { planSteps, worksheetExpired } from "./position-plan";
const basis: ActionWorksheet["basis"] = {
  checked_at: "2026-10-23T10:00:00+08:00", quote_as_of: "2026-10-23T10:00:00+08:00", quote_source: "合成行情", quote_issue: "", price: 9.2, cost: 12, shares: 1000, sellable_shares: 800, position_id: "test", joint_weakness: false,
  references: [{ label: "本交易日已见高点", price: 9.5, as_of: "2026-10-23T10:00:00+08:00", source: "合成行情" }, { label: "本交易日已见低点", price: 9, as_of: "2026-10-23T10:00:00+08:00", source: "合成行情" }],
};
export const positionPlanContract = {
  changedPricesAndSizeReachSteps: planSteps("t_watch", basis, {sell_price: 9.6, buy_price: 9.1, quantity: 200}).join().includes("9.60") && planSteps("t_watch", basis, {sell_price: 9.6, buy_price: 9.1, quantity: 200}).join().includes("200 股"),
  differentChoicesHaveDifferentSteps: planSteps("hold", basis).join() !== planSteps("t_watch", basis).join(),
  sellAndBuyUseSourcePrices: planSteps("t_watch", basis)[0].includes("9.50") && planSteps("t_watch", basis)[1].includes("9.00"),
  conditionsAreExplicit: planSteps("t_watch", basis)[0].includes("冲高回落") && planSteps("t_watch", basis)[1].includes("已实际卖出"),
  t1RestrictionShown: planSteps("t_watch", basis).join().includes("T+1"),
  reductionNeverPromisesRecovery: planSteps("reduce", basis).join().includes("不安排自动回补"),
  missingPricesStayMissing: planSteps("t_watch", { ...basis, references: [] }).join().includes("价位待补"),
  expiredSnapshotBlocked: worksheetExpired(basis, Date.parse("2026-10-23T10:02:00+08:00")),
  futureSnapshotBlocked: worksheetExpired(basis, Date.parse("2026-10-23T09:59:00+08:00")),
  freshSnapshotAccepted: !worksheetExpired(basis, Date.parse("2026-10-23T10:00:01+08:00")),
};
