import type { AqspVariant } from "@/types/aqsp";
import { variantAdjustmentReasons, variantMoney, variantPercent, variantStrategyLogic, variantStrategyText } from "./variant-view";

const variantFixture = {
  variant_id: "trend_follow",
  label: "趋势跟随",
  initial_cash: 100000,
  cash: 42000,
  final_equity: 101250,
  total_pnl: 1250,
  return_pct: 1.25,
  filled_orders: 4,
  rejected_orders: 1,
  start_date: "2026-06-01",
  end_date: "2026-07-01",
  data_mode: "historical_raw_unadjusted",
  strategy: '{"id":"trend_follow","mode":"momentum","lookback_days":20}',
  holdings: [],
  hard_rules: ["T+1"],
} satisfies AqspVariant;

export const variantViewContractChecks = {
  accountFieldsAreRepresented: [variantFixture.cash, variantFixture.final_equity, variantFixture.total_pnl].every((value) => typeof value === "number"),
  strategyIsReadable: variantStrategyText(variantFixture.strategy, variantFixture.variant_id).includes("回看 20 日"),
  strategyIncludesHypothesis: variantStrategyText('{"id":"x","mode":"trend","hypothesis":"价格趋势延续"}', "x").includes("价格趋势延续"),
  strategyLogicExplainsTrigger: variantStrategyLogic('{"mode":"reversion","lookback_days":20,"entry_return_pct":3,"max_bias_pct":0}', "x").includes("收盘低于20日均线"),
  missingCashDoesNotBecomeZero: variantMoney(undefined) === "未提供",
  positivePnlIsSigned: variantPercent(variantFixture.return_pct) === "+1.25%",
  adjustmentReasonsExposeChanges: variantAdjustmentReasons(
    [{ symbol: "BBB", quantity: 100, average_price: 10, last_price: 10, market_value: 1000, unrealized_pnl: 0, name: "乙公司" }],
    [{ symbol: "AAA", quantity: 100, average_price: 10, last_price: 10, market_value: 1000, unrealized_pnl: 0, name: "甲公司" }],
  ).join("；") === "新增：乙公司；移出：甲公司",
};
