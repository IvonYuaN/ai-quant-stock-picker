import { formatStockResearchEvidenceValue, resolveStockResearchEvidence } from "./stock-research-analysis-view";

export const stockResearchEvidenceKeepsUnknownIdsVisible = resolveStockResearchEvidence(
  [" quote.current ", "missing", "quote.current", ""],
  { "quote.current": { title: "当前行情", value: 0 } },
).map((item) => item.id === "quote.current" && item.item?.value === 0 || item.id === "missing" && item.item === null);

export const stockResearchEvidenceDistinguishesZeroFromMissing = [
  formatStockResearchEvidenceValue(0) === "0",
  formatStockResearchEvidenceValue(null) === "没有可展示的事实值",
  formatStockResearchEvidenceValue({ source: "source-a" }) === '{"source":"source-a"}',
];
