import { formatStockFinancialValue, stockFinancialPeriod } from "./stock-financial-view";

export const stockFinancialViewContracts = {
  formatsNumericValues: formatStockFinancialValue(1234567.891) === "1,234,567.89",
  appendsPercentToGrowthMetrics: formatStockFinancialValue(-12.5, true) === "-12.5%",
  preservesSourceText: formatStockFinancialValue("不适用") === "不适用",
  treatsMissingAndNonFiniteAsUnknown:
    formatStockFinancialValue(null) === "—" && formatStockFinancialValue(Number.NaN) === "—",
  normalizesReportPeriod: stockFinancialPeriod("20260331") === "2026-03-31",
  leavesUnknownReportPeriodExplicit: stockFinancialPeriod(null) === "报告期未标注",
};
