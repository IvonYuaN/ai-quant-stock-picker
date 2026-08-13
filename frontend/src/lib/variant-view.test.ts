import type { AqspVariant } from "@/types/aqsp";
import {
  variantActionText,
  variantHoldingChangeText,
  variantHoldingName,
  variantHoldingsLabel,
  variantMoney,
  variantPercent,
  variantStrategyText,
  variantTechnicalEvidenceText,
} from "./variant-view";

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
  strategy: '{"id":"trend_follow","mode":"momentum","lookback_days":20,"hypothesis":"趋势延续优先"}',
  holdings_date: "2026-07-01",
  holdings: [
    {
      symbol: "600001",
      name: "旧名称",
      display_name: "示例股份",
      quantity: 1000,
      average_price: 10,
      last_price: 10.5,
      market_value: 10500,
      unrealized_pnl: 500,
    },
  ],
  previous_holdings_date: "2026-06-30",
  previous_holdings: [],
  adjustments: ["买入 600001 示例股份：MACD 柱转强，量比确认。"],
  recent_actions: [
    {
      date: "2026-07-01",
      action: "买入",
      symbol: "600001",
      name: "示例股份",
      quantity: 1000,
      reason: "MACD 柱转强，量比确认。",
    },
  ],
  technical_evidence: [
    {
      symbol: "600001",
      name: "示例股份",
      signal_date: "2026-06-30",
      execution_date: "2026-07-01",
      macd_hist: 0.12,
      kdj_j: 55,
      volume_ratio: 1.35,
      atr_pct: 2.4,
      evidence_kind: "current_holding_snapshot",
    },
  ],
  hard_rules: ["T+1"],
} satisfies AqspVariant;

export const variantViewContractChecks = {
  accountFieldsAreRepresented: [variantFixture.cash, variantFixture.final_equity, variantFixture.total_pnl].every((value) => typeof value === "number"),
  strategyIsReadable:
    variantStrategyText(variantFixture.strategy, variantFixture.variant_id).includes("回看 20 日") &&
    variantStrategyText(variantFixture.strategy, variantFixture.variant_id).includes("假设：趋势延续优先"),
  holdingsShowCountAndPreferredName:
    variantHoldingsLabel(variantFixture.holdings) === "1 个持仓" &&
    variantHoldingName(variantFixture.holdings[0]) === "示例股份",
  emptyHoldingsAreExplicit: variantHoldingsLabel([]) === "当前无持仓",
  missingHoldingsAreExplicit: variantHoldingsLabel(undefined) === "持仓字段未提供",
  todayAndPreviousHoldingsAreRecorded:
    variantFixture.holdings_date === "2026-07-01" &&
    variantFixture.previous_holdings_date === "2026-06-30",
  adjustmentKeepsSwitchReason: variantFixture.adjustments[0].includes("MACD 柱转强"),
  holdingChangeSeparatesAddedNameAndCode:
    variantHoldingChangeText(variantFixture.holdings, variantFixture.previous_holdings)[0] ===
    "新增：示例股份（600001）",
  unchangedHoldingsAreExplicit:
    variantHoldingChangeText(variantFixture.holdings, variantFixture.holdings)[0] === "持仓未变化。",
  actionKeepsNameAndReason:
    variantActionText(variantFixture.recent_actions[0]).includes("示例股份") &&
    variantActionText(variantFixture.recent_actions[0]).includes("量比确认"),
  missingCashDoesNotBecomeZero: variantMoney(undefined) === "未提供",
  positivePnlIsSigned: variantPercent(variantFixture.return_pct) === "+1.25%",
  technicalEvidenceIsExplicit:
    variantTechnicalEvidenceText(variantFixture.technical_evidence[0]).includes("持仓当日截面") &&
    variantTechnicalEvidenceText(variantFixture.technical_evidence[0]).includes("MACD +0.12") &&
    variantTechnicalEvidenceText(variantFixture.technical_evidence[0]).includes("KDJ-J +55") &&
    variantTechnicalEvidenceText(variantFixture.technical_evidence[0]).includes("量比 +1.35") &&
    variantTechnicalEvidenceText(variantFixture.technical_evidence[0]).includes("ATR +2.4%"),
};
