import type { AqspVariant } from "@/types/aqsp";
import { variantHoldingsLabel, variantMoney, variantPercent, variantStrategyText, variantTechnicalEvidenceText } from "./variant-view";

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
    },
  ],
  hard_rules: ["T+1"],
} satisfies AqspVariant;

export const variantViewContractChecks = {
  accountFieldsAreRepresented: [variantFixture.cash, variantFixture.final_equity, variantFixture.total_pnl].every((value) => typeof value === "number"),
  strategyIsReadable: variantStrategyText(variantFixture.strategy, variantFixture.variant_id).includes("回看 20 日"),
  emptyHoldingsAreExplicit: variantHoldingsLabel(variantFixture.holdings) === "当前无持仓",
  missingHoldingsAreExplicit: variantHoldingsLabel(undefined) === "持仓字段未提供",
  missingCashDoesNotBecomeZero: variantMoney(undefined) === "未提供",
  positivePnlIsSigned: variantPercent(variantFixture.return_pct) === "+1.25%",
  technicalEvidenceIsExplicit: variantTechnicalEvidenceText(variantFixture.technical_evidence[0]).includes("MACD +0.12") && variantTechnicalEvidenceText(variantFixture.technical_evidence[0]).includes("KDJ-J +55"),
};
